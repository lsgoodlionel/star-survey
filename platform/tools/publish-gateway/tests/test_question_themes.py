"""WP-02 切片 02.3：平台题型主题（themeOptions）的 422 校验与编译产物。

主题只换展示，不改答卷列的形状，所以每一条都顺带证明 ``expected_rows`` 与结构指纹没有变。
副表主题（自增表格、热力图）另外证明绑定记录里的副表声明与结构摘要。
"""

import json
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from pubgw.compiler import LssCompiler
from pubgw.fieldmap import binding_map, definition_signature, parse_fieldmap
from pubgw.qtypes import expected_rows
from pubgw.questions.themes import THEMES, VIEW_FOLDERS, structure_digest
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


LOOP_OBJECTS = [{"code": "B1", "label": "甲品牌"}, {"code": "B2", "label": "乙品牌"}]
LOOP_DIMENSIONS = [{"code": "price", "label": "价格"}, {"code": "service", "label": "服务"}]
LOOP_SCALE = [{"code": "1", "label": "差"}, {"code": "2", "label": "一般"}, {"code": "3", "label": "好"}]


def loop_rating(**options):
    payload = {"structureVersion": "lr1", "objects": LOOP_OBJECTS,
               "dimensions": LOOP_DIMENSIONS, "scale": LOOP_SCALE}
    payload.update(options)
    return question("T", code="QLOOP", theme="mjy-loop-rating", themeOptions=payload)


PK_ITEMS = [
    {"code": "A", "label": "包装甲", "image": "https://assets.example.invalid/a.png"},
    {"code": "B", "label": "包装乙", "image": "https://assets.example.invalid/b.png"},
    {"code": "C", "label": "包装丙", "image": "https://assets.example.invalid/c.png"},
]
PK_PAIRS = [{"code": "P1", "left": "A", "right": "B"}, {"code": "P2", "left": "B", "right": "C"}]


def image_pk(**options):
    payload = {"structureVersion": "pk1", "items": PK_ITEMS, "pairs": PK_PAIRS}
    payload.update(options)
    return question("T", code="QPK", theme="mjy-image-pk", themeOptions=payload)


ALL_THEMED = (collapsible(), scan(), grouped(), stepper(), inline_blank(), table(), heatmap(),
              loop_rating(), image_pk())


# ------------------------------------------------------------------ 注册表


THEME_ROOT = Path(__file__).resolve().parents[4] / "themes/question"


def view_folder(theme, qtype):
    return THEME_ROOT / theme / "survey/questions/answer" / VIEW_FOLDERS[qtype]


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


class EnumColumnTest(unittest.TestCase):
    """枚举列与唯一列：给自增表格与循环评价共用的两条列约束。

    两条都必须在服务端成立——浏览器端渲染成下拉还是按钮都不作数，
    提交上来的永远是一个字符串。
    """

    def enum_table(self, column, **options):
        return table(columns=[column], **options)

    def test_an_enum_column_compiles_its_options_canonically(self):
        value = compile_attributes(self.enum_table(
            {"code": "grade", "type": "enum", "required": True,
             "options": [{"code": "A", "label": "优"}, {"code": "B", "label": "良"}]}), code="QTABLE")
        self.assertEqual(
            [{"code": "grade", "label": "grade", "type": "enum", "required": True,
              "options": [{"code": "A", "label": "优"}, {"code": "B", "label": "良"}]}],
            json.loads(value["mjy_table_columns"]),
        )

    def test_a_bare_code_list_is_accepted_and_labelled_by_its_code(self):
        value = compile_attributes(self.enum_table(
            {"code": "grade", "type": "enum", "options": ["A", "B"]}), code="QTABLE")
        self.assertEqual([{"code": "A", "label": "A"}, {"code": "B", "label": "B"}],
                         json.loads(value["mjy_table_columns"])[0]["options"])

    def test_an_enum_column_without_options_is_rejected(self):
        self.assertEqual(["E_THEME_OPTION_VALUE"],
                         codes(self.enum_table({"code": "grade", "type": "enum"})))

    def test_options_on_a_non_enum_column_are_rejected(self):
        self.assertEqual(["E_THEME_OPTION_VALUE"],
                         codes(self.enum_table({"code": "grade", "type": "text", "options": ["A"]})))

    def test_bad_option_shapes(self):
        # 取值代码可以数字打头（量表常写成 1…5），但不能带空格、也不能是中文。
        for opts in ([], [{"label": "没有代码"}], ["A", "A"], [{"code": "bad code"}], "A,B"):
            with self.subTest(options=opts):
                self.assertEqual(["E_THEME_OPTION_VALUE"], codes(
                    self.enum_table({"code": "grade", "type": "enum", "options": opts})))

    def test_a_distinct_column_is_carried_through(self):
        value = compile_attributes(self.enum_table(
            {"code": "grade", "type": "enum", "options": ["A"], "distinct": True}), code="QTABLE")
        self.assertTrue(json.loads(value["mjy_table_columns"])[0]["distinct"])

    def test_the_digest_follows_the_options_and_the_distinct_flag(self):
        plain = [{"code": "g", "type": "enum", "options": [{"code": "A", "label": "优"}]}]
        relabelled = [{"code": "g", "type": "enum", "options": [{"code": "A", "label": "改了标签"}]}]
        widened = [{"code": "g", "type": "enum", "options": [{"code": "A", "label": "优"},
                                                             {"code": "B", "label": "良"}]}]
        unique = [dict(plain[0], distinct=True)]
        # 标签不进摘要（改标签不影响读回），取值集合与唯一约束要进。
        self.assertEqual(structure_digest(plain), structure_digest(relabelled))
        self.assertNotEqual(structure_digest(plain), structure_digest(widened))
        self.assertNotEqual(structure_digest(plain), structure_digest(unique))


