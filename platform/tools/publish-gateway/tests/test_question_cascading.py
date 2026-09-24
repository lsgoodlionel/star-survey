"""R02-03 多级下拉：字典引用、生成的列定义、随 .lss 下发的字典快照。

与别的副表题型不同的一点全在这里：**取值集合不在题目属性里**。省市区有数千个节点，
塞进 `mjy_table_columns` 的 options 既超过 MAX_COLUMN_OPTIONS 也超过任何合理体量，
所以列的类型是 `dict`（取值由字典的某一层决定），字典本身作为快照走 plugin_settings。
"""

import dataclasses
import json
import unittest
import xml.etree.ElementTree as ElementTree

from pubgw.binding import BindingRecord  # noqa: F401  （形状说明用）
from pubgw.compiler import LssCompiler
from pubgw.fieldmap import binding_map, parse_fieldmap
from pubgw.qtypes import expected_rows
from pubgw.questions.theme_kit import MAX_DICTIONARY_LEVELS
from pubgw.validate import validate_definition

from .fixtures import fieldmap_for
from .qtype_fixtures import definition_with, first_question, question
from .theme_fixtures import cascading_select, codes, compile_attributes, issues

#: 夹具里那本字典：北京 / 广东两棵子树，与平台 DictionaryFixture 同形。
DICTIONARY_NODES = [
    ["110000", "", "北京市"],
    ["110100", "110000", "市辖区"],
    ["110101", "110100", "东城区"],
    ["440000", "", "广东省"],
    ["440100", "440000", "广州市"],
    ["440103", "440100", "荔湾区"],
]
DICTIONARY = {
    "code": "cn-admin-divisions",
    "version": "2024.1",
    "digest": "dg1:0123456789abcdef",
    "nodes": DICTIONARY_NODES,
}


def definition(*questions, dictionaries=(DICTIONARY,)):
    return dataclasses.replace(definition_with(*questions), dictionaries=tuple(dictionaries))


class CascadingColumnsTest(unittest.TestCase):
    """生成的列定义：一层一列，取值由字典的那一层决定。"""

    def columns(self, **options):
        raw = compile_attributes(cascading_select(**options), code="QREGION")["mjy_table_columns"]
        return json.loads(raw)

    def test_one_column_per_level_in_order(self):
        columns = self.columns()
        self.assertEqual(["L1", "L2", "L3"], [column["code"] for column in columns])
        self.assertEqual(["省", "市", "区"], [column["label"] for column in columns])
        self.assertEqual([1, 2, 3], [column["level"] for column in columns])

    def test_every_level_is_a_dictionary_column_pinned_to_one_version(self):
        for column in self.columns():
            self.assertEqual("dict", column["type"])
            self.assertTrue(column["required"])
            self.assertEqual("cn-admin-divisions", column["dictionary"])
            self.assertEqual("2024.1", column["dictionaryVersion"])
            # 取值集合**不**在列定义里：这正是本题型存在的理由。
            self.assertNotIn("options", column)

    def test_the_answer_is_one_row_because_it_is_one_place(self):
        attributes = compile_attributes(cascading_select(), code="QREGION")
        self.assertEqual(("1", "1"), (attributes["mjy_table_min_rows"], attributes["mjy_table_max_rows"]))

    def test_the_dictionary_reference_reaches_the_plugin_as_attributes(self):
        attributes = compile_attributes(cascading_select(), code="QREGION")
        self.assertEqual("cn-admin-divisions", attributes["mjy_dictionary"])
        self.assertEqual("2024.1", attributes["mjy_dictionary_version"])
        self.assertEqual("dg1:0123456789abcdef", attributes["mjy_dictionary_digest"])
        self.assertEqual("r1", attributes["mjy_structure_version"])

    def test_the_answer_is_still_one_engine_column(self):
        self.assertEqual((("QREGION", "", 0),), expected_rows(first_question(cascading_select())))


