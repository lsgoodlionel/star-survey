"""品牌与多语言的编译产物（契约 survey-branding-v1 §1、§2、§3）。"""

import unittest
import xml.etree.ElementTree as ElementTree

from pubgw.branding.compile import compile_branding
from pubgw.compiler import LssCompiler
from pubgw.fieldmap import fingerprint
from pubgw.model import SurveyDefinition

from .branding_fixtures import branded_definition, full_branding, sample_translations
from .fixtures import sample_definition, sample_payload


def compile_lss(definition):
    return LssCompiler().compile(definition)


def theme_rows(lss):
    root = ElementTree.fromstring(lss)
    return [
        {
            "template_name": theme.findtext("template_name") or "",
            "options": {
                option.tag: (option.text or "") for option in theme.find("config/options") or []
            },
        }
        for theme in root.findall("themes/theme")
    ]


def rows_of(lss, section):
    root = ElementTree.fromstring(lss)
    return [
        {child.tag: (child.text or "") for child in row}
        for row in root.findall("{}/rows/row".format(section))
    ]


class UnbrandedDefinitionsAreUntouchedTest(unittest.TestCase):
    """两个块都不出现时编译结果逐字节不变——这是所有既有问卷的回归防线。"""

    def test_no_themes_section_without_branding(self):
        self.assertEqual([], theme_rows(compile_lss(sample_definition()).lss))

    def test_branding_returns_none_when_absent(self):
        self.assertIsNone(compile_branding(sample_definition()))

    def test_the_document_is_byte_identical_to_the_same_definition_without_the_keys(self):
        plain = SurveyDefinition.from_dict(sample_payload())
        payload = sample_payload()
        payload["branding"] = None
        payload["translations"] = None
        with_nulls = SurveyDefinition.from_dict(payload)
        self.assertEqual(compile_lss(plain).lss, compile_lss(with_nulls).lss)

    def test_the_fingerprint_does_not_move_when_branding_is_added(self):
        plain = SurveyDefinition.from_dict(sample_payload())
        branded = branded_definition(full_branding(), sample_translations())
        self.assertEqual(
            fingerprint(compile_lss(plain).signature),
            fingerprint(compile_lss(branded).signature),
        )

    def test_the_fingerprint_does_not_move_when_only_the_theme_changes(self):
        plain = compile_lss(SurveyDefinition.from_dict(sample_payload()))
        themed = compile_lss(branded_definition(theme="zh-business", languages=()))
        self.assertEqual(plain.fingerprint, themed.fingerprint)


class BrandingCompileTest(unittest.TestCase):
    def setUp(self):
        self.compiled = compile_lss(branded_definition(full_branding()))
        self.themes = theme_rows(self.compiled.lss)

    def test_one_theme_row_naming_the_definitions_theme(self):
        self.assertEqual(1, len(self.themes))
        self.assertEqual("zh-business", self.themes[0]["template_name"])

    def test_the_colour_is_normalised_to_lower_case(self):
        self.assertEqual("#1f6feb", self.themes[0]["options"]["mjybrandprimary"])

    def test_assets_become_theme_relative_paths(self):
        options = self.themes[0]["options"]
        self.assertEqual("on", options["mjybrandlogo"])
        self.assertEqual("./files/logo.png", options["mjybrandlogofile"])
        self.assertEqual("Acme", options["mjybrandlogoalt"])
        self.assertEqual("./files/favicon.ico", options["mjybrandfavicon"])

    def test_text_maps_become_one_flat_option_per_language(self):
        options = self.themes[0]["options"]
        self.assertEqual("Acme feedback", options["mjybrandtitle"])
        self.assertEqual("Acme feedback", options["mjybrandtitle__en"])
        self.assertEqual("Acme 意见征集", options["mjybrandtitle__zh-Hans"])
        self.assertEqual("(c) Acme Ltd.", options["mjybrandfooter"])
        self.assertEqual("（c）艾克姆有限公司", options["mjybrandfooter__zh-Hans"])

    def test_a_plain_string_fills_the_default_only(self):
        compiled = compile_lss(
            branded_definition({"brandingVersion": 1, "footerText": "(c) Acme"}, languages=())
        )
        options = theme_rows(compiled.lss)[0]["options"]
        self.assertEqual("(c) Acme", options["mjybrandfooter"])
        self.assertNotIn("mjybrandfooter__en", options)

    def test_absent_fields_produce_no_option_at_all(self):
        compiled = compile_lss(
            branded_definition({"brandingVersion": 1, "primaryColor": "#abc"}, languages=())
        )
        options = theme_rows(compiled.lss)[0]["options"]
        self.assertEqual(["mjybrandprimary"], sorted(options))

    def test_the_survey_row_still_names_the_theme(self):
        row = rows_of(self.compiled.lss, "surveys")[0]
        self.assertEqual("zh-business", row["template"])

    def test_option_values_are_wrapped_in_cdata_so_markup_cannot_escape(self):
        compiled = compile_lss(
            branded_definition({"brandingVersion": 1, "footerText": "</options><x>&amp;"}, languages=())
        )
        options = theme_rows(compiled.lss)[0]["options"]
        self.assertEqual("</options><x>&amp;", options["mjybrandfooter"])


