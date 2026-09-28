#!/usr/bin/env python3
"""平台 Java 测试的**对账**：报告里的数字必须和源码里的测试类对得上。

前几波两次被假绿骗到：

1. 新写的测试类根本没被执行，而报告里 ``failures=0``——绿得一点问题没有，
   靠人工数总数差了 14 条才发现；
2. 改包名之后，上一轮的报告还躺在 ``target/surefire-reports`` 里被**重复计数**，
   虚高了 14 条 1 个类。

两次的共同点是：**只看 failures 是看不出来的**。所以这里不看 failures，
而是把两边的集合对起来：

* 源码里有 ``@Test``（或 ``@ParameterizedTest`` / ``@RepeatedTest`` / ``@TestFactory``
  / ``@TestTemplate``）、名字又落在 surefire 默认扫描模式里的类，**必须**有一份报告
  ——少一份就是事故 1；
* 报告里出现的类，**必须**在源码里找得到——多一份就是事故 2（改名、删掉或上一轮的残留）。

为什么不按文件时间判断陈旧：Maven 在容器里跑，报告的 mtime 来自容器时钟，
和宿主机时钟不是一个源，比时间会出假红。上面两条集合检查是无时钟的，
而且正好命中真实发生过的两种事故；跑前清空目录 ＋ 强制 ``clean`` 是它们的前提，
由 ``platform/deploy/test/run-platform-tests.sh`` 负责。

用法见该脚本；单独调用：

    python3 platform/tools/test-report/surefire_reconcile.py \\
        --reports platform/services/business/target/surefire-reports \\
        --sources platform/services/business/src/test/java \\
        [--expect-tests 1234] [--expect-classes 157]

对不上就非零退出（2 = 对账不通过，3 = 用法或环境有问题）。
"""

import argparse
import os
import re
import sys
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

EXIT_OK = 0
EXIT_MISMATCH = 2
EXIT_USAGE = 3

#: surefire 默认扫描的类名模式（没在 pom 里改过 includes，就是这四条）。
_INCLUDE_PATTERNS = (
    re.compile(r"\ATest.+\Z"),
    re.compile(r"\A.+Test\Z"),
    re.compile(r"\A.+Tests\Z"),
    re.compile(r"\A.+TestCase\Z"),
)

#: 让一个类真的产生用例的注解。少写一个，这份对账就会把整类误判成「没被执行」。
_TEST_ANNOTATIONS = re.compile(
    r"@(?:Test|ParameterizedTest|RepeatedTest|TestFactory|TestTemplate)\b"
)

_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.M)
#: 注解类型（``@interface``）名字也可能落在 surefire 模式里（AssetIntegrationTest），
#: 但它不会产生任何用例，必须排除，否则每轮都误报「没被执行」。
_ANNOTATION_TYPE = re.compile(r"@interface\s+(\w+)")
_ABSTRACT_CLASS = re.compile(r"\babstract\s+class\s+(\w+)")


@dataclass(frozen=True)
class TestClass:
    """源码里一个「应该被执行」的测试类。"""

    fqcn: str
    path: str


@dataclass(frozen=True)
class SuiteReport:
    """一份 ``TEST-*.xml`` 里的汇总数字。"""

    fqcn: str
    tests: int
    failures: int
    errors: int
    skipped: int
    path: str


@dataclass(frozen=True)
class Totals:
    tests: int
    classes: int
    failures: int
    errors: int
    skipped: int

    def describe(self) -> str:
        return "用例 {} 条，测试类 {} 个（失败 {}、错误 {}、跳过 {}）".format(
            self.tests, self.classes, self.failures, self.errors, self.skipped
        )


# ------------------------------------------------------------------ 源码一侧


def is_surefire_name(simple_name: str) -> bool:
    return any(pattern.match(simple_name) for pattern in _INCLUDE_PATTERNS)


def classify_source(source: str, simple_name: str) -> bool:
    """这份源码是不是「应该被执行」的测试类。"""
    if not is_surefire_name(simple_name):
        return False
    if _ANNOTATION_TYPE.search(source) and simple_name in _ANNOTATION_TYPE.findall(source):
        return False
    if simple_name in _ABSTRACT_CLASS.findall(source):
        return False
    return bool(_TEST_ANNOTATIONS.search(source))


def package_of(source: str, path: str) -> str:
    found = _PACKAGE.search(source)
    if not found:
        raise ValueError("{} 没有 package 声明，算不出全限定名".format(path))
    return found.group(1)


def scan_sources(root: str) -> List[TestClass]:
    """走一遍测试源码树，挑出所有「应该被执行」的测试类。"""
    if not os.path.isdir(root):
        raise FileNotFoundError("测试源码目录不存在：{}".format(root))
    found: List[TestClass] = []
    for current, _dirs, files in os.walk(root):
        for name in sorted(files):
            if not name.endswith(".java"):
                continue
            simple_name = name[: -len(".java")]
            path = os.path.join(current, name)
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            if classify_source(source, simple_name):
                found.append(TestClass(package_of(source, path) + "." + simple_name, path))
    return sorted(found, key=lambda item: item.fqcn)