class CascadingOptionsTest(unittest.TestCase):
    """themeOptions 的形状。"""

    def test_the_theme_only_fits_a_long_text_question(self):
        self.assertEqual(
            ["E_THEME_TYPE_MISMATCH"],
            codes(question("S", code="QREGION", theme="mjy-cascading-select",
                           themeOptions={"structureVersion": "r1", "dictionary": "d", "levels": ["省"]})),
        )

    def test_every_required_option_is_required(self):
        for missing in ("structureVersion", "dictionary", "dictionaryVersion", "dictionaryDigest", "levels"):
            payload = cascading_select()
            del payload["themeOptions"][missing]
            self.assertEqual(["E_THEME_OPTION_REQUIRED"], codes(payload), missing)

    def test_levels_must_be_non_empty_labels(self):
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(cascading_select(levels=["省", ""])))
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(cascading_select(levels=["省", 7])))

    def test_there_is_a_ceiling_on_the_number_of_levels(self):
        too_many = ["第{}级".format(n) for n in range(MAX_DICTIONARY_LEVELS + 1)]
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(cascading_select(levels=too_many)))

    def test_the_pinned_version_must_look_like_a_structure_version(self):
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(cascading_select(dictionaryVersion="2024 年版")))

    def test_the_digest_must_look_like_a_digest(self):
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(cascading_select(dictionaryDigest="whatever")))

    def test_a_well_formed_question_has_no_theme_issues(self):
        self.assertEqual([], [code for code in codes(cascading_select()) if code.startswith("E_THEME")])


class CascadingBindingTest(unittest.TestCase):
    """绑定记录里的副表声明——读端据它解释单元格。"""

    def binding(self, *questions, code):
        payload = definition_with(*questions)
        rows = parse_fieldmap(fieldmap_for(payload))
        return {item.code: item for item in binding_map(payload, rows)}[code]

    def test_the_side_table_declares_the_levels_and_the_pinned_version(self):
        declared = self.binding(cascading_select(), code="QREGION").to_dict()["sideTable"]
        self.assertEqual(["L1", "L2", "L3"], [column["code"] for column in declared["columns"]])
        self.assertEqual("r1", declared["structureVersion"])
        self.assertTrue(declared["structureDigest"].startswith("sd1:"))

    def test_a_different_dictionary_version_is_a_different_structure(self):
        """这条是本题型对结构摘要的要求：字典换了一版，单元格里那些代码的含义就变了。"""
        first = self.binding(cascading_select(), code="QREGION").to_dict()["sideTable"]
        second = self.binding(cascading_select(dictionaryVersion="2025.1"),
                              code="QREGION").to_dict()["sideTable"]
        self.assertNotEqual(first["structureDigest"], second["structureDigest"])

    def test_a_different_dictionary_is_a_different_structure(self):
        first = self.binding(cascading_select(), code="QREGION").to_dict()["sideTable"]
        second = self.binding(cascading_select(dictionary="stores"), code="QREGION").to_dict()["sideTable"]
        self.assertNotEqual(first["structureDigest"], second["structureDigest"])


