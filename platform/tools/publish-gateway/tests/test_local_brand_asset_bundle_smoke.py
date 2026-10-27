"""LOCAL TEST ONLY: exercise temporary image bundle bytes and theme-file references."""

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pubgw.branding.compile import compile_branding

from .branding_fixtures import branded_definition
from .fixtures import sample_definition


class LocalBrandAssetBundleSmokeTest(unittest.TestCase):
    def test_deterministic_images_match_manifest_lss_and_written_theme_files(self):
        from tools.local_brand_asset_bundle_smoke import run_smoke

        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_smoke(Path(temp_dir) / "bundle", Path(temp_dir) / "theme")

            self.assertEqual("local-test-only", result["scope"])
            manifest = json.loads(Path(result["manifestPath"]).read_text(encoding="utf-8"))
            self.assertEqual({"logo", "favicon"}, {asset["slot"] for asset in manifest["assets"]})
            self.assertEqual(
                {"logo": "11111111-1111-4111-8111-111111111111", "favicon": "22222222-2222-4222-8222-222222222222"},
                {asset["slot"]: asset["assetId"] for asset in manifest["assets"]},
            )
            by_slot = {asset["slot"]: asset for asset in manifest["assets"]}
            self.assertEqual(by_slot["logo"]["sha256"], result["branding"]["logoAsset"]["sha256"])
            self.assertEqual(by_slot["favicon"]["sha256"], result["branding"]["faviconAsset"]["sha256"])

            options = compile_branding(branded_definition(result["branding"])).options
            self.assertNotIn("mjybrandlogofile", options)
            self.assertNotIn("mjybrandfavicon", options)

            for asset in manifest["assets"]:
                data = base64.b64decode(asset["bytesBase64"], validate=True)
                self.assertEqual(len(data), asset["sizeBytes"])
                self.assertEqual(hashlib.sha256(data).hexdigest(), asset["sha256"])
                self.assertEqual("image/png" if asset["slot"] == "logo" else "image/jpeg", asset["contentType"])
                self.assertEqual(Path(asset["fileName"]).name, asset["fileName"])
                output = Path(result["themeDir"]) / "files" / asset["fileName"]
                self.assertTrue(output.is_file())
                self.assertEqual(data, output.read_bytes())

    def test_smoke_is_explicitly_separate_from_gateway_request_byte_transport(self):
        from pubgw.request import PublishRequest

        self.assertIn("brand_assets", PublishRequest.__dataclass_fields__)
        self.assertIsNotNone(sample_definition())


if __name__ == "__main__":
    unittest.main()