class TranslationsCompileTest(unittest.TestCase):
    def setUp(self):
        self.lss = compile_lss(branded_definition(translations=sample_translations())).lss

    def test_every_language_gets_its_own_language_settings_row(self):
        rows = {row["surveyls_language"]: row for row in rows_of(self.lss, "surveys_languagesettings")}
        self.assertEqual({"en", "zh-Hans"}, set(rows))
        self.assertEqual("P0 发布网关样例", rows["en"]["surveyls_title"])
        self.assertEqual("网关样例", rows["zh-Hans"]["surveyls_title"])
        self.assertEqual("大约两分钟", rows["zh-Hans"]["surveyls_description"])

    def test_question_text_is_translated_and_falls_back_otherwise(self):
        rows = rows_of(self.lss, "question_l10ns")
        by_language = {}
        for row in rows:
            by_language.setdefault(row["language"], []).append(row)
        self.assertEqual({"en", "zh-Hans"}, set(by_language))
        english = {row["qid"]: row for row in by_language["en"]}
        chinese = {row["qid"]: row for row in by_language["zh-Hans"]}
        self.assertEqual(sorted(english), sorted(chinese))
        translated = [row for row in by_language["zh-Hans"] if row["question"] == "选一个（中文）"]
        self.assertEqual(1, len(translated))
        self.assertEqual("只能选一项", translated[0]["help"])
        # 未翻译的题目按基础语言回退，不留空白
        fallback = {row["qid"]: row["question"] for row in by_language["zh-Hans"]}
        self.assertIn("写点什么", fallback.values())

    def test_subquestion_text_is_translated(self):
        chinese = [row for row in rows_of(self.lss, "question_l10ns") if row["language"] == "zh-Hans"]
        self.assertIn("甲（中文）", [row["question"] for row in chinese])

    def test_answer_text_is_translated_per_language(self):
        rows = rows_of(self.lss, "answer_l10ns")
        chinese = [row for row in rows if row["language"] == "zh-Hans"]
        english = [row for row in rows if row["language"] == "en"]
        self.assertEqual(len(chinese), len(english))
        self.assertIn("甲（中文）", [row["answer"] for row in chinese])
        self.assertIn("乙（中文）", [row["answer"] for row in chinese])
        # 同一个 aid 在两种语言里各有一行
        self.assertEqual(
            sorted(row["aid"] for row in chinese), sorted(row["aid"] for row in english)
        )

    def test_group_titles_are_translated(self):
        chinese = [row for row in rows_of(self.lss, "group_l10ns") if row["language"] == "zh-Hans"]
        titles = {row["group_name"] for row in chinese}
        self.assertIn("基本情况", titles)
        self.assertIn("评价", titles)  # 未翻译的题组回退

    def test_l10n_row_ids_stay_unique_across_languages(self):
        for section in ("group_l10ns", "question_l10ns", "answer_l10ns"):
            with self.subTest(section=section):
                ids = [row["id"] for row in rows_of(self.lss, section)]
                self.assertEqual(len(ids), len(set(ids)))

    def test_additional_languages_without_translations_still_get_full_rows(self):
        lss = compile_lss(branded_definition(languages=("zh-Hans",))).lss
        chinese = [row for row in rows_of(lss, "question_l10ns") if row["language"] == "zh-Hans"]
        english = [row for row in rows_of(lss, "question_l10ns") if row["language"] == "en"]
        self.assertEqual(len(english), len(chinese))
        self.assertTrue(chinese)


if __name__ == "__main__":
    unittest.main()
