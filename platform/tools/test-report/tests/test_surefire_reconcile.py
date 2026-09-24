"""对账脚本自己的用例。

重点不是「一切正常时它会绿」，而是**前几波真实发生过的两种假绿，它必须红**：

* 事故一：新测试类根本没被执行，报告里却 ``failures=0``；
* 事故二：改包名后旧报告残留在 ``target/surefire-reports`` 里被重复计数。

两种都按当时的形状造成 fixture（源码树 ＋ 报告目录），走完整的 ``main()``，
断言退出码非零、且报文说得出是哪个类。
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from surefire_reconcile import (  # noqa: E402
    EXIT_MISMATCH, EXIT_OK, EXIT_USAGE, SuiteReport, TestClass, classify_source, main,
    reconcile, scan_reports, scan_sources, totals_of,
)

_TEST_CLASS = """package cn.mjy.platform.demo;

import org.junit.jupiter.api.Test;

class {name} {{

    @Test
    void does_something() {{
    }}
}}
"""

_REPEATED_TEST_CLASS = """package cn.mjy.platform.demo;

import org.junit.jupiter.api.RepeatedTest;

class {name} {{

    @RepeatedTest(5)
    void does_something_five_times() {{
    }}
}}
"""

_ANNOTATION_TYPE = """package cn.mjy.platform.demo;

import java.lang.annotation.Retention;

/** 组合注解，本身不产生任何用例。 */
public @interface {name} {{
    String MASTER_SECRET = "only-for-tests";
}}
"""

_ABSTRACT_BASE = """package cn.mjy.platform.demo;

import org.junit.jupiter.api.Test;

abstract class {name} {{

    @Test
    void inherited() {{
    }}
}}
"""

_HELPER = """package cn.mjy.platform.demo;