class DictionarySnapshotTest(unittest.TestCase):
    """随定义下发的字典快照：校验与落进 .lss。"""

    def report(self, *questions, dictionaries=(DICTIONARY,)):
        return validate_definition(definition(*questions, dictionaries=dictionaries))

    def test_a_referenced_dictionary_must_be_shipped_with_the_definition(self):
        report = self.report(cascading_select(), dictionaries=())
        self.assertIn("E_DICTIONARY_MISSING", [issue.code for issue in report.issues])

    def test_the_shipped_version_must_be_the_one_the_question_was_pinned_to(self):
        stale = {**DICTIONARY, "version": "2023.1"}
        report = self.report(cascading_select(), dictionaries=(stale,))
        self.assertIn("E_DICTIONARY_MISSING", [issue.code for issue in report.issues])

    def test_the_shipped_digest_must_match_the_one_the_question_carries(self):
        tampered = {**DICTIONARY, "digest": "dg1:ffffffffffffffff"}
        report = self.report(cascading_select(), dictionaries=(tampered,))
        self.assertIn("E_DICTIONARY_DIGEST", [issue.code for issue in report.issues])

    def test_a_snapshot_with_a_dangling_parent_is_refused(self):
        orphan = {**DICTIONARY, "nodes": [["110101", "999999", "东城区"]]}
        report = self.report(cascading_select(), dictionaries=(orphan,))
        self.assertIn("E_DICTIONARY_SHAPE", [issue.code for issue in report.issues])

    def test_a_snapshot_with_duplicate_codes_is_refused(self):
        twice = {**DICTIONARY, "nodes": [["110000", "", "北京市"], ["110000", "", "又一个"]]}
        report = self.report(cascading_select(), dictionaries=(twice,))
        self.assertIn("E_DICTIONARY_SHAPE", [issue.code for issue in report.issues])

    def test_a_snapshot_shallower_than_the_question_needs_is_refused(self):
        """声明了三级，字典只有两层——作答者永远填不满第三级。"""
        shallow = {**DICTIONARY, "nodes": [["110000", "", "北京市"], ["110100", "110000", "市辖区"]]}
        report = self.report(cascading_select(), dictionaries=(shallow,))
        self.assertIn("E_DICTIONARY_DEPTH", [issue.code for issue in report.issues])

    def test_a_well_formed_snapshot_raises_nothing(self):
        self.assertEqual([], [issue.code for issue in self.report(cascading_select()).issues
                              if issue.code.startswith("E_DICTIONARY")])

    def test_an_unused_dictionary_is_refused_rather_than_silently_shipped(self):
        spare = {**DICTIONARY, "code": "stores", "digest": "dg1:1111111111111111"}
        report = self.report(cascading_select(), dictionaries=(DICTIONARY, spare))
        self.assertIn("E_DICTIONARY_UNUSED", [issue.code for issue in report.issues])


class DictionaryInTheLssTest(unittest.TestCase):
    """快照要真的到引擎：plugin_settings 一行，key=dictionaries。"""

    def plugin_settings(self, *questions, dictionaries=(DICTIONARY,)):
        compiled = LssCompiler().compile(definition(*questions, dictionaries=dictionaries))
        root = ElementTree.fromstring(compiled.lss)
        section = root.find("plugin_settings")
        if section is None:
            return []
        found = []
        for row in section.findall("rows/row"):
            found.append({child.tag: (child.text or "") for child in row})
        return found

    def test_the_snapshot_reaches_the_engine_as_one_plugin_setting(self):
        rows = self.plugin_settings(cascading_select())
        dictionaries = [row for row in rows if row["key"] == "dictionaries"]
        self.assertEqual(1, len(dictionaries))
        self.assertEqual("MjyQuestionExtensions", dictionaries[0]["name"])

        payload = json.loads(dictionaries[0]["value"])
        self.assertEqual(1, payload["v"])
        self.assertEqual(["cn-admin-divisions"], [one["code"] for one in payload["dictionaries"]])
        self.assertEqual("2024.1", payload["dictionaries"][0]["version"])
        self.assertEqual(DICTIONARY_NODES, payload["dictionaries"][0]["nodes"])

    def test_a_definition_without_a_cascading_question_carries_no_such_row(self):
        rows = self.plugin_settings(question("S", code="Q1"), dictionaries=())
        self.assertEqual([], [row for row in rows if row["key"] == "dictionaries"])


class CascadingIssuePathsTest(unittest.TestCase):
    """错误路径要指到具体那道题，别让作者自己猜。"""

    def test_theme_issues_carry_the_question_path(self):
        payload = cascading_select()
        del payload["themeOptions"]["dictionary"]
        self.assertEqual([("E_THEME_OPTION_REQUIRED", "groups[0].questions[0].themeOptions.dictionary")],
                         issues(payload))


if __name__ == "__main__":
    unittest.main()
