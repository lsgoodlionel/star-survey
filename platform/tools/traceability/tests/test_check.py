"""六道闸门合起来跑。

两组用例：

* ``RepositoryTest``——**真实仓库**必须通过。它是这套东西每天的价值所在；
* ``GatesGoRedTest``——在一个人造的小仓库里逐条制造漂移，断言对应的闸门确实红。
  每条红用例都标注了它对应的现实场景。
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

from reqtrace import check
from reqtrace.cli import REPO_ROOT
from reqtrace.index import load_matrix, parse_matrix, render_index

THEME_SPECS_STUB = '''"""人造的题型注册表。"""

_THEMES = (
    ThemeSpec(
        name="mjy-collapsible",
        label="折叠栏目",
        requirement="R01-01",
        types=("X",),
    ),
)
'''

MATRIX_STUB = """# 矩阵

| 需求ID | 功能要求 | 实现方式 | 断言 |
|---|---|---|---|
| R01-01 | 空白与应用类型创建 | N+C | 断言 |
| R01-02 | 批量文本导入 | C | 断言 |
"""

REGISTRY_STUB = """# 登记表

| 需求ID | 层级 | 证据定位符 | 说明 |
|---|---|---|---|
| R01-01 | 单测 | `platform/tools/publish-gateway/tests/test_themes.py::CollapsibleTest::test_shape` | 折叠栏目 |
"""

TEST_STUB = '''
class CollapsibleTest:
    def test_shape(self):
        pass
'''


class RepositoryTest(unittest.TestCase):
    """真实仓库上的校验。"""

    def test_the_repository_passes_every_gate(self):
        report = check.run(REPO_ROOT)
        self.assertEqual([], report.problems)

    def test_the_index_covers_all_305_frozen_requirements(self):
        self.assertEqual(305, check.run(REPO_ROOT).requirement_count)

    def test_the_registry_actually_claims_something(self):
        """登记表被清空、或表格形状改得解析不到，这条会红——否则「零条目零问题」也算通过。"""
        report = check.run(REPO_ROOT)
        self.assertGreater(report.entry_count, 40, "登记表解析出的条目太少，格式大概坏了")
        self.assertGreater(report.covered_count, 20)

    def test_the_repository_wide_reference_scan_actually_looks_at_something(self):
        """扫描前缀写错就会一个文件都扫不到——那也是「零问题」。"""
        self.assertGreater(check.run(REPO_ROOT).referenced_count, 50)

    @unittest.skipIf(load_matrix() is None, "本机读不到 02 矩阵（CI 上正常）")
    def test_the_snapshot_still_matches_the_matrix_outside_the_repository(self):
        self.assertTrue(check.run(REPO_ROOT).matrix_checked)


class GatesGoRedTest(unittest.TestCase):
    """人造小仓库：每条用例先造出一处漂移，再断言它被报出来。"""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        self.matrix = parse_matrix(MATRIX_STUB)

        (self.root / "platform/docs/traceability").mkdir(parents=True)
        (self.root / "platform/tools/publish-gateway/pubgw/questions").mkdir(parents=True)
        (self.root / "platform/tools/publish-gateway/tests").mkdir(parents=True)
        self._write(check.INDEX_FILE, render_index(self.matrix))
        self._write(check.REGISTRY_FILE, REGISTRY_STUB)
        self._write(Path("platform/tools/publish-gateway/pubgw/questions/theme_specs.py"), THEME_SPECS_STUB)
        self._write(Path("platform/tools/publish-gateway/tests/test_themes.py"), TEST_STUB)
        subprocess.run(["git", "init", "-q"], cwd=str(self.root), check=True, capture_output=True)
        self._stage()
        self._lower_the_sentinel()

    def _write(self, relative, text):
        (self.root / relative).write_text(text, encoding="utf-8")

    def _stage(self):
        subprocess.run(["git", "add", "-A"], cwd=str(self.root), check=True, capture_output=True)

    def _lower_the_sentinel(self):
        """人造仓库里只有一个主题，真实下限（15）不适用；用 1 让别的闸门能单独被看见。"""
        original = check.code_claims.EXPECTED_AT_LEAST
        check.code_claims.EXPECTED_AT_LEAST = 1
        self.addCleanup(setattr, check.code_claims, "EXPECTED_AT_LEAST", original)

    def _run(self):
        return check.run(self.root, matrix=self.matrix)

    def _problems_matching(self, needle):
        return [problem for problem in self._run().problems if needle in problem]

    def test_the_fixture_repository_is_clean_to_begin_with(self):
        """先证明基线是绿的，否则下面每条「红」都可能只是夹具本身坏了。"""
        self.assertEqual([], self._run().problems)

    # ---------------------------------------------------------------- 闸门 3
    def test_a_renamed_test_makes_the_claimed_coverage_red(self):
        """现实场景：重构时把测试方法改了名，没人想到去改一张 Markdown 表。"""
        self._write(Path("platform/tools/publish-gateway/tests/test_themes.py"),
                    TEST_STUB.replace("test_shape", "test_column_shape_is_unchanged"))
        self._stage()
        self.assertEqual(1, len(self._problems_matching("找不到")))

    def test_a_deleted_test_file_makes_the_claimed_coverage_red(self):
        (self.root / "platform/tools/publish-gateway/tests/test_themes.py").unlink()
        self._stage()
        self.assertEqual(1, len(self._problems_matching("文件不存在")))

    # ---------------------------------------------------------------- 闸门 2
    def test_a_registry_entry_for_a_requirement_outside_the_index_is_red(self):
        """现实场景：手抖把 R01-02 写成 R01-20，那条证据于是挂在一条不存在的需求上。"""
        self._write(check.REGISTRY_FILE, REGISTRY_STUB.replace("| R01-01 |", "| R01-20 |"))
        self._stage()
        self.assertEqual(1, len(self._problems_matching("R01-20 不是矩阵里的需求编号")))

    # ---------------------------------------------------------------- 闸门 4
    def test_an_invented_requirement_id_in_a_code_comment_is_red(self):
        """现实场景：注释里凭空写了个编号（这里用 R01-84），评审时看不出来。"""
        self._write(Path("platform/tools/publish-gateway/pubgw/questions/theme_specs.py"),
                    THEME_SPECS_STUB + "\n# 另见 R01-84 的那一条\n")
        self._stage()
        self.assertEqual(1, len(self._problems_matching("R01-84 不是矩阵里的需求编号")))

    def test_a_reference_in_an_untracked_file_is_not_scanned(self):
        """只扫 git 索引：工作区里的临时文件不该让校验红。"""
        self._write(Path("platform/tools/publish-gateway/pubgw/questions/scratch.py"), "# R01-84\n")
        self.assertEqual([], self._run().problems)

    # ---------------------------------------------------------------- 闸门 5
    def test_a_new_theme_without_test_evidence_is_red(self):
        """现实场景：加了个题型主题、声明了它实现哪条需求，但没登记任何测试证据。"""
        self._write(
            Path("platform/tools/publish-gateway/pubgw/questions/theme_specs.py"),
            THEME_SPECS_STUB.replace(
                '        requirement="R01-01",\n        types=("X",),\n    ),\n',
                '        requirement="R01-01",\n        types=("X",),\n    ),\n'
                '    ThemeSpec(\n        name="mjy-brand-new",\n        label="新主题",\n'
                '        requirement="R01-02",\n        types=("S",),\n    ),\n',
            ),
        )
        self._stage()
        problems = self._problems_matching("mjy-brand-new")
        self.assertEqual(1, len(problems))
        self.assertIn("R01-02", problems[0])

    def test_a_registry_shape_that_stops_parsing_is_red(self):
        """现实场景：题型归属的正则不再匹配（字段换了顺序），静默变空就是假绿。"""
        self._write(Path("platform/tools/publish-gateway/pubgw/questions/theme_specs.py"),
                    THEME_SPECS_STUB.replace('requirement="R01-01"', "requirement=REQ_COLLAPSIBLE"))
        self._stage()
        self.assertEqual(1, len(self._problems_matching("正则大概不再匹配")))

    # ---------------------------------------------------------------- 闸门 6
    def test_a_stale_snapshot_is_red(self):
        """现实场景：矩阵加了一条需求，快照没重生成。"""
        wider = parse_matrix(MATRIX_STUB + "| R01-03 | 企业与个人模板创建 | N+C | 断言 |\n")
        report = check.run(self.root, matrix=wider)
        self.assertTrue(any("R01-03" in problem for problem in report.problems), report.problems)

    def test_the_matrix_check_is_skipped_rather_than_faked_when_it_is_unreachable(self):
        """CI 的检出里没有方案文档目录：那一道闸门应当明确跳过，而不是悄悄算通过。"""
        report = check.run(self.root, matrix=None)
        self.assertFalse(report.matrix_checked)
        self.assertEqual([], report.problems)


if __name__ == "__main__":
    unittest.main()