/** 名字落在 surefire 模式里，但没有任何用例。 */
final class {name} {{
}}
"""

_REPORT = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<testsuite name="{fqcn}" time="0.1" tests="{tests}" errors="{errors}"'
    ' skipped="{skipped}" failures="{failures}"/>\n'
)


class SourceClassificationTest(unittest.TestCase):
    """源码一侧的判定：少认一个类会误报「没被执行」，多认一个会天天红。"""

    def test_a_named_class_with_a_test_method_is_expected_to_run(self):
        self.assertTrue(classify_source(_TEST_CLASS.format(name="PaymentTest"), "PaymentTest"))

    def test_a_repeated_test_counts_as_a_test(self):
        source = _REPEATED_TEST_CLASS.format(name="ResourceMoveConcurrencyTest")
        self.assertTrue(classify_source(source, "ResourceMoveConcurrencyTest"))

    def test_an_annotation_type_is_not_expected_to_run(self):
        source = _ANNOTATION_TYPE.format(name="AssetIntegrationTest")
        self.assertFalse(classify_source(source, "AssetIntegrationTest"))

    def test_an_abstract_base_class_is_not_expected_to_run(self):
        source = _ABSTRACT_BASE.format(name="AbstractTenantTest")
        self.assertFalse(classify_source(source, "AbstractTenantTest"))

    def test_a_helper_named_like_a_test_is_not_expected_to_run(self):
        self.assertFalse(classify_source(_HELPER.format(name="TestTokens"), "TestTokens"))

    def test_a_test_class_named_outside_the_surefire_patterns_is_not_expected_to_run(self):
        # surefire 根本不会扫它，所以它不该被算进「应执行」——但这本身值得人看见，
        # 见 ``SurefireNamingTest``（平台车道的命名检查另说）。
        source = _TEST_CLASS.format(name="PaymentChecks")
        self.assertFalse(classify_source(source, "PaymentChecks"))


class ReconcileTest(unittest.TestCase):
    """两边集合对不上时必须报出来，而且要说清是哪个类。"""

    def _class(self, name):
        return TestClass("cn.mjy.platform.demo." + name, "/src/" + name + ".java")

    def _report(self, name, tests=3, failures=0, errors=0, skipped=0):
        return SuiteReport(
            fqcn="cn.mjy.platform.demo." + name,
            tests=tests,
            failures=failures,
            errors=errors,
            skipped=skipped,
            path="/reports/TEST-" + name + ".xml",
        )

    def test_a_matching_pair_of_sets_reconciles(self):
        classes = [self._class("AlphaTest"), self._class("BetaTest")]
        reports = [self._report("AlphaTest"), self._report("BetaTest")]

        totals, problems = reconcile(classes, reports)

        self.assertEqual([], problems)
        self.assertEqual(6, totals.tests)
        self.assertEqual(2, totals.classes)

    def test_a_test_class_that_never_ran_is_reported(self):
        """事故一：新类写好了，但没被执行；报告里 failures=0，绿得毫无破绽。"""
        classes = [self._class("AlphaTest"), self._class("NewlyAddedTest")]
        reports = [self._report("AlphaTest")]

        _totals, problems = reconcile(classes, reports)

        self.assertEqual(1, len(problems), problems)
        self.assertIn("没被执行", problems[0])
        self.assertIn("NewlyAddedTest", problems[0])

    def test_a_stale_report_from_a_renamed_package_is_reported(self):
        """事故二：改包名后旧报告残留，总数与类数都虚高。"""
        classes = [self._class("AlphaTest")]
        stale = SuiteReport(
            fqcn="cn.mjy.platform.oldpackage.AlphaTest",
            tests=14,
            failures=0,
            errors=0,
            skipped=0,
            path="/reports/TEST-cn.mjy.platform.oldpackage.AlphaTest.xml",
        )

        totals, problems = reconcile(classes, [self._report("AlphaTest"), stale])

        self.assertEqual(1, len(problems), problems)
        self.assertIn("陈旧报告", problems[0])
        self.assertIn("oldpackage", problems[0])
        # 对账前的「总数」正是被虚高过的那个数字：3 + 14。
        self.assertEqual(17, totals.tests)

    def test_an_empty_report_directory_is_reported(self):
        _totals, problems = reconcile([self._class("AlphaTest")], [])

        self.assertEqual(1, len(problems), problems)
        self.assertIn("根本没跑起来", problems[0])

    def test_a_suite_that_ran_nothing_is_reported(self):
        classes = [self._class("AlphaTest")]

        _totals, problems = reconcile(classes, [self._report("AlphaTest", tests=0)])

        self.assertTrue(any("0 条用例" in problem for problem in problems), problems)

    def test_failures_are_reported(self):
        classes = [self._class("AlphaTest")]

        _totals, problems = reconcile(classes, [self._report("AlphaTest", failures=1)])

        self.assertTrue(any("失败 1 条" in problem for problem in problems), problems)

    def test_a_baseline_that_does_not_match_is_reported(self):
        classes = [self._class("AlphaTest")]
        reports = [self._report("AlphaTest", tests=3)]

        _totals, problems = reconcile(classes, reports, expect_tests=4, expect_classes=2)

        self.assertEqual(2, len(problems), problems)
        self.assertIn("用例总数对不上", problems[0])
        self.assertIn("测试类数对不上", problems[1])

    def test_a_matching_baseline_reconciles(self):
        classes = [self._class("AlphaTest")]
        reports = [self._report("AlphaTest", tests=3)]

        _totals, problems = reconcile(classes, reports, expect_tests=3, expect_classes=1)

        self.assertEqual([], problems)

    def test_totals_add_every_suite_up(self):
        reports = [self._report("AlphaTest", tests=3, skipped=1), self._report("BetaTest", tests=5)]

        totals = totals_of(reports)

        self.assertEqual(8, totals.tests)
        self.assertEqual(1, totals.skipped)


class _Tree:
    """临时的「源码树 ＋ 报告目录」，用完删干净。"""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="surefire-reconcile-")
        self.sources = os.path.join(self.root, "src/test/java/cn/mjy/platform/demo")
        self.reports = os.path.join(self.root, "target/surefire-reports")
        os.makedirs(self.sources)
        os.makedirs(self.reports)

    def add_source(self, name, template=_TEST_CLASS):
        path = os.path.join(self.sources, name + ".java")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(template.format(name=name))

    def add_report(self, fqcn, tests=3, failures=0, errors=0, skipped=0):
        path = os.path.join(self.reports, "TEST-" + fqcn + ".xml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(_REPORT.format(
                fqcn=fqcn, tests=tests, failures=failures, errors=errors, skipped=skipped))

    def argv(self, *extra):
        return ["--reports", self.reports, "--sources", os.path.join(self.root, "src/test/java")] + list(extra)

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


class EndToEndTest(unittest.TestCase):
    """从真的文件读起，走完整的 main()，看退出码。"""

    def setUp(self):
        self.tree = _Tree()
        self.addCleanup(self.tree.close)

    def test_a_clean_run_exits_zero(self):
        self.tree.add_source("AlphaTest")
        self.tree.add_source("TestTokens", template=_HELPER)
        self.tree.add_source("DemoIntegrationTest", template=_ANNOTATION_TYPE)
        self.tree.add_report("cn.mjy.platform.demo.AlphaTest")

        self.assertEqual(EXIT_OK, main(self.tree.argv()))

    def test_a_class_that_never_ran_exits_non_zero(self):
        self.tree.add_source("AlphaTest")
        self.tree.add_source("NewlyAddedTest")
        self.tree.add_report("cn.mjy.platform.demo.AlphaTest")

        self.assertEqual(EXIT_MISMATCH, main(self.tree.argv()))

    def test_a_stale_report_exits_non_zero(self):
        self.tree.add_source("AlphaTest")
        self.tree.add_report("cn.mjy.platform.demo.AlphaTest")
        self.tree.add_report("cn.mjy.platform.oldpackage.AlphaTest", tests=14)

        self.assertEqual(EXIT_MISMATCH, main(self.tree.argv()))

    def test_a_baseline_mismatch_exits_non_zero(self):
        self.tree.add_source("AlphaTest")
        self.tree.add_report("cn.mjy.platform.demo.AlphaTest", tests=3)

        self.assertEqual(EXIT_MISMATCH, main(self.tree.argv("--expect-tests", "4")))

    def test_a_missing_report_directory_exits_with_the_usage_code(self):
        argv = ["--reports", os.path.join(self.tree.root, "nope"),
                "--sources", os.path.join(self.tree.root, "src/test/java")]

        self.assertEqual(EXIT_USAGE, main(argv))

    def test_the_scanners_read_what_was_written(self):
        self.tree.add_source("AlphaTest")
        self.tree.add_report("cn.mjy.platform.demo.AlphaTest", tests=7, skipped=2)

        classes = scan_sources(os.path.join(self.tree.root, "src/test/java"))
        reports = scan_reports(self.tree.reports)

        self.assertEqual(["cn.mjy.platform.demo.AlphaTest"], [item.fqcn for item in classes])
        self.assertEqual(7, reports[0].tests)
        self.assertEqual(2, reports[0].skipped)


if __name__ == "__main__":
    unittest.main()
