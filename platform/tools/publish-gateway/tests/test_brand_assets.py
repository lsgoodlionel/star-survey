"""Brand asset installation is closed-world and happens only after import."""

import base64
import hashlib
import json
import unittest
from urllib.parse import parse_qs, urlsplit

from pubgw.brand_assets import BrandAssetError, BrandAssetInstaller, validate_brand_assets
from pubgw.channel import canonical_query, sign
from pubgw.compiler import LssCompiler
from pubgw.model import SurveyDefinition

from .branding_fixtures import branded_payload


ASSET_ID = "123e4567-e89b-42d3-a456-426614174000"
CONTENT = b"brand-image"


def asset(slot="logoAsset", content_type="image/png", content=CONTENT):
    filename = "mjy-brand-logo.png" if slot == "logoAsset" else "mjy-brand-favicon.png"
    return {
        "slot": slot,
        "fileName": filename,
        "contentType": content_type,
        "sizeBytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "contentBase64": base64.b64encode(content).decode("ascii"),
    }


def branded_definition(materialized=None):
    materialized = materialized or {
        "url": "https://survey.example/a/tenant/{}/7?exp=1800003600&sig=signed".format(ASSET_ID),
        "assetVersion": 7,
        "contentType": "image/png",
        "sizeBytes": len(CONTENT),
        "fileName": "mjy-brand-logo.png",
        "sha256": hashlib.sha256(CONTENT).hexdigest(),
    }
    return SurveyDefinition.from_dict(branded_payload({"brandingVersion": 1, "logoAsset": materialized}))


class BrandAssetCorrespondenceTest(unittest.TestCase):
    def test_asset_bundle_defers_brand_options_until_signed_install(self):
        payload = branded_payload({
            "brandingVersion": 1,
            "primaryColor": "#1f6feb",
            "pageTitle": "Pinned brand",
            "logoAsset": branded_definition().branding["logoAsset"],
        })
        definition = SurveyDefinition.from_dict(payload)

        compiled = LssCompiler().compile(definition, defer_branding=True)

        self.assertNotIn("mjybrandprimary", compiled.lss)
        self.assertNotIn("mjybrandtitle", compiled.lss)
        self.assertEqual("#1f6feb", compiled.branding.options["mjybrandprimary"])
        self.assertEqual("Pinned brand", compiled.branding.engine_branding["pageTitle"])

    def test_accepts_bytes_that_match_the_materialized_logo(self):
        assets = validate_brand_assets(branded_definition(), (asset(),))

        self.assertEqual((asset(),), assets)

    def test_rejects_a_bundle_without_a_materialized_definition_match(self):
        with self.assertRaises(BrandAssetError):
            validate_brand_assets(branded_definition(), (asset(slot="faviconAsset"),))

    def test_rejects_a_materialized_asset_missing_from_the_bundle(self):
        with self.assertRaises(BrandAssetError):
            validate_brand_assets(branded_definition(), ())

    def test_rejects_same_size_and_mime_bytes_with_a_different_digest(self):
        swapped = asset(content=b"other-bytes")

        with self.assertRaises(BrandAssetError):
            validate_brand_assets(branded_definition(), (swapped,))


class BrandAssetInstallerTest(unittest.TestCase):
    def test_posts_a_digest_bound_signed_payload_without_platform_asset_metadata(self):
        captured = []

        def post(request, timeout):
            captured.append((request, timeout))
            return b'{"status":"ok"}'

        secret = "a" * 64
        installer = BrandAssetInstaller(
            "https://engine.example/index.php", "engine-01", secret, post=post, now=lambda: 1800000000
        )
        installer.install(42, {"brandingVersion": 1, "logoFile": "mjy-brand-logo.png"}, (asset(),))

        request, timeout = captured[0]
        split = urlsplit(request.full_url)
        query = {key: values[0] for key, values in parse_qs(split.query).items()}
        signature = query.pop("sig")
        self.assertEqual(30, timeout)
        self.assertEqual("POST", request.get_method())
        self.assertEqual(
            "/index.php/plugins/direct/plugin/MjyQuestionExtensions/function/brandAssetsV1",
            split.path,
        )
        self.assertEqual("MjyQuestionExtensions", query["plugin"])
        self.assertEqual("brandAssetsV1", query["function"])
        self.assertEqual(hashlib.sha256(request.data).hexdigest(), query["bodySha256"])
        self.assertEqual(str(len(request.data)), query["bodySize"])
        self.assertEqual(signature, sign(secret, query["ts"], canonical_query(query)))
        payload = json.loads(request.data)
        self.assertNotIn("url", payload["assets"][0])
        self.assertNotIn("assetVersion", payload["assets"][0])
