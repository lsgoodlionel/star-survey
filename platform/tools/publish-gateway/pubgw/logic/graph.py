"""依赖图：循环依赖与引用顺序。

节点是题目（uuid）与题组（``group:<uuid>``）。边表示「我的取值或可见性取决于它」：

- 题目 → 它的显示条件、计算公式引用到的题目；
- 题目 → 它所在的题组（题组被隐藏，组内每道题都被隐藏、清值）；
- 题组 → 题组条件引用到的题目。

校验规则和文本引用不改变任何取值，所以不进图，只查顺序。
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .types import CALCULATION, CONDITION, GROUP_CONDITION, VALIDATION
from ..model import SurveyDefinition

_VALUE_KINDS = frozenset({CONDITION, CALCULATION, GROUP_CONDITION})
#: 校验提示显示在题目自己下方，可以引用这道题自己的答案。
_VALIDATION_MESSAGE = ".validation.message"


@dataclass(frozen=True)
class Reference:
    """site（path/kind/所属题目或题组）对某道题的一次引用。"""

    path: str
    kind: str
    group_index: int
    source_uuid: Optional[str]
    target_uuid: str
    pos: int


@dataclass(frozen=True)
class Problem:
    code: str
    path: str
    message: str
    pos: Optional[int] = None


class DependencyGraph:
    def __init__(self, definition: SurveyDefinition, references: Sequence[Reference], page: Dict[str, int]):
        self._definition = definition
        self._references = references
        self._page = page
        self._names: Dict[str, str] = {}
        self._order: Dict[str, int] = {}
        self._group_of: Dict[str, int] = {}
        self._group_page: Dict[int, int] = {}
        self._edges: Dict[str, List[Tuple[str, Optional[str]]]] = {}
        self._build()

    def _build(self) -> None:
        position = 0
        for group_index, group in enumerate(self._definition.groups):
            group_node = _group_node(group.uuid)
            self._names[group_node] = 'group "{}"'.format(group.title)
            self._order[group_node] = position
            self._edges.setdefault(group_node, [])
            position += 1
            for question in group.questions:
                self._names[question.uuid] = question.code
                self._order[question.uuid] = position
                self._group_of[question.uuid] = group_index
                self._group_page.setdefault(group_index, self._page.get(question.uuid, group_index))
                self._edges.setdefault(question.uuid, []).append((group_node, None))
                position += 1
        for reference in self._references:
            if reference.kind in _VALUE_KINDS:
                self._edges[self._source_node(reference)].append((reference.target_uuid, reference.path))

    def _source_node(self, reference: Reference) -> str:
        if reference.source_uuid is not None:
            return reference.source_uuid
        return _group_node(self._definition.groups[reference.group_index].uuid)

    # ------------------------------------------------------------- 循环

    def problems(self) -> List[Problem]:
        components = _strongly_connected(self._edges, sorted(self._edges, key=self._order.get))
        cyclic: Dict[str, int] = {}
        problems: List[Problem] = []
        for index, component in enumerate(components):
            members = set(component)
            if len(component) == 1 and not any(target == component[0] for target, _ in self._edges[component[0]]):
                continue
            for node in component:
                cyclic[node] = index
            problems.append(self._cycle_problem(members))
        problems.extend(self._order_problems(cyclic))
        return problems

    def _cycle_problem(self, members: Set[str]) -> Problem:
        start = min(members, key=self._order.get)
        path = _cycle_path(self._edges, start, members)
        names = " -> ".join(self._names[node] for node in path)
        first_edge = next(p for target, p in self._edges[start] if target == path[1] and p is not None) if len(path) > 1 else None
        where = first_edge or next((p for _, p in self._edges[start] if p), "")
        return Problem("E_EXPR_CYCLE", where, "circular dependency: {}".format(names))

    # ------------------------------------------------------------- 顺序

    def _order_problems(self, cyclic: Dict[str, int]) -> List[Problem]:
        problems = []
        for reference in self._references:
            source = self._source_node(reference)
            target = reference.target_uuid
            if source in cyclic and cyclic.get(target) == cyclic[source]:
                continue
            problem = self._order_problem(reference)
            if problem is not None:
                problems.append(problem)
        return problems

    def _order_problem(self, reference: Reference) -> Optional[Problem]:
        target = reference.target_uuid
        name = self._names[target]
        if reference.source_uuid is None:
            if self._group_of[target] < reference.group_index:
                return None
            source_page = self._group_page.get(reference.group_index, reference.group_index)
            return self._late(reference, name, self._page[target] > source_page, "its own group")
        source = reference.source_uuid
        if target == source:
            if reference.kind == VALIDATION or reference.path.endswith(_VALIDATION_MESSAGE):
                return None
            return Problem("E_EXPR_FORWARD_REFERENCE", reference.path, "{} refers to itself".format(name), reference.pos)
        if self._order[target] < self._order[source]:
            return None
        return self._late(reference, name, self._page[target] > self._page[source], "a later question")

    def _late(self, reference: Reference, name: str, is_later_page: bool, what: str) -> Problem:
        if is_later_page:
            return Problem(
                "E_EXPR_LATER_PAGE",
                reference.path,
                "{} is on a later page; its answer is not known yet".format(name),
                reference.pos,
            )
        return Problem(
            "E_EXPR_FORWARD_REFERENCE",
            reference.path,
            "{} is {} on the same page; only earlier questions may be referenced".format(name, what),
            reference.pos,
        )


def _group_node(uuid: str) -> str:
    return "group:" + uuid


def _strongly_connected(edges: Dict[str, List[Tuple[str, Optional[str]]]], nodes: List[str]) -> List[List[str]]:
    """Tarjan，迭代实现（不受 Python 递归深度限制）。"""
    index_of: Dict[str, int] = {}
    low: Dict[str, int] = {}
    on_stack: Set[str] = set()
    stack: List[str] = []
    components: List[List[str]] = []
    counter = 0
    for root in nodes:
        if root in index_of:
            continue
        work = [(root, 0)]
        while work:
            node, child = work.pop()
            if child == 0:
                index_of[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            targets = [target for target, _ in edges.get(node, []) if target in edges]
            if child < len(targets):
                work.append((node, child + 1))
                target = targets[child]
                if target not in index_of:
                    work.append((target, 0))
                elif target in on_stack:
                    low[node] = min(low[node], index_of[target])
                continue
            if low[node] == index_of[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(component)
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
    return components


def _cycle_path(edges, start: str, members: Set[str]) -> List[str]:
    """在一个强连通分量里找一条从 start 出发回到 start 的路径（BFS，最短）。"""
    previous: Dict[str, str] = {}
    frontier = [start]
    while frontier:
        following = []
        for node in frontier:
            for target, _ in edges[node]:
                if target == start:
                    path = [start]
                    cursor = node
                    trail = []
                    while cursor != start:
                        trail.append(cursor)
                        cursor = previous[cursor]
                    return path + list(reversed(trail)) + [start]
                if target in members and target not in previous and target != start:
                    previous[target] = node
                    following.append(target)
        frontier = following
    return [start]
