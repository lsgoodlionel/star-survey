"""把各层拼成一次完整校验。

六道闸门，每一道对应一种**真实会发生**的漂移：

1. 登记表格式：列数、层级用词、定位符形状；
2. 登记的需求编号必须在索引里（写错编号 → 这条证据挂在不存在的需求上）；
3. 登记的定位符必须指到真实存在的测试（测试改名／删掉 → 声称的覆盖变成空话）；
4. 自有源码里出现的需求编号必须在索引里（零散注释里的拼写错）；
5. 题型主题声明的需求必须有测试证据（新主题只写实现不写证据）；
6. 仓库内索引快照必须与仓库外的 02 矩阵一致（矩阵改了快照没跟上）。

第 6 道在矩阵读不到时**跳过**——CI 的检出里没有方案文档目录。前 5 道任何环境都生效。
"""

from pathlib import Path
from typing import Any, List, NamedTuple

from . import code_claims, references
from .index import Index, load_matrix, parse_index, reconcile
from .locators import resolve_all
from .registry import covered_requirements, duplicate_entries, parse_registry

INDEX_FILE = Path("platform/docs/traceability/requirement-index.md")
REGISTRY_FILE = Path("platform/docs/traceability/requirement-tests.md")

#: ``run(matrix=...)`` 的「没给」与「给了 None」是两回事：没给＝自己去磁盘找，
#: 给了 None＝调用方已经确认读不到（测试要模拟 CI 就靠这个区分）。
_UNSET = object()


class Report(NamedTuple):
    """一次校验的结果。``problems`` 空＝通过。"""

    problems: List[str]
    requirement_count: int
    entry_count: int
    covered_count: int
    referenced_count: int
    matrix_checked: bool

    @property
    def is_clean(self) -> bool:
        return not self.problems


def load_index(repo_root: Path) -> Index:
    return parse_index((repo_root / INDEX_FILE).read_text(encoding="utf-8"))


def run(repo_root: Path, matrix: Any = _UNSET) -> Report:
    """跑完六道闸门。

    ``matrix`` 不传＝自己去磁盘找 02 矩阵；传 ``Index``＝用这一份（测试伪造矩阵）；
    传 ``None``＝调用方已确认读不到，第六道闸门跳过（测试模拟 CI 的检出）。
    """
    index = load_index(repo_root)
    known = set(index.ids)
    problems: List[str] = []

    entries, format_problems = parse_registry((repo_root / REGISTRY_FILE).read_text(encoding="utf-8"))
    problems.extend(format_problems)
    problems.extend(duplicate_entries(entries))

    for entry in entries:
        if entry.requirement_id not in known:
            problems.append(
                "登记表第 {} 行的 {} 不是矩阵里的需求编号".format(entry.line, entry.requirement_id)
            )

    problems.extend(resolve_all([entry.locator for entry in entries], repo_root))

    referenced = references.scan(repo_root, references.owned_files(repo_root))
    problems.extend(references.unknown_references(referenced, known))

    covered = covered_requirements(entries)
    claims = code_claims.theme_requirements(repo_root)
    if len(claims) < code_claims.EXPECTED_AT_LEAST:
        problems.append(
            "只从 {} 里读到 {} 个题型主题的需求归属（至少应有 {}）——正则大概不再匹配了".format(
                code_claims.THEME_SPECS, len(claims), code_claims.EXPECTED_AT_LEAST
            )
        )
    problems.extend(code_claims.missing_evidence(claims, covered))

    if matrix is _UNSET:
        matrix = load_matrix()
    if matrix is not None:
        problems.extend(reconcile(index, matrix))

    return Report(
        problems=problems,
        requirement_count=len(index.requirements),
        entry_count=len(entries),
        covered_count=len(covered),
        referenced_count=len(referenced),
        matrix_checked=matrix is not None,
    )
