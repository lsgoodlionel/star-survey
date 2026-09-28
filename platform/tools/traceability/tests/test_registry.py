"""登记表的解析。

这一层要挡住的是「看起来填了、其实解析不到」：列数写错、层级用词自创、定位符写成一句话。
这些在 Markdown 里都渲染得很正常，评审时看不出来——所以每一种都要有一条断言它被报出来。
"""

import unittest

from reqtrace.registry import covered_requirements, duplicate_entries, parse_registry

GOOD = """# 登记表

| 需求ID | 层级 | 证据定位符 | 说明 |
|---|---|---|---|
| R04-01 | 单测 | `a/tests/test_policy.py::AccessTest::test_password` | 明文不落库 |
| R04-01 | 端到端 | `a/e2e/access_policy.py::scenario_password` | 真实引擎 |
| R04-04 | 单测 | `a/tests/test_policy.py::WindowTest::test_timezone` | 时区窗口 |
"""


class ParsingTest(unittest.TestCase):
    def test_every_row_becomes_an_entry_with_its_line_number(self):
        entries, problems = parse_registry(GOOD)
        self.assertEqual([], problems)
        self.assertEqual(3, len(entries))
        self.assertEqual(["R04-01", "R04-01", "R04-04"], [e.requirement_id for e in entries])
        self.assertEqual([5, 6, 7], [e.line for e in entries])

    def test_backticks_around_the_locator_are_stripped(self):
        entries, _ = parse_registry(GOOD)
        self.assertEqual("a/e2e/access_policy.py::scenario_password", entries[1].locator.raw)

    def test_the_header_and_the_separator_row_are_not_entries(self):
        entries, problems = parse_registry("| 需求ID | 层级 | 证据定位符 | 说明 |\n|---|---|---|---|\n")
        self.assertEqual(([], []), (entries, problems))

    def test_prose_tables_elsewhere_in_the_file_are_ignored(self):
        entries, problems = parse_registry("| 列 | 说明 |\n|---|---|\n| 层级 | 单测／集成／端到端 |\n")
        self.assertEqual(([], []), (entries, problems))

    def test_several_entries_group_under_one_requirement(self):
        entries, _ = parse_registry(GOOD)
        covered = covered_requirements(entries)
        self.assertEqual(2, len(covered["R04-01"]))


class FormatGoesRedTest(unittest.TestCase):
    def test_a_row_with_a_missing_column_is_reported(self):
        _, problems = parse_registry("| R04-01 | 单测 | `a/tests/test_x.py::A::test_b` |\n")
        self.assertEqual(1, len(problems))
        self.assertIn("列数不对", problems[0])

    def test_an_invented_level_is_reported(self):
        _, problems = parse_registry("| R04-01 | 冒烟 | `a/tests/test_x.py::A::test_b` | 说明 |\n")
        self.assertIn("层级", problems[0])

    def test_a_locator_that_is_really_a_sentence_is_reported(self):
        _, problems = parse_registry("| R04-01 | 单测 | 见 access_policy 的那几个场景 | 说明 |\n")
        self.assertEqual(1, len(problems))

    def test_a_bad_row_does_not_stop_the_rest_of_the_table(self):
        """一次把整张表的毛病都报出来，而不是改一行跑一次。"""
        entries, problems = parse_registry(GOOD + "| R04-05 | 冒烟 | `a/tests/test_x.py::A::test_b` | 说明 |\n")
        self.assertEqual(3, len(entries))
        self.assertEqual(1, len(problems))


class DuplicateTest(unittest.TestCase):
    def test_the_same_requirement_and_locator_twice_is_reported(self):
        problems = duplicate_entries(parse_registry(
            GOOD + "| R04-01 | 单测 | `a/tests/test_policy.py::AccessTest::test_password` | 又写了一遍 |\n"
        )[0])
        self.assertEqual(1, len(problems))
        self.assertIn("重复登记", problems[0])

    def test_the_same_locator_under_two_requirements_is_fine(self):
        """一个端到端脚本同时覆盖好几条需求是正常的。"""
        entries, _ = parse_registry(
            GOOD + "| R04-05 | 端到端 | `a/e2e/access_policy.py::scenario_password` | 同一场景 |\n"
        )
        self.assertEqual([], duplicate_entries(entries))


if __name__ == "__main__":
    unittest.main()
