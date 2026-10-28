"""`branding` 块的校验（契约 survey-branding-v1 §2）：每一条都必须是 422，不能静默忽略。"""

import unittest

from pubgw.validate import validate_definition

from .branding_fixtures import branded_definition, full_branding


MATERIALIZED_SHA256 = "a" * 64


def codes(definition):
    return sorted({issue.code for issue in validate_definition(definition).issues})


def brand(**overrides):
    block = full_branding()
    for name, value in overrides.items():
        if value is _DROP:
            block.pop(name, None)
            continue
        block[name] = value
    return branded_definition(block)


class _Drop:
    pass


_DROP = _Drop()


class AcceptedBrandingTest(unittest.TestCase):
    def test_the_full_branding_block_passes(self):
        self.assertEqual([], codes(brand()))

    def test_branding_may_be_omitted_entirely(self):
        self.assertEqual([], codes(branded_definition(theme="fruity_twentythree")))

    def test_plain_string_text_is_accepted(self):
        self.assertEqual([], codes(brand(pageTitle="Acme", footerText="(c) Acme")))

    def test_only_a_colour_is_enough(self):
        definition = branded_definition({"brandingVersion": 1, "primaryColor": "#abc"})
        self.assertEqual([], codes(definition))

    def test_author_asset_references_are_accepted(self):
        definition = branded_definition(
            {"brandingVersion": 1, "logoAsset": {"assetId": "asset-logo"},
             "faviconAsset": {"assetId": "asset-icon"}}
        )
        self.assertEqual([], codes(definition))

    def test_materialized_asset_references_are_accepted(self):
        definition = branded_definition(
            {
                "brandingVersion": 1,
                "logoAsset": {
                    "url": "https://survey.lsgood.cn/a/tenant/11111111-1111-4111-8111-111111111111/1?exp=123&sig=abc",
                    "assetVersion": 1,
                    "contentType": "image/png",
                    "sizeBytes": 128,
                    "fileName": "mjy-brand-logo.png",
                    "sha256": MATERIALIZED_SHA256,
                },
            }
        )
        self.assertEqual([], codes(definition))

    def test_materialized_asset_accepts_isolated_loopback_http(self):
        definition = branded_definition({
            "brandingVersion": 1,
            "logoAsset": {
                "url": "http://127.0.0.1:49123/a/tenant/11111111-1111-4111-8111-111111111111/1?exp=123&sig=abc",
                "assetVersion": 1,
                "contentType": "image/png",
                "sizeBytes": 128,
                "fileName": "mjy-brand-logo.png",
                "sha256": MATERIALIZED_SHA256,
            },
        })
        self.assertEqual([], codes(definition))


