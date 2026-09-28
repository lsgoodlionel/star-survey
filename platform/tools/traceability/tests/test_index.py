"""需求编号索引的解析与对账。

这里每一条「红」用例都对着一种真实会发生的事：矩阵加了一条需求、改了功能要求的措辞、
或者快照与矩阵的列顺序被搞混（这最后一种在开发本校验时就真的发生过一次：
快照有三列而矩阵只有两列，共用一个正则的结果是 305 条全部报「功能要求不一致」）。
"""

import unittest

from reqtrace.index import parse_index, parse_matrix, reconcile, render_index

MATRIX = """# 全量需求追踪矩阵

| 工作包 | 领域 | 条目数 |
|---|---|---:|
| WP-01 | 创建与编辑 | 2 |

| 需求ID | 功能要求 | 实现方式 | 核心验收断言 |
|---|---|---|---|
| R01-01 | 空白与应用类型创建 | N+C | 各创建后出现对应配置 |
| R01-02 | 批量文本导入 | C | 题号及顺序一致 |
"""


class MatrixParsingTest(unittest.TestCase):
    def test_only_requirement_rows_are_taken_not_the_overview_table(self):
        index = parse_matrix(MATRIX)
        self.assertEqual(("R01-01", "R01-02"), index.ids)
        self.assertEqual("批量文本导入", index.by_id()["R01-02"].title)

    def test_the_work_package_comes_from_the_requirement_id(self):
        self.assertEqual("WP-01", parse_matrix(MATRIX).by_id()["R01-02"].work_package)

    def test_a_matrix_without_requirement_rows_is_an_error_not_an_empty_index(self):
        with self.assertRaises(ValueError):
            parse_matrix("# 只有标题，没有表")

    def test_a_duplicated_requirement_id_is_an_error(self):
        with self.assertRaises(ValueError) as caught:
            parse_matrix(MATRIX + "| R01-02 | 又一次 | C | 断言 |\n")
        self.assertIn("R01-02", str(caught.exception))


class RoundTripTest(unittest.TestCase):
    def test_a_rendered_snapshot_reads_back_as_the_same_index(self):
        matrix = parse_matrix(MATRIX)
        snapshot = parse_index(render_index(matrix))
        self.assertEqual(matrix.ids, snapshot.ids)
        self.assertEqual(matrix.digest, snapshot.digest)
        self.assertEqual([], reconcile(snapshot, matrix))

    def test_the_snapshot_keeps_the_feature_column_not_the_work_package_column(self):
        """快照多一列工作包。若解析时按第二列读，功能要求会被读成「WP-01」——
        开发本校验时就是这样一次性产出了 305 条假告警。"""
        snapshot = parse_index(render_index(parse_matrix(MATRIX)))
        self.assertEqual("批量文本导入", snapshot.by_id()["R01-02"].title)


class ReconcileGoesRedTest(unittest.TestCase):
    """人为制造不一致，断言 reconcile 确实报错。"""

    def setUp(self):
        self.matrix = parse_matrix(MATRIX)
        self.snapshot = parse_index(render_index(self.matrix))

    def test_a_requirement_added_to_the_matrix_is_reported(self):
        wider = parse_matrix(MATRIX + "| R01-03 | 企业与个人模板创建 | N+C | 断言 |\n")
        problems = reconcile(self.snapshot, wider)
        self.assertTrue(any("R01-03" in problem for problem in problems), problems)

    def test_a_requirement_dropped_from_the_matrix_is_reported(self):
        narrower = parse_matrix(MATRIX.replace("| R01-02 | 批量文本导入 | C | 题号及顺序一致 |\n", ""))
        problems = reconcile(self.snapshot, narrower)
        self.assertTrue(any("R01-02" in problem for problem in problems), problems)

    def test_a_reworded_feature_requirement_is_reported(self):
        reworded = parse_matrix(MATRIX.replace("批量文本导入", "批量文本导入（含 Excel）"))
        problems = reconcile(self.snapshot, reworded)
        self.assertTrue(any("功能要求不一致" in problem for problem in problems), problems)

    def test_an_edit_that_changes_nothing_visible_still_moves_the_digest(self):
        """矩阵改了正文而没动需求行，摘要也会变——快照必须重生成，否则它记的就是旧原文。"""
        edited = parse_matrix(MATRIX.replace("# 全量需求追踪矩阵", "# 全量需求追踪矩阵（评审稿 v1.1）"))
        problems = reconcile(self.snapshot, edited)
        self.assertEqual(1, len(problems), problems)
        self.assertIn("没重生成", problems[0])


if __name__ == "__main__":
    unittest.main()
