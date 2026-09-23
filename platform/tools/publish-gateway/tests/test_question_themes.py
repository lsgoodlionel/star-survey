"""WP-02 切片 02.3：平台题型主题（themeOptions）的 422 校验与编译产物。

主题只换展示，不改答卷列的形状，所以每一条都顺带证明 ``expected_rows`` 与结构指纹没有变。
副表主题（自增表格、热力图）另外证明绑定记录里的副表声明与结构摘要。
"""

import json
import unittest
import xml.etree.ElementTree as ElementTree

from pubgw.compiler import LssCompiler
from pubgw.fieldmap import binding_map, definition_signature, parse_fieldmap
from pubgw.qtypes import expected_rows
from pubgw.questions.themes import THEMES, structure_digest
from pubgw.validate import validate_definition

from .fixtures import fieldmap_for
from .qtype_fixtures import answer, definition_with, first_question, question, sub

TABLE_COLUMNS = [
    {"code": "item", "label": "物品", "type": "text", "required": True, "maxLength": 40},
    {"code": "qty", "label": "数量", "type": "integer", "required": True, "min": 1, "max": 99},
]


def issues(*questions, version=1):
    report = validate_definition(definition_with(*questions, version=version))
    return [(issue.code, issue.path) for issue in report.issues]


def codes(*questions, version=1):
    return [code for code, _ in issues(*questions, version=version)]


def compile_attributes(*questions, code="Q1"):
    tree = ElementTree.fromstring(LssCompiler().compile(definition_with(*questions)).lss)
    rows = [{child.tag: (child.text or "") for child in row}
            for row in tree.findall("question_attributes/rows/row")]
    qids = {row.find("title").text: row.find("qid").text
            for row in tree.findall("questions/rows/row")}
    return {row["attribute"]: row["value"] for row in rows if row["qid"] == qids[code]}


def theme_row(*questions, code="Q1"):
    tree = ElementTree.fromstring(LssCompiler().compile(definition_with(*questions)).lss)
    for row in tree.findall("questions/rows/row"):
        if row.find("title").text == code:
            node = row.find("question_theme_name")
            return "" if node is None or node.text is None else node.text
    raise AssertionError("no question row for " + code)


# ------------------------------------------------------------------ 各主题的样例题


def collapsible(**options):
    payload = {"summary": "第二部分：家庭情况"}
    payload.update(options)
    return question("X", code="QSEC", theme="mjy-collapsible", themeOptions=payload)


def scan(max_length=64, **options):
    payload = dict(options)
    extra = {"theme": "mjy-scan-input", "themeOptions": payload}
    if max_length is not None:
        extra["maxLength"] = max_length
    return question("S", code="QSCAN", **extra)


def grouped(qtype="L", **options):
    payload = {"groups": [{"label": "水果", "codes": ["A1", "A2"]}, {"label": "蔬菜", "codes": ["A3"]}]}
    payload.update(options)
    extra = {"theme": "mjy-grouped-options", "themeOptions": payload}
    if qtype == "L":
        extra["answers"] = [answer("A1"), answer("A2"), answer("A3")]
    else:
        extra["subquestions"] = [sub("A1"), sub("A2"), sub("A3")]
    return question(qtype, code="QGRP", **extra)


def stepper(**options):
    return question("F", code="QSTEP", theme="mjy-matrix-stepper", themeOptions=dict(options),
                    answers=[answer("A1"), answer("A2")], subquestions=[sub("R1"), sub("R2")])


def inline_blank(**options):
    payload = {"blankMaxLength": 30}
    payload.update(options)
    return question("P", code="QBLANK", theme="mjy-inline-blank", themeOptions=payload,
                    subquestions=[sub("S1"), sub("S2")])


def table(**options):
    payload = {"structureVersion": "rt1", "columns": TABLE_COLUMNS, "minRows": 1, "maxRows": 5}
    payload.update(options)
    return question("T", code="QTABLE", theme="mjy-repeating-table", themeOptions=payload)


def heatmap(**options):
    payload = {"structureVersion": "hm1", "image": "https://assets.example.invalid/store.png", "maxPoints": 3}
    payload.update(options)
    return question("T", code="QHEAT", theme="mjy-heatmap", themeOptions=payload)


ALL_THEMED = (collapsible(), scan(), grouped(), stepper(), inline_blank(), table(), heatmap())


# ------------------------------------------------------------------ 注册表