class LoopRatingTest(unittest.TestCase):
    """R02-11 循环评价：同一套评价维度对每个对象各问一遍，引擎没有循环。

    一行＝一个评价对象，一列＝一个维度；对象列是枚举＋唯一，行数被钉死成对象个数，
    三者合起来逼出「每个对象恰好评一次」，不必另写一套按行下标的规则。
    """

    def columns(self, **options):
        return json.loads(compile_attributes(loop_rating(**options), code="QLOOP")["mjy_table_columns"])

    def test_the_object_column_comes_first_and_is_unique(self):
        first = self.columns()[0]
        self.assertEqual(("target", "enum", True, True), (first["code"], first["type"],
                                                          first["required"], first["distinct"]))
        self.assertEqual(["B1", "B2"], [option["code"] for option in first["options"]])
        self.assertEqual(["甲品牌", "乙品牌"], [option["label"] for option in first["options"]])

    def test_one_enum_column_per_dimension_over_the_declared_scale(self):
        dimensions = self.columns()[1:]
        self.assertEqual(["price", "service"], [column["code"] for column in dimensions])
        for column in dimensions:
            self.assertEqual(("enum", True), (column["type"], column["required"]))
            self.assertEqual(["1", "2", "3"], [option["code"] for option in column["options"]])

    def test_the_row_count_is_pinned_to_the_number_of_objects(self):
        attributes = compile_attributes(loop_rating(), code="QLOOP")
        self.assertEqual(("2", "2"), (attributes["mjy_table_min_rows"], attributes["mjy_table_max_rows"]))

    def test_the_object_labels_reach_the_theme(self):
        value = compile_attributes(loop_rating(), code="QLOOP")["mjy_loop_objects"]
        self.assertEqual(LOOP_OBJECTS, json.loads(value))
        self.assertNotIn(" ", value)

    def test_structure_version_is_required(self):
        payload = loop_rating()
        del payload["themeOptions"]["structureVersion"]
        self.assertEqual(["E_THEME_OPTION_REQUIRED"], codes(payload))

    def test_objects_dimensions_and_scale_are_all_required(self):
        for name in ("objects", "dimensions", "scale"):
            with self.subTest(option=name):
                payload = loop_rating()
                del payload["themeOptions"][name]
                self.assertEqual(["E_THEME_OPTION_REQUIRED"], codes(payload))

    def test_bad_object_lists(self):
        for objects in ([{"label": "没有代码"}],
                        [{"code": "B1"}, {"code": "B1"}],
                        [{"code": "bad code"}],
                        [{"code": "B1", "label": 7}]):
            with self.subTest(objects=objects):
                self.assertEqual(["E_THEME_OPTION_VALUE"], codes(loop_rating(objects=objects)))

    def test_a_dimension_may_not_collide_with_the_object_column(self):
        self.assertEqual(["E_THEME_OPTION_VALUE"],
                         codes(loop_rating(dimensions=[{"code": "target", "label": "撞车"}])))

    def test_too_many_objects(self):
        many = [{"code": "B{}".format(index), "label": str(index)} for index in range(501)]
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(loop_rating(objects=many)))

    def test_too_many_dimensions(self):
        # 对象列占掉一列，所以维度最多 39 个。
        many = [{"code": "d{}".format(index), "label": str(index)} for index in range(40)]
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(loop_rating(dimensions=many)))

    def test_the_answer_is_still_one_engine_column(self):
        self.assertEqual((("QLOOP", "", 0),), expected_rows(first_question(loop_rating())))

    def test_the_theme_is_only_for_long_free_text(self):
        payload = dict(loop_rating(), type="S")
        self.assertEqual(["E_THEME_TYPE_MISMATCH"], codes(payload))


