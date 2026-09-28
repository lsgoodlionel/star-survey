"""需求编号索引：仓库内快照，以及与仓库外那份 02 矩阵的对账。

为什么要在仓库里留一份快照：需求矩阵
`02-全量需求追踪矩阵.md` 不在本仓库里（在方案文档目录），CI 的检出里根本没有它。
若校验直接去读那个绝对路径，CI 只能跳过——那就等于没有校验。所以把「有哪些编号」
物化成 `platform/docs/traceability/requirement-index.md`，连矩阵原文的 SHA-256 一起写下：

* CI（矩阵不可达）：编号一律按快照校验，能抓住拼错与凭空发明的编号；
* 本地（矩阵可达）：额外把快照与矩阵重新对一遍，矩阵改了而快照没跟上就报错。

快照本身不许手改——它由矩阵生成，改矩阵后用 `python3 -m reqtrace.cli sync` 重生成。
"""

import hashlib
import os
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

#: 需求编号的形状：`R` ＋ 两位工作包号 ＋ `-` ＋ 两位序号。全仓通用，勿放宽。
REQUIREMENT_PATTERN = re.compile(r"R\d{2}-\d{2}")

#: 矩阵的行形状：`| R01-02 | 批量文本导入 | C | …`——功能要求在第二列。
_MATRIX_ROW = re.compile(r"^\|\s*(R\d{2}-\d{2})\s*\|([^|]*)\|")

#: 快照的行形状：`| R01-02 | WP-01 | 批量文本导入 |`——中间多一列工作包，功能要求在第三列。
#: 两个形状必须分开写：共用一个正则会把工作包当成功能要求，两侧永远对不上。
_INDEX_ROW = re.compile(r"^\|\s*(R\d{2}-\d{2})\s*\|\s*WP-\d{2}\s*\|([^|]*)\|")

#: 快照头部记录矩阵原文的摘要，用来判断快照是不是过期了。
_DIGEST_LINE = re.compile(r"^-\s*矩阵原文 SHA-256：`([0-9a-f]{64})`\s*$", re.M)

#: 方案文档不在仓库里。默认路径与 `platform/README.md` 里写的是同一处；
#: 换机器或换目录时用环境变量覆盖，不要改代码。
MATRIX_ENV = "REQUIREMENT_MATRIX"
DEFAULT_MATRIX = Path(
    "/Users/lionel/Documents/MJY 2/LimeSurvey本土化方案/02-全量需求追踪矩阵.md"
)


class Requirement(NamedTuple):
    """索引里的一行：编号、所属工作包、功能要求（矩阵第二列的原文）。"""

    id: str
    work_package: str
    title: str


class Index(NamedTuple):
    """一份需求编号索引。``digest`` 是生成它时矩阵原文的 SHA-256。"""

    requirements: Tuple[Requirement, ...]
    digest: Optional[str]

    @property
    def ids(self) -> Tuple[str, ...]:
        return tuple(item.id for item in self.requirements)

    def by_id(self) -> Dict[str, Requirement]:
        return {item.id: item for item in self.requirements}


def _rows(text: str, row: "re.Pattern[str]") -> List[Requirement]:
    """按表格行取出编号与功能要求。矩阵总览表里没有编号列，天然不会被误读。"""
    found: List[Requirement] = []
    for line in text.splitlines():
        match = row.match(line)
        if match is None:
            continue
        requirement_id = match.group(1)
        found.append(
            Requirement(
                id=requirement_id,
                work_package="WP-" + requirement_id[1:3],
                title=match.group(2).strip(),
            )
        )
    return found


def _check_unique(requirements: List[Requirement], source: str) -> None:
    counts = Counter(item.id for item in requirements)
    duplicated = sorted(rid for rid, times in counts.items() if times > 1)
    if duplicated:
        raise ValueError("{} 里有重复的需求编号：{}".format(source, "、".join(duplicated)))


def parse_matrix(text: str) -> Index:
    """解析 02 矩阵原文。摘要就是原文自身的 SHA-256。"""
    requirements = _rows(text, _MATRIX_ROW)
    if not requirements:
        raise ValueError("矩阵里一行需求都没解析出来，格式大概变了")
    _check_unique(requirements, "矩阵")
    return Index(tuple(requirements), hashlib.sha256(text.encode("utf-8")).hexdigest())


def parse_index(text: str) -> Index:
    """解析仓库内快照。头部那行摘要缺了也能解析，但对账会因此判不出过期。"""
    requirements = _rows(text, _INDEX_ROW)
    if not requirements:
        raise ValueError("快照里一行需求都没解析出来，格式大概坏了")
    _check_unique(requirements, "快照")
    digest_match = _DIGEST_LINE.search(text)
    return Index(tuple(requirements), digest_match.group(1) if digest_match else None)


def matrix_path() -> Path:
    """矩阵原文的位置：环境变量优先，否则用默认路径。"""
    override = os.environ.get(MATRIX_ENV)
    return Path(override) if override else DEFAULT_MATRIX


def load_matrix() -> Optional[Index]:
    """读矩阵原文；读不到（CI 的检出里就是读不到）返回 ``None``。"""
    path = matrix_path()
    if not path.is_file():
        return None
    return parse_matrix(path.read_text(encoding="utf-8"))


def render_index(matrix: Index) -> str:
    """把矩阵渲染成仓库内快照。``sync`` 子命令写出来的就是这段。"""
    lines = [
        "# 需求编号索引（由 02 矩阵生成，勿手改）",
        "",
        "本文件是 `02-全量需求追踪矩阵.md` 里**需求编号**的仓库内快照。",
        "存在的唯一理由：矩阵不在本仓库，CI 的检出里读不到它，而编号校验必须在 CI 里生效。",
        "",
        "- 矩阵原文 SHA-256：`{}`".format(matrix.digest),
        "- 条目数：{}".format(len(matrix.requirements)),
        "",
        "改了矩阵之后重生成：",
        "",
        "```",
        "cd platform/tools/traceability && python3 -m reqtrace.cli sync",
        "```",
        "",
        "| 需求ID | 工作包 | 功能要求 |",
        "|---|---|---|",
    ]
    lines.extend(
        "| {} | {} | {} |".format(item.id, item.work_package, item.title)
        for item in matrix.requirements
    )
    return "\n".join(lines) + "\n"


def reconcile(snapshot: Index, matrix: Index) -> List[str]:
    """快照与矩阵的差异，每条一句人话。空列表＝一致。"""
    problems: List[str] = []
    if snapshot.digest != matrix.digest:
        problems.append(
            "快照记的矩阵摘要是 {}，矩阵现在是 {}——矩阵改过而快照没重生成".format(
                snapshot.digest, matrix.digest
            )
        )
    missing = [item for item in matrix.requirements if item.id not in set(snapshot.ids)]
    if missing:
        problems.append("矩阵有而快照没有：{}".format("、".join(item.id for item in missing)))
    extra = [item for item in snapshot.requirements if item.id not in set(matrix.ids)]
    if extra:
        problems.append("快照有而矩阵没有：{}".format("、".join(item.id for item in extra)))
    snapshot_titles = snapshot.by_id()
    for item in matrix.requirements:
        mirrored = snapshot_titles.get(item.id)
        if mirrored is not None and mirrored.title != item.title:
            problems.append(
                "{} 的功能要求不一致：快照「{}」／矩阵「{}」".format(
                    item.id, mirrored.title, item.title
                )
            )
    return problems
