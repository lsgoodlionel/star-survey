"""全仓引用扫描的范围。

这一层最容易出的毛病是「扫不到」——前缀写错、后缀漏了、豁免范围开太大，结果一个文件
都没进来，而校验照样绿。`test_script_modes.py` 里也有同样形状的一条哨兵用例，理由相同。
"""

import unittest

from reqtrace import references
from reqtrace.cli import REPO_ROOT
from reqtrace.check import load_index


class ScopeTest(unittest.TestCase):
    def setUp(self):
        self.files = references.owned_files(REPO_ROOT)

    def test_the_scan_actually_looks_at_something(self):
        self.assertGreater(len(self.files), 300, "扫不到自有文件，前缀或后缀大概写错了")

    def test_the_gateway_the_platform_and_the_plugins_are_all_in_scope(self):
        for prefix in ("platform/tools/publish-gateway/", "platform/services/business/", "plugins/Mjy"):
            self.assertTrue(
                any(path.startswith(prefix) for path in self.files),
                "{} 不在扫描范围内".format(prefix),
            )

    def test_the_project_engine_theme_is_in_scope(self):
        """`themes/survey/zh-business` 是本项目的引擎主题，不是上游快照——早先的前缀漏了它。"""
        self.assertTrue(any(path.startswith("themes/survey/zh-business/") for path in self.files))

    def test_upstream_files_are_out_of_scope(self):
        for path in self.files:
            self.assertTrue(path.startswith(references.OWNED_PREFIXES), path)

    def test_the_exemption_only_covers_the_traceability_tool_itself(self):
        """豁免开大了，别处的错编号就抓不到了。"""
        self.assertEqual(
            ("platform/tools/traceability/", "platform/docs/traceability/"),
            references.EXEMPT_PREFIXES,
        )
        self.assertEqual([], [p for p in self.files if p.startswith(references.EXEMPT_PREFIXES)])


class ScanTest(unittest.TestCase):
    def test_every_requirement_id_in_the_repository_is_in_the_index(self):
        known = set(load_index(REPO_ROOT).ids)
        referenced = references.scan(REPO_ROOT, references.owned_files(REPO_ROOT))
        self.assertEqual([], references.unknown_references(referenced, known))

    def test_a_bad_id_in_a_scanned_file_is_reported(self):
        """反向证明：上面那条不是因为 unknown_references 永远返回空。"""
        problems = references.unknown_references({"R01-84": ["platform/x.py"]}, {"R01-01"})
        self.assertEqual(1, len(problems))
        self.assertIn("platform/x.py", problems[0])


if __name__ == "__main__":
    unittest.main()
