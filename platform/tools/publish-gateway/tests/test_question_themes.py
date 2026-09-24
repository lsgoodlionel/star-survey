"""WP-02 平台题型主题：注册表、``themeOptions`` 的 422 校验，以及展示型主题的编译产物。

主题只换展示，不改答卷列的形状，所以每一条都顺带证明 ``expected_rows`` 与结构指纹没有变。
副表主题（自增表格、热力图、循环评价、图片 PK、货架）在 test_question_side_tables.py。
"""

import json
import unittest

from pubgw.fieldmap import definition_signature
from pubgw.qtypes import expected_rows
from pubgw.questions.themes import THEMES

from .qtype_fixtures import definition_with, first_question, question
from .theme_fixtures import (
    ALL_THEMED, THEME_ROOT, codes, collapsible, compile_attributes, grouped, inline_blank, issues,
    scan, stepper, table, theme_row, view_folder,
)


class RegistryTest(unittest.TestCase):
    def test_every_registered_theme_has_a_real_theme_directory(self):
        for name in THEMES:
            with self.subTest(theme=name):
                self.assertTrue((THEME_ROOT / name).is_dir(), "{} 没有对应的题型主题目录".format(name))

    def test_every_registered_type_has_its_own_view_folder(self):
        """一个主题目录可以覆盖多个基础题型，但**每个题型都要有自己的 config.xml**：

        引擎按 ``survey/questions/answer/<基础题型目录>`` 找视图与资产
        （``QuestionTemplate::getTemplatePath``），少一个目录就是静默降级。
        """
        for name, spec in THEMES.items():
            for qtype in spec.types:
                with self.subTest(theme=name, type=qtype):
                    folder = view_folder(name, qtype)
                    self.assertTrue((folder / "config.xml").is_file(),
                                    "{} 缺少 {} 的 config.xml".format(name, qtype))

    def test_every_registered_theme_is_accepted_on_its_own_type(self):
        for sample in ALL_THEMED:
            with self.subTest(theme=sample["theme"], type=sample["type"]):
                self.assertEqual([], codes(sample))


class ThemeNameTest(unittest.TestCase):
    def test_an_unregistered_platform_theme_is_rejected(self):
        # 拼错的 mjy- 主题在目标实例上会静默降级成基础主题（ADR 0006 决定 6）。
        self.assertEqual(["E_THEME_UNKNOWN"], codes(question("S", theme="mjy-typo")))

    def test_an_engine_theme_is_still_passed_through(self):
        self.assertEqual([], codes(question("S", theme="browserdetect")))

    def test_theme_options_need_a_platform_theme(self):
        self.assertEqual(
            [("E_THEME_OPTIONS_UNSUPPORTED", "groups[0].questions[0].themeOptions")],
            issues(question("S", theme="browserdetect", themeOptions={"a": 1})),
        )

    def test_a_theme_used_on_the_wrong_type_is_rejected(self):
        self.assertEqual(
            [("E_THEME_TYPE_MISMATCH", "groups[0].questions[0].theme")],
            issues(question("S", theme="mjy-collapsible", themeOptions={"summary": "x"})),
        )

    def test_an_attribute_managed_by_a_theme_cannot_be_written_directly(self):
        payload = table()
        payload["attributes"] = {"mjy_table_columns": "[]"}
        self.assertIn("E_THEME_ATTRIBUTE_MANAGED", codes(payload))


class OptionShapeTest(unittest.TestCase):
    def test_unknown_option(self):
        self.assertEqual(
            [("E_THEME_OPTION_UNKNOWN", "groups[0].questions[0].themeOptions.colour")],
            issues(collapsible(colour="red")),
        )

    def test_missing_required_option(self):
        payload = collapsible()
        del payload["themeOptions"]["summary"]
        self.assertEqual(
            [("E_THEME_OPTION_REQUIRED", "groups[0].questions[0].themeOptions.summary")], issues(payload)
        )

    def test_wrong_value_kinds(self):
        for sample in (collapsible(collapsed="yes"), collapsible(summary=""),
                       scan(scanFormat="ocr"), stepper(rowsPerStep=0), stepper(rowsPerStep=99),
                       stepper(showProgress=1), inline_blank(blankMaxLength="30"),
                       table(structureVersion="坏值"), table(structureVersion="_x"), table(columns=[])):
            with self.subTest(sample=sample["themeOptions"]):
                self.assertEqual(["E_THEME_OPTION_VALUE"], codes(sample))

    def test_defaults_are_written_explicitly(self):
        attributes = compile_attributes(collapsible(), code="QSEC")
        self.assertEqual("1", attributes["mjy_collapse_default"])
        self.assertEqual("第二部分：家庭情况", attributes["mjy_collapse_summary"])


# ------------------------------------------------------------------ 逐主题


