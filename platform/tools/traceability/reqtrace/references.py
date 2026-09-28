"""全仓扫描：自有源码与文档里出现的每个需求编号。

这一层管的是**已经存在的那批零散注释**（约 160 个自有文件、90 多个编号）。它们是本项目
标注需求归属的既有写法，没有另造一套语法去替换；这里只加一道闸门：编号必须真的在
02 矩阵里。凭空发明或手抖写错的编号（`R02-84`、`R2-04`）在评审时基本看不出来，
在这里一眼就红。

上游 LimeSurvey 自带的文件不在管辖范围内——同 `test_script_modes.py` 的理由（见
`docs/p0/upstream-diff.md`：自有内容全是新增文件）。
"""

import re
import subprocess
from pathlib import Path
from typing import Dict, List, Sequence, Set

from .index import REQUIREMENT_PATTERN

#: 自有路径前缀。`themes/survey/zh-business` 是本项目的引擎主题，别漏。
OWNED_PREFIXES = (
    "platform/",
    "plugins/Mjy",
    "themes/question/mjy-",
    "themes/survey/zh-business/",
)

#: 只扫这些后缀：需求编号写在代码注释、迁移脚本与文档里，不会写在图片或锁文件里。
SCANNED_SUFFIXES = (".py", ".java", ".php", ".sh", ".md", ".sql", ".yml", ".yaml", ".twig", ".css", ".js")

#: 追溯工具自己与它的两份数据文件不在扫描范围内，两个理由：
#:
#: * 快照与登记表本来就通篇是需求编号，扫它们等于自证；
#: * 工具的测试与注释里**故意**放着不存在的编号（`R01-84` 之类）来证明这道闸门会红。
#:   把反例当成违规报出来，就没法用反例来证明校验有效了。
#:
#: 代价：`platform/tools/traceability/` 里提到的真实编号不受这道闸门管。范围刻意收得很窄
#: ——除了这两个目录，`platform/`、`plugins/Mjy` 下别的地方都照扫。
EXEMPT_PREFIXES = (
    "platform/tools/traceability/",
    "platform/docs/traceability/",
)


def owned_files(repo_root: Path) -> List[str]:
    """git 索引里本项目自有、且后缀在扫描范围内的文件。"""
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=str(repo_root), capture_output=True, text=True, check=True,
    ).stdout
    return [
        path for path in out.splitlines()
        if path.startswith(OWNED_PREFIXES)
        and path.endswith(SCANNED_SUFFIXES)
        and not path.startswith(EXEMPT_PREFIXES)
    ]


def scan(repo_root: Path, paths: Sequence[str]) -> Dict[str, List[str]]:
    """编号 → 提到它的文件列表。读不了的文件（二进制混入）跳过而不是让整轮挂掉。"""
    found: Dict[str, List[str]] = {}
    for path in paths:
        target = repo_root / path
        if not target.is_file():
            continue
        try:
            text = target.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for requirement_id in sorted(set(REQUIREMENT_PATTERN.findall(text))):
            found.setdefault(requirement_id, []).append(path)
    return found


def unknown_references(referenced: Dict[str, List[str]], known: Set[str]) -> List[str]:
    """不在索引里的引用，每条一句人话。"""
    return [
        "{} 不是矩阵里的需求编号，被这些文件引用：{}".format(
            requirement_id, "、".join(files[:5]) + ("…" if len(files) > 5 else "")
        )
        for requirement_id, files in sorted(referenced.items())
        if requirement_id not in known
    ]
