"""`branding` 块的校验（契约 survey-branding-v1 §2）：每一条都必须是 422，不能静默忽略。"""

import unittest

from pubgw.validate import validate_definition

from .branding_fixtures import branded_definition, full_branding


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