class CollapsibleTest(unittest.TestCase):
    """R02-43 折叠栏目：X 题不收集作答，主题只把题组文案折起来。"""

    def test_column_shape_is_unchanged(self):
        self.assertEqual((("QSEC", "", 0),), expected_rows(first_question(collapsible())))

    def test_collapsed_false_compiles_to_zero(self):
        self.assertEqual("0", compile_attributes(collapsible(collapsed=False), code="QSEC")["mjy_collapse_default"])

    def test_theme_name_reaches_the_question_row(self):
        self.assertEqual("mjy-collapsible", theme_row(collapsible(), code="QSEC"))


class ScanInputTest(unittest.TestCase):
    """R02-28 扫码录入：数据形状＝短文本，扫到的内容整串由客户端提交。"""

    def test_a_scan_question_must_bound_what_the_client_may_submit(self):
        self.assertEqual(
            [("E_THEME_OPTION_REQUIRED", "groups[0].questions[0].maxLength")], issues(scan(max_length=None))
        )

    def test_the_length_bound_becomes_a_server_side_rule(self):
        attributes = compile_attributes(scan(), code="QSCAN")
        self.assertEqual("qr", attributes["mjy_scan_format"])
        self.assertEqual("64", attributes["maximum_chars"])
        self.assertIn("strlen(html_entity_decode(QSCAN.NAOK)) <= 64", attributes["em_validation_q"])

    def test_a_scan_question_can_still_carry_a_format(self):
        payload = scan()
        payload["format"] = "cn_uscc"
        attributes = compile_attributes(payload, code="QSCAN")
        self.assertIn(" and ", attributes["em_validation_q"])


class GroupedOptionsTest(unittest.TestCase):
    """R02-04 选项分类：分组必须恰好覆盖全部选项，漏一个就是拼错了。"""

    def test_groups_are_compiled_canonically(self):
        value = compile_attributes(grouped(), code="QGRP")["mjy_option_groups"]
        self.assertEqual(
            [{"label": "水果", "codes": ["A1", "A2"]}, {"label": "蔬菜", "codes": ["A3"]}], json.loads(value)
        )
        self.assertNotIn(" ", value)

    def test_an_option_outside_the_question_is_rejected(self):
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(grouped(
            groups=[{"label": "水果", "codes": ["A1", "A2", "A3", "A9"]}])))

    def test_an_option_in_two_groups_is_rejected(self):
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(grouped(
            groups=[{"label": "甲", "codes": ["A1", "A2", "A3"]}, {"label": "乙", "codes": ["A1"]}])))

    def test_an_option_left_out_of_every_group_is_rejected(self):
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(grouped(
            groups=[{"label": "水果", "codes": ["A1", "A2"]}])))

    def test_column_shape_is_unchanged(self):
        self.assertEqual((("QGRP", "", 0),), expected_rows(first_question(grouped())))


class GroupedOptionsMultipleChoiceTest(unittest.TestCase):
    """R02-04 的多选分支（第四波遗留）：多选的「选项」是子题，分组按子题代码写。

    多选行的 checkbox value 恒为 ``Y``，代码只在字段名里，所以这一支必须连
    ``rows/*.twig`` 一起接管，把选项代码打进行标记（见 themes/question/mjy-grouped-options）。
    """

    def test_multiple_choice_is_accepted(self):
        self.assertEqual([], codes(grouped("M")))

    def test_groups_are_compiled_canonically(self):
        value = compile_attributes(grouped("M"), code="QGRP")["mjy_option_groups"]
        self.assertEqual(
            [{"label": "水果", "codes": ["A1", "A2"]}, {"label": "蔬菜", "codes": ["A3"]}], json.loads(value)
        )
        self.assertNotIn(" ", value)

    def test_the_theme_name_reaches_the_question_row(self):
        self.assertEqual("mjy-grouped-options", theme_row(grouped("M"), code="QGRP"))

    def test_a_subquestion_outside_the_question_is_rejected(self):
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(grouped(
            "M", groups=[{"label": "水果", "codes": ["A1", "A2", "A3", "A9"]}])))

    def test_a_subquestion_left_out_of_every_group_is_rejected(self):
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(grouped(
            "M", groups=[{"label": "水果", "codes": ["A1", "A2"]}])))

    def test_a_subquestion_in_two_groups_is_rejected(self):
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(grouped(
            "M", groups=[{"label": "甲", "codes": ["A1", "A2", "A3"]}, {"label": "乙", "codes": ["A2"]}])))

    def test_the_other_option_does_not_need_a_group(self):
        """「其他」不是子题，没有代码可写进分组；它留在原来的列表里，不能因此判 422。"""
        with_other = dict(grouped("M"), other=True)
        self.assertEqual([], codes(with_other))

    def test_column_shape_is_unchanged(self):
        self.assertEqual(
            (("QGRP", "A1", 0), ("QGRP", "A2", 0), ("QGRP", "A3", 0)),
            expected_rows(first_question(grouped("M"))),
        )

    def test_single_choice_still_reads_the_answer_options(self):
        """单选那一支照旧按 answers 判定：两种题型的「选项」来源不同，不能混用。"""
        self.assertEqual([], codes(grouped("L")))