class RejectedBrandingTest(unittest.TestCase):
    def test_branding_must_be_an_object(self):
        self.assertIn("E_BRAND_INVALID", codes(branded_definition(branding=[])))

    def test_the_version_is_required_and_must_be_one(self):
        self.assertIn("E_BRAND_VERSION", codes(brand(brandingVersion=2)))
        self.assertIn("E_BRAND_VERSION", codes(brand(brandingVersion=_DROP)))

    def test_an_unknown_key_is_rejected_rather_than_ignored(self):
        self.assertIn("E_BRAND_UNKNOWN_KEY", codes(brand(headHtml="<script>x</script>")))

    def test_custom_css_has_its_own_code_so_the_author_learns_why(self):
        report = validate_definition(brand(customCss="body{color:red}"))
        issue = next(i for i in report.issues if i.code == "E_BRAND_CUSTOM_CSS_UNSUPPORTED")
        self.assertEqual("branding.customCss", issue.path)

    def test_custom_js_is_an_unknown_key_not_a_supported_one(self):
        self.assertIn("E_BRAND_UNKNOWN_KEY", codes(brand(customJs="alert(1)")))

    def test_a_colour_must_be_a_hex_triplet(self):
        for value in ("red", "rgb(1,2,3)", "#12345", "#ggghhh", "#1F6FEB;", "var(--x)"):
            with self.subTest(value=value):
                self.assertIn("E_BRAND_COLOR", codes(brand(primaryColor=value)))

    def test_a_colour_must_be_a_string(self):
        self.assertIn("E_BRAND_TYPE", codes(brand(primaryColor=255)))

    def test_an_asset_may_not_leave_the_theme_directory(self):
        for value in (
            "../../etc/passwd",
            "files/logo.png",
            "/logo.png",
            "http://cdn.example.com/logo.png",
            "//cdn.example.com/logo.png",
            "data:image/png;base64,AAAA",
            "logo.png?x=1",
            "",
        ):
            with self.subTest(value=value):
                self.assertIn("E_BRAND_ASSET", codes(brand(logoFile=value)))

    def test_an_asset_must_look_like_an_image(self):
        self.assertIn("E_BRAND_ASSET", codes(brand(logoFile="logo.svgz")))
        self.assertIn("E_BRAND_ASSET", codes(brand(logoFile="logo")))

    def test_a_favicon_may_be_an_ico(self):
        self.assertEqual([], codes(brand(faviconFile="favicon.ico")))

    def test_a_logo_may_not_be_an_ico(self):
        self.assertIn("E_BRAND_ASSET", codes(brand(logoFile="logo.ico")))

    def test_author_asset_reference_rejects_url_version_and_unknown_fields(self):
        for reference in (
            {"assetId": "asset-logo", "url": "https://example.test/logo.png"},
            {"assetId": "asset-logo", "assetVersion": 2},
            {"assetId": "asset-logo", "filename": "../logo.png"},
            {"assetId": "../logo"},
            {"assetId": ""},
            {},
        ):
            with self.subTest(reference=reference):
                self.assertIn("E_BRAND_ASSET", codes(brand(logoAsset=reference)))

    def test_author_asset_reference_rejects_mime_and_size_fields(self):
        for reference in (
            {"assetId": "logo-1", "mimeType": "image/png"},
            {"assetId": "logo-1", "sizeBytes": 10},
        ):
            with self.subTest(reference=reference):
                self.assertIn("E_BRAND_ASSET", codes(brand(logoAsset=reference)))

    def test_materialized_asset_rejects_external_url_path_or_wrong_filename(self):
        valid = {
            "url": "https://survey.example/a/tenant/11111111-1111-4111-8111-111111111111/1?exp=123&sig=abc",
            "assetVersion": 1,
            "contentType": "image/png",
            "sizeBytes": 128,
            "fileName": "mjy-brand-logo.png",
            "sha256": MATERIALIZED_SHA256,
        }
        for key, value in (
            ("url", "https://evil.example/logo.png"),
            ("url", "https://survey.example/../evil"),
            ("fileName", "../../logo.png"),
            ("fileName", "mjy-brand-logo-11111111-1111-4111-8111-111111111111.png"),
            ("contentType", "image/svg+xml"),
            ("sizeBytes", 5_242_881),
            ("assetVersion", 0),
        ):
            with self.subTest(key=key, value=value):
                reference = dict(valid)
                reference[key] = value
                self.assertIn("E_BRAND_ASSET", codes(branded_definition({"brandingVersion": 1, "logoAsset": reference})))

    def test_materialized_asset_requires_a_digest_and_url_version_must_match_metadata(self):
        valid = {
            "url": "https://survey.lsgood.cn/a/tenant/11111111-1111-4111-8111-111111111111/1?exp=123&sig=abc",
            "assetVersion": 1,
            "contentType": "image/png",
            "sizeBytes": 128,
            "fileName": "mjy-brand-logo.png",
            "sha256": MATERIALIZED_SHA256,
        }
        missing_digest = dict(valid)
        missing_digest.pop("sha256")
        mismatched_version = dict(valid, url=valid["url"].replace("/1?", "/2?"))

        self.assertIn("E_BRAND_ASSET", codes(branded_definition({
            "brandingVersion": 1, "logoAsset": missing_digest,
        })))
        self.assertIn("E_BRAND_ASSET", codes(branded_definition({
            "brandingVersion": 1, "logoAsset": mismatched_version,
        })))

    def test_materialized_asset_rejects_noncanonical_or_credentialed_hosts(self):
        valid = {
            "url": "https://survey.lsgood.cn/a/tenant/11111111-1111-4111-8111-111111111111/1?exp=123&sig=abc",
            "assetVersion": 1,
            "contentType": "image/png",
            "sizeBytes": 128,
            "fileName": "mjy-brand-logo.png",
            "sha256": MATERIALIZED_SHA256,
        }
        for url in (
            "https://survey.lsgood.cn@evil.example/a/tenant/11111111-1111-4111-8111-111111111111/1?exp=123&sig=abc",
            "https://survey.lsgood.cn:443/a/tenant/11111111-1111-4111-8111-111111111111/1?exp=123&sig=abc",
            "https://Survey.lsgood.cn/a/tenant/11111111-1111-4111-8111-111111111111/1?exp=123&sig=abc",
        ):
            with self.subTest(url=url):
                self.assertIn("E_BRAND_ASSET", codes(branded_definition({
                    "brandingVersion": 1, "logoAsset": dict(valid, url=url),
                })))

    def test_materialized_asset_rejects_author_id_and_unknown_fields(self):
        reference = {
            "assetId": "11111111-1111-4111-8111-111111111111",
            "url": "https://survey.example/a/tenant/11111111-1111-4111-8111-111111111111/1?exp=123&sig=abc",
            "assetVersion": 1,
            "contentType": "image/png",
            "sizeBytes": 128,
            "fileName": "mjy-brand-logo.png",
            "unexpected": True,
        }
        self.assertIn("E_BRAND_ASSET", codes(branded_definition({"brandingVersion": 1, "logoAsset": reference})))

    def test_author_asset_filename_is_fixed_and_slot_specific(self):
        logo_definition = branded_definition(
            {
                "brandingVersion": 1,
                "logoAsset": {"assetId": "abc-123"},
            }
        )
        self.assertEqual([], codes(logo_definition))
        favicon_definition = branded_definition(
            {
                "brandingVersion": 1,
                "faviconAsset": {"assetId": "abc-123", "assetVersion": 2},
            }
        )
        self.assertIn("E_BRAND_ASSET", codes(favicon_definition))

    def test_legacy_filename_and_author_reference_cannot_share_a_slot(self):
        self.assertIn("E_BRAND_ASSET", codes(brand(logoAsset={"assetId": "abc"}, logoFile="logo.png")))
        self.assertIn(
            "E_BRAND_ASSET", codes(brand(faviconAsset={"assetId": "abc"}, faviconFile="favicon.ico"))
        )

    def test_a_text_map_may_only_use_the_definitions_languages(self):
        self.assertIn("E_BRAND_LANGUAGE", codes(brand(footerText={"fr": "bonjour"})))

    def test_a_text_map_value_must_be_a_string(self):
        self.assertIn("E_BRAND_TYPE", codes(brand(footerText={"en": 1})))

    def test_branding_requires_the_branding_theme(self):
        definition = branded_definition(full_branding(), theme="fruity_twentythree")
        self.assertIn("E_BRAND_THEME", codes(definition))

    def test_every_problem_is_reported_not_just_the_first(self):
        found = codes(brand(primaryColor="red", logoFile="/x.png", customCss="a{}"))
        self.assertEqual(
            ["E_BRAND_ASSET", "E_BRAND_COLOR", "E_BRAND_CUSTOM_CSS_UNSUPPORTED"], found
        )


if __name__ == "__main__":
    unittest.main()