class RegistryTest(unittest.TestCase):
    def test_every_registered_theme_has_a_real_theme_directory(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[4] / "themes/question"
        for name in THEMES:
            with self.subTest(theme=name):
                self.assertTrue((root / name).is_dir(), "{} 没有对应的题型主题目录".format(name))

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

    def test_multiple_choice_is_deliberately_out_of_this_batch(self):
        # 多选的选项行走 rows/*.twig，换主题要连行模板一起接管（02.4）。
        self.assertEqual(["E_THEME_TYPE_MISMATCH"], codes(grouped("M")))

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


# ------------------------------------------------------------------ 副表主题


class RepeatingTableTest(unittest.TestCase):
    """R02-13 自增表格：整张表一列 JSON，列定义随 .lss 发布，插件按它投影副表。"""

    def test_columns_and_bounds_compile_to_the_plugin_attributes(self):
        attributes = compile_attributes(table(), code="QTABLE")
        self.assertEqual("rt1", attributes["mjy_structure_version"])
        self.assertEqual("1", attributes["mjy_table_min_rows"])
        self.assertEqual("5", attributes["mjy_table_max_rows"])
        self.assertEqual(TABLE_COLUMNS, json.loads(attributes["mjy_table_columns"]))

    def test_a_column_label_defaults_to_its_code(self):
        value = compile_attributes(table(columns=[{"code": "item"}]), code="QTABLE")["mjy_table_columns"]
        self.assertEqual([{"code": "item", "label": "item", "type": "text", "required": False}], json.loads(value))

    def test_bad_column_definitions(self):
        for columns in ([{"code": "1bad"}],
                        [{"code": "item", "type": "money"}],
                        [{"code": "item"}, {"code": "item"}],
                        [{"code": "item", "maxLength": 0}],
                        [{"code": "item", "min": 1}],
                        [{"code": "qty", "type": "integer", "min": 9, "max": 1}],
                        ["not an object"]):
            with self.subTest(columns=columns):
                self.assertEqual(["E_THEME_OPTION_VALUE"], codes(table(columns=columns)))

    def test_too_many_columns(self):
        many = [{"code": "c{}".format(index)} for index in range(41)]
        self.assertIn("E_THEME_OPTION_VALUE", codes(table(columns=many)))

    def test_row_bounds_must_be_ordered_and_within_the_plugin_hard_limit(self):
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(table(minRows=5, maxRows=2)))
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(table(maxRows=501)))

    def test_the_answer_is_still_one_engine_column(self):
        self.assertEqual((("QTABLE", "", 0),), expected_rows(first_question(table())))


class HeatmapTest(unittest.TestCase):
    """R02-19 热力图选区：归一化坐标，范围由平台写死，插件逐格校验。"""

    def test_generated_columns_pin_the_coordinate_range(self):
        columns = json.loads(compile_attributes(heatmap(), code="QHEAT")["mjy_table_columns"])
        self.assertEqual(["x", "y"], [column["code"] for column in columns])
        for column in columns:
            self.assertEqual(("decimal", True, 0, 1), (column["type"], column["required"], column["min"], column["max"]))

    def test_point_bounds_reach_the_plugin_row_bounds(self):
        attributes = compile_attributes(heatmap(minPoints=1), code="QHEAT")
        self.assertEqual(("1", "3"), (attributes["mjy_table_min_rows"], attributes["mjy_table_max_rows"]))

    def test_unordered_point_bounds_are_rejected(self):
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(heatmap(minPoints=4, maxPoints=2)))

    def test_the_image_is_required(self):
        payload = heatmap()
        del payload["themeOptions"]["image"]
        self.assertEqual(["E_THEME_OPTION_REQUIRED"], codes(payload))


class SideTableBindingTest(unittest.TestCase):
    """绑定记录里的副表声明：读端据此解释单元格（contracts/question-extension-tables-v1.md）。"""

    def binding(self, *questions, code):
        definition = definition_with(*questions)
        rows = parse_fieldmap(fieldmap_for(definition))
        return {item.code: item for item in binding_map(definition, rows)}[code]

    def test_a_plain_question_declares_no_side_table(self):
        item = self.binding(question("S", code="QS"), code="QS")
        self.assertIsNone(item.side_table)
        self.assertNotIn("sideTable", item.to_dict())

    def test_a_repeating_table_declares_its_columns_and_structure_version(self):
        declared = self.binding(table(), code="QTABLE").to_dict()["sideTable"]
        self.assertEqual("question-extension-tables-v1", declared["contract"])
        self.assertEqual("mjyquestionextensions_answer_cell", declared["cellTable"])
        self.assertEqual("mjyquestionextensions_answer_state", declared["stateTable"])
        self.assertEqual("rt1", declared["structureVersion"])
        self.assertEqual(TABLE_COLUMNS, declared["columns"])
        self.assertTrue(declared["structureDigest"].startswith("sd1:"))

    def test_a_heatmap_declares_the_generated_columns(self):
        declared = self.binding(heatmap(), code="QHEAT").to_dict()["sideTable"]
        self.assertEqual(["x", "y"], [column["code"] for column in declared["columns"]])
        self.assertEqual("hm1", declared["structureVersion"])

    def test_the_digest_follows_the_structure_and_ignores_labels(self):
        relabelled = [dict(column, label="改了标签") for column in TABLE_COLUMNS]
        retyped = [dict(TABLE_COLUMNS[0]), dict(TABLE_COLUMNS[1], type="decimal")]
        self.assertEqual(structure_digest(TABLE_COLUMNS), structure_digest(relabelled))
        self.assertNotEqual(structure_digest(TABLE_COLUMNS), structure_digest(retyped))

    def test_the_binding_record_round_trips_the_side_table(self):
        from pubgw.binding import BindingRecord

        item = self.binding(table(), code="QTABLE")
        record = BindingRecord("e1", 1, "d", "c", "fm1", "fm1:x", "en", "now", (item,))
        restored = BindingRecord.from_dict(record.to_dict())
        self.assertEqual(item.side_table, restored.questions[0].side_table)


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