class ImagePkTest(unittest.TestCase):
    """R02-17 图片 PK：成对比较，配对由平台声明，展示位置随作答一并留痕。

    **一对一列**，这一列的可选值恰好是这一对的两张图——所以「选了不在这一对里的东西」
    根本不需要跨列规则，枚举列自己就挡住了。
    """

    def columns(self, **options):
        return json.loads(compile_attributes(image_pk(**options), code="QPK")["mjy_table_columns"])

    def test_each_pair_becomes_a_choice_column_over_its_own_two_items(self):
        chosen = [column for column in self.columns() if not column["code"].endswith("_shown")]
        self.assertEqual(["P1", "P2"], [column["code"] for column in chosen])
        self.assertEqual([["A", "B"], ["B", "C"]],
                         [[option["code"] for option in column["options"]] for column in chosen])
        for column in chosen:
            self.assertEqual(("enum", True), (column["type"], column["required"]))

    def test_each_pair_also_records_which_image_was_shown_first(self):
        """配对随机要可追溯：随机的是展示顺序，那就把它一起记下来。
        这一列不必填——关掉 JavaScript 直接填信封的那条路径给不出展示顺序。"""
        shown = [column for column in self.columns() if column["code"].endswith("_shown")]
        self.assertEqual(["P1_shown", "P2_shown"], [column["code"] for column in shown])
        self.assertEqual([["A", "B"], ["B", "C"]],
                         [[option["code"] for option in column["options"]] for column in shown])
        self.assertEqual([False, False], [column["required"] for column in shown])

    def test_the_answer_is_exactly_one_row(self):
        attributes = compile_attributes(image_pk(), code="QPK")
        self.assertEqual(("1", "1"), (attributes["mjy_table_min_rows"], attributes["mjy_table_max_rows"]))

    def test_the_images_reach_the_theme(self):
        value = compile_attributes(image_pk(), code="QPK")["mjy_pk_items"]
        self.assertEqual(PK_ITEMS, json.loads(value))
        self.assertNotIn(" ", value)

    def test_the_pairs_reach_the_theme(self):
        self.assertEqual(PK_PAIRS, json.loads(compile_attributes(image_pk(), code="QPK")["mjy_pk_pairs"]))

    def test_items_pairs_and_structure_version_are_required(self):
        for name in ("structureVersion", "items", "pairs"):
            with self.subTest(option=name):
                payload = image_pk()
                del payload["themeOptions"][name]
                self.assertEqual(["E_THEME_OPTION_REQUIRED"], codes(payload))

    def test_every_item_needs_an_image(self):
        for items in ([{"code": "A", "label": "甲"}],
                      [{"code": "A", "label": "甲", "image": ""}],
                      [{"code": "A", "label": "甲", "image": 7}]):
            with self.subTest(items=items):
                self.assertIn("E_THEME_OPTION_VALUE", codes(image_pk(items=items)))

    def test_a_pair_must_name_two_different_declared_items(self):
        for pairs in ([{"code": "P1", "left": "A", "right": "Z"}],
                      [{"code": "P1", "left": "A", "right": "A"}],
                      [{"code": "P1", "left": "A"}],
                      [{"left": "A", "right": "B"}],
                      [{"code": "P1", "left": "A", "right": "B"}, {"code": "P1", "left": "B", "right": "C"}]):
            with self.subTest(pairs=pairs):
                self.assertEqual(["E_THEME_OPTION_VALUE"], codes(image_pk(pairs=pairs)))

    def test_a_pair_code_leaves_room_for_the_shown_suffix(self):
        long_code = "P" * 30
        self.assertEqual(["E_THEME_OPTION_VALUE"],
                         codes(image_pk(pairs=[{"code": long_code, "left": "A", "right": "B"}])))

    def test_too_many_pairs(self):
        # 一对占两列，列数上限 40，所以最多 20 对。
        many = [{"code": "P{}".format(index), "left": "A", "right": "B"} for index in range(21)]
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(image_pk(pairs=many)))

    def test_the_answer_is_still_one_engine_column(self):
        self.assertEqual((("QPK", "", 0),), expected_rows(first_question(image_pk())))


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

    def test_an_image_pk_declares_one_column_per_pair_plus_its_shown_column(self):
        declared = self.binding(image_pk(), code="QPK").to_dict()["sideTable"]
        self.assertEqual(["P1", "P1_shown", "P2", "P2_shown"],
                         [column["code"] for column in declared["columns"]])
        self.assertEqual("pk1", declared["structureVersion"])

    def test_a_loop_rating_declares_the_object_column_and_every_dimension(self):
        declared = self.binding(loop_rating(), code="QLOOP").to_dict()["sideTable"]
        self.assertEqual(["target", "price", "service"], [column["code"] for column in declared["columns"]])
        self.assertEqual("lr1", declared["structureVersion"])
        self.assertTrue(declared["structureDigest"].startswith("sd1:"))

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
