"""命令行入口。

    cd platform/tools/traceability
    python3 -m reqtrace.cli check     # 六道闸门，非零退出＝有漂移
    python3 -m reqtrace.cli sync      # 按 02 矩阵重生成仓库内索引快照
    python3 -m reqtrace.cli report    # 按工作包列出覆盖情况（只读，供人看）

退出码：0 通过；2 有漂移；3 用法或环境问题（如 sync 读不到矩阵）。
"""

import argparse
import sys
from pathlib import Path
from typing import List

from . import check, references
from .index import MATRIX_ENV, load_matrix, matrix_path, render_index
from .registry import covered_requirements, parse_registry

#: 本文件在 platform/tools/traceability/reqtrace/ 下，往上四层是仓库根。
REPO_ROOT = Path(__file__).resolve().parents[4]

_EXIT_DRIFT = 2
_EXIT_USAGE = 3


def _check() -> int:
    report = check.run(REPO_ROOT)
    print("需求条目 {}｜登记证据 {} 条，覆盖 {} 条需求｜自有源码引用到 {} 个编号".format(
        report.requirement_count, report.entry_count, report.covered_count, report.referenced_count
    ))
    print("与 02 矩阵对账：{}".format(
        "已对" if report.matrix_checked else "跳过（{} 读不到，设 {} 指过去）".format(
            matrix_path(), MATRIX_ENV
        )
    ))
    if report.is_clean:
        print("需求追溯校验通过")
        return 0
    print("\n发现 {} 处问题：".format(len(report.problems)), file=sys.stderr)
    for problem in report.problems:
        print("  - " + problem, file=sys.stderr)
    return _EXIT_DRIFT


def _sync() -> int:
    matrix = load_matrix()
    if matrix is None:
        print("读不到 02 矩阵（{}）；设 {} 指过去".format(matrix_path(), MATRIX_ENV), file=sys.stderr)
        return _EXIT_USAGE
    target = REPO_ROOT / check.INDEX_FILE
    rendered = render_index(matrix)
    if target.is_file() and target.read_text(encoding="utf-8") == rendered:
        print("索引快照已是最新（{} 条）".format(len(matrix.requirements)))
        return 0
    target.write_text(rendered, encoding="utf-8")
    print("已重生成 {}（{} 条）".format(check.INDEX_FILE, len(matrix.requirements)))
    return 0


def _report() -> int:
    index = check.load_index(REPO_ROOT)
    entries, _ = parse_registry((REPO_ROOT / check.REGISTRY_FILE).read_text(encoding="utf-8"))
    covered = covered_requirements(entries)
    referenced = references.scan(REPO_ROOT, references.owned_files(REPO_ROOT))
    lines: List[str] = ["| 工作包 | 条目数 | 有测试证据 | 仅有代码引用 |", "|---|---:|---:|---:|"]
    packages = sorted({item.work_package for item in index.requirements})
    for package in packages:
        ids = [item.id for item in index.requirements if item.work_package == package]
        with_tests = [rid for rid in ids if rid in covered]
        mentioned = [rid for rid in ids if rid not in covered and rid in referenced]
        lines.append("| {} | {} | {} | {} |".format(package, len(ids), len(with_tests), len(mentioned)))
    total_tests = len([item.id for item in index.requirements if item.id in covered])
    lines.append("| **合计** | {} | {} | {} |".format(
        len(index.requirements), total_tests,
        len([i.id for i in index.requirements if i.id not in covered and i.id in referenced]),
    ))
    print("\n".join(lines))
    return 0


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="需求追溯校验")
    parser.add_argument("command", choices=("check", "sync", "report"))
    args = parser.parse_args(argv)
    return {"check": _check, "sync": _sync, "report": _report}[args.command]()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