# ------------------------------------------------------------------ 报告一侧


def parse_report(path: str) -> SuiteReport:
    root = ElementTree.parse(path).getroot()
    if root.tag != "testsuite":
        raise ValueError("{} 的根元素是 {}，不是 testsuite".format(path, root.tag))

    def number(attribute: str) -> int:
        return int(root.attrib.get(attribute, "0"))

    return SuiteReport(
        fqcn=root.attrib.get("name", ""),
        tests=number("tests"),
        failures=number("failures"),
        errors=number("errors"),
        skipped=number("skipped"),
        path=path,
    )


def scan_reports(directory: str) -> List[SuiteReport]:
    if not os.path.isdir(directory):
        raise FileNotFoundError("报告目录不存在：{}".format(directory))
    reports = [
        parse_report(os.path.join(directory, name))
        for name in sorted(os.listdir(directory))
        if name.startswith("TEST-") and name.endswith(".xml")
    ]
    return sorted(reports, key=lambda item: item.fqcn)


# -------------------------------------------------------------------- 对 账


def totals_of(reports: Sequence[SuiteReport]) -> Totals:
    return Totals(
        tests=sum(report.tests for report in reports),
        classes=len(reports),
        failures=sum(report.failures for report in reports),
        errors=sum(report.errors for report in reports),
        skipped=sum(report.skipped for report in reports),
    )


def _duplicates(names: Sequence[str]) -> List[str]:
    counts: Dict[str, int] = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    return sorted(name for name, count in counts.items() if count > 1)


def _listing(names: Sequence[str], limit: int = 20) -> str:
    shown = list(names[:limit])
    tail = "" if len(names) <= limit else "……另有 {} 个".format(len(names) - limit)
    return "，".join(shown) + tail


def reconcile(
    classes: Sequence[TestClass],
    reports: Sequence[SuiteReport],
    expect_tests: Optional[int] = None,
    expect_classes: Optional[int] = None,
) -> Tuple[Totals, List[str]]:
    """返回 ``(总数, 问题列表)``；问题列表为空才算对得上。"""
    totals = totals_of(reports)
    problems: List[str] = []
    if not reports:
        problems.append("报告目录里一份 TEST-*.xml 都没有：这一轮根本没跑起来")
        return totals, problems

    declared: Set[str] = {item.fqcn for item in classes}
    reported: Set[str] = {report.fqcn for report in reports}

    duplicated = _duplicates([report.fqcn for report in reports])
    if duplicated:
        problems.append("同一个测试类有多份报告（重复计数）：{}".format(_listing(duplicated)))

    missing = sorted(declared - reported)
    if missing:
        problems.append(
            "源码里有、报告里没有的测试类 {} 个——没被执行，而报告照样是绿的：{}".format(
                len(missing), _listing(missing)
            )
        )

    unexpected = sorted(reported - declared)
    if unexpected:
        problems.append(
            "报告里有、源码里没有的测试类 {} 个——陈旧报告，会被重复计数：{}".format(
                len(unexpected), _listing(unexpected)
            )
        )

    empty = sorted(report.fqcn for report in reports if report.tests == 0)
    if empty:
        problems.append("报告里 0 条用例的测试类：{}".format(_listing(empty)))

    if totals.failures or totals.errors:
        problems.append("失败 {} 条、错误 {} 条".format(totals.failures, totals.errors))

    if expect_tests is not None and expect_tests != totals.tests:
        problems.append("用例总数对不上：期望 {}，实际 {}".format(expect_tests, totals.tests))
    if expect_classes is not None and expect_classes != totals.classes:
        problems.append("测试类数对不上：期望 {}，实际 {}".format(expect_classes, totals.classes))

    return totals, problems


# --------------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="surefire 报告与测试源码的对账")
    parser.add_argument("--reports", required=True, help="surefire-reports 目录")
    parser.add_argument("--sources", required=True, help="测试源码根目录（src/test/java）")
    parser.add_argument("--expect-tests", type=int, default=None, help="期望的用例总数")
    parser.add_argument("--expect-classes", type=int, default=None, help="期望的测试类数")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        classes = scan_sources(args.sources)
        reports = scan_reports(args.reports)
    except (FileNotFoundError, ValueError) as problem:
        print("[对账] 跑不起来：{}".format(problem), file=sys.stderr)
        return EXIT_USAGE

    totals, problems = reconcile(classes, reports, args.expect_tests, args.expect_classes)
    print("[对账] 报告：{}".format(totals.describe()))
    print("[对账] 源码里应执行的测试类 {} 个".format(len(classes)))
    if not problems:
        print("[对账] 通过")
        return EXIT_OK
    for problem in problems:
        print("[对账] 不通过：{}".format(problem), file=sys.stderr)
    return EXIT_MISMATCH


if __name__ == "__main__":
    sys.exit(main())