class GroupedOptionsTemplateTest(unittest.TestCase):
    """多选分支的行模板接管：目录、行标记与两份资产的一致性。"""

    FOLDER = "multiplechoice"
    VIEWS = ("answer.twig", "rows/answer_row.twig", "rows/answer_row_other.twig")
    SCRIPT = "assets/scripts/mjy-grouped-options.js"

    def folder(self, name=FOLDER):
        return THEME_ROOT / "mjy-grouped-options/survey/questions/answer" / name

    def test_the_option_rows_are_taken_over(self):
        for view in self.VIEWS:
            with self.subTest(view=view):
                self.assertTrue((self.folder() / view).is_file(), "缺少 " + view)

    def test_every_option_row_carries_its_own_code(self):
        """多选行的 checkbox value 恒为 Y，代码只在字段名里——必须由行模板打上去，
        否则浏览器端按代码分组时只能靠猜。"""
        rows = (self.folder() / "rows/answer_row.twig").read_text(encoding="utf-8")
        # 行标记的取值必须来自子题代码 title，并且经过属性转义后再输出。
        self.assertRegex(rows, r"""data-mjy-code=['"]\{\{\s*title\|escape\('html_attr'\)\s*\}\}['"]""")

    def test_the_other_row_is_not_claimed_by_any_group(self):
        """「其他」行没有子题代码，必须留空标记，让脚本把它留在原列表里。"""
        other = (self.folder() / "rows/answer_row_other.twig").read_text(encoding="utf-8")
        self.assertNotIn("data-mjy-code=", other)

    def test_the_two_view_folders_ship_the_same_script(self):
        """资产按 ``<主题>/survey/questions/answer/<基础题型>/assets`` 发布，一个题型一份；
        两份必须逐字节一致，否则单选与多选的分组行为会悄悄分叉。"""
        single = (self.folder("listradio") / self.SCRIPT).read_bytes()
        multi = (self.folder() / self.SCRIPT).read_bytes()
        self.assertEqual(single, multi, "两个基础题型下的分组脚本不一致")


class MatrixStepperTest(unittest.TestCase):
    """R02-14 矩阵单题作答：逐行导航，答卷列仍然是每个行子题一列。"""

    def test_defaults(self):
        attributes = compile_attributes(stepper(), code="QSTEP")
        self.assertEqual({"mjy_stepper_rows": "1", "mjy_stepper_progress": "1"}, attributes)

    def test_column_shape_is_unchanged(self):
        self.assertEqual((("QSTEP", "R1", 0), ("QSTEP", "R2", 0)), expected_rows(first_question(stepper())))


class InlineBlankTest(unittest.TestCase):
    """R02-07 选项内嵌填空：填空落在 P 的评论列，清值策略用引擎自带的 commented_checkbox。"""

    def test_clearing_policy_uses_the_engine_attribute(self):
        self.assertEqual("checked", compile_attributes(inline_blank(), code="QBLANK")["commented_checkbox"])

    def test_every_blank_gets_a_server_side_length_rule(self):
        rule = compile_attributes(inline_blank(), code="QBLANK")["em_validation_q"]
        for column in ("QBLANK_S1comment", "QBLANK_S2comment"):
            self.assertIn("strlen(html_entity_decode({}.NAOK)) <= 30".format(column), rule)

    def test_the_other_blank_is_covered_too(self):
        payload = inline_blank()
        payload["other"] = True
        self.assertIn("QBLANK_othercomment.NAOK", compile_attributes(payload, code="QBLANK")["em_validation_q"])

    def test_column_shape_is_unchanged(self):
        self.assertEqual(
            (("QBLANK", "S1", 0), ("QBLANK", "S1comment", 0), ("QBLANK", "S2", 0), ("QBLANK", "S2comment", 0)),
            expected_rows(first_question(inline_blank())),
        )


class ShapeStabilityTest(unittest.TestCase):
    """主题只换展示：加上 themeOptions 之后结构签名逐字节不变。"""

    def test_theme_options_never_change_the_signature(self):
        for sample in ALL_THEMED:
            with self.subTest(theme=sample["theme"], type=sample["type"]):
                bare = dict(sample)
                bare.pop("themeOptions")
                bare.pop("theme")
                self.assertEqual(
                    definition_signature(definition_with(bare)),
                    definition_signature(definition_with(sample)),
                )
