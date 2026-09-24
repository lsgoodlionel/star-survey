"""WP-03.4 双执行比对：平台自己算一遍，和真引擎算的逐条比。

两次执行：

1. **平台**：evaluate.py 直接在 DSL 语法树上按契约语义求值；
2. **引擎**：真 LimeSurvey 的 ExpressionManager 跑 emit.py 编译出来的
   ExpressionScript（platform/tests/e2e/logic_parity.php 注入同一份答案）。

两边都渲染成同一种文本形式再比。**分歧必须能指出是哪一侧错的**，靠的是用例
自带的期望值（``AnswerVector.expected``，照契约人工写下来的第三方裁判）：

===================  ==========================================
情况                 归责
===================  ==========================================
引擎报错             compiler（我们编出了引擎跑不了的表达式）
两边一致且合期望     none
两边一致但不合期望   both（编译器与解释器一起错，或契约本身错）
分歧，引擎合期望     platform（解释器错）
分歧，平台合期望     compiler（编译器错）
分歧，都不合期望     both
分歧，没有期望值     unknown（必须人工裁决，不许当成「通过」）
===================  ==========================================
"""

from dataclasses import dataclass, field, replace
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import ast
from .emit import Emitter
from .evaluate import EMPTY, AnswerSheet, Interpreter, render
from .model import LogicModel, Site, sites
from .parser import parse_expression, parse_template
from .types import VALIDATION
from ..model import SurveyDefinition

BLAME_NONE = "none"
BLAME_PLATFORM = "platform"
BLAME_COMPILER = "compiler"
BLAME_BOTH = "both"
BLAME_UNKNOWN = "unknown"


@dataclass(frozen=True)
class AnswerVector:
    """一份答案向量：答卷里的值、被隐藏的题，以及按 site 路径写下的期望值。"""

    name: str
    answers: Mapping[str, str] = field(default_factory=dict)
    hidden: Tuple[str, ...] = ()
    expected: Mapping[str, str] = field(default_factory=dict)

    @property
    def sheet(self) -> AnswerSheet:
        return AnswerSheet(dict(self.answers), frozenset(self.hidden))


@dataclass(frozen=True)
class ParityCase:
    """一条比对用例：同一处表达式、同一份答案，两边各算一次。"""

    vector: str
    path: str
    kind: str
    source: str
    compiled: str
    platform: str
    answers: Mapping[str, str]
    hidden: Tuple[str, ...]
    expected: Optional[str] = None

    @property
    def id(self) -> str:
        return "{}|{}".format(self.vector, self.path)

    def replace(self, **changes) -> "ParityCase":
        return replace(self, **changes)

    def to_plan(self) -> Dict[str, object]:
        """交给引擎那一侧的载荷（platform/tests/e2e/logic_parity.php）。"""
        return {
            "id": self.id,
            "expression": self.compiled,
            "answers": dict(self.answers),
            "hidden": list(self.hidden),
        }


@dataclass(frozen=True)
class Verdict:
    case: ParityCase
    engine: str
    errors: Tuple[str, ...]
    agree: bool
    blame: str

    def row(self) -> Tuple[str, ...]:
        """证据表的一行。"""
        return (
            self.case.vector,
            self.case.path,
            self.case.source,
            _show(self.case.platform),
            _show(self.engine),
            _show(self.case.expected) if self.case.expected is not None else "-",
            self.blame,
        )


def build_cases(definition: SurveyDefinition, vectors: Sequence[AnswerVector]) -> List[ParityCase]:
    """定义里每一处表达式 × 每一份答案向量 = 一条用例。"""
    model = LogicModel(definition)
    cases: List[ParityCase] = []
    for site in sites(definition):
        for path, node in _expressions_of(site):
            for vector in vectors:
                cases.append(_case(model, site, path, node, vector))
    return cases


def adjudicate(case: ParityCase, engine: str, errors: Iterable[str]) -> Verdict:
    """比对一条用例，并在分歧时指出是哪一侧错了。"""
    errors = tuple(errors)
    if errors:
        # 引擎连解析／求值都做不到，只能是我们编出来的表达式有问题。
        return Verdict(case, engine, errors, False, BLAME_COMPILER)
    agree = case.platform == engine
    if case.expected is None:
        return Verdict(case, engine, errors, agree, BLAME_NONE if agree else BLAME_UNKNOWN)
    platform_right = case.platform == case.expected
    engine_right = engine == case.expected
    if platform_right and engine_right:
        return Verdict(case, engine, errors, True, BLAME_NONE)
    if engine_right:
        return Verdict(case, engine, errors, False, BLAME_PLATFORM)
    if platform_right:
        return Verdict(case, engine, errors, False, BLAME_COMPILER)
    return Verdict(case, engine, errors, False, BLAME_BOTH)


def summarize(verdicts: Sequence[Verdict]) -> Dict[str, int]:
    counts = {"cases": len(verdicts), "adjudicated": 0, "agreed": 0}
    for blame in (BLAME_NONE, BLAME_PLATFORM, BLAME_COMPILER, BLAME_BOTH, BLAME_UNKNOWN):
        counts[blame] = 0
    for verdict in verdicts:
        counts[verdict.blame] += 1
        counts["agreed"] += 1 if verdict.agree else 0
        counts["adjudicated"] += 1 if verdict.case.expected is not None else 0
    return counts


# ------------------------------------------------------------------ 内部


def _expressions_of(site: Site) -> List[Tuple[str, ast.Node]]:
    """一处 site 里的全部表达式；模板的每个占位符各算一条，路径带序号。"""
    if not site.is_template:
        return [(site.path, parse_expression(site.source))]
    placeholders = [
        segment for segment in parse_template(site.source) if isinstance(segment, ast.Placeholder)
    ]
    return [
        ("{}{{{{{}}}}}".format(site.path, index), placeholder.expression)
        for index, placeholder in enumerate(placeholders)
    ]


def _case(model: LogicModel, site: Site, path: str, node: ast.Node, vector: AnswerVector) -> ParityCase:
    owner = site.question
    emitter = Emitter(model.scope, owner)
    interpreter = Interpreter(model.scope, owner)
    compiled = emitter.emit(node)
    value = interpreter.evaluate(node, vector.sheet)
    if site.kind == VALIDATION and owner is not None:
        # 校验规则在引擎里是「没作答就放行」，比对也要比这个包好的形态。
        own = model.scope.resolve_question(owner)
        compiled = "(is_empty({}.NAOK) or {})".format(own.variable, compiled)
        if vector.sheet.raw(own) == "":
            value = True
    return ParityCase(
        vector=vector.name,
        path=path,
        kind=site.kind,
        source=site.source if not site.is_template else _slice(site, path),
        compiled=compiled,
        platform=render(value),
        answers=dict(vector.answers),
        hidden=tuple(vector.hidden),
        expected=vector.expected.get(path),
    )


def _slice(site: Site, path: str) -> str:
    """模板用例的 source 显示成占位符本身，证据表才看得懂。"""
    index = int(path[len(site.path) + 2 : -2])
    placeholders = [
        segment for segment in parse_template(site.source) if isinstance(segment, ast.Placeholder)
    ]
    start = placeholders[index].pos
    end = site.source.index("}}", start) + 2
    return site.source[start:end]


def _show(value: str) -> str:
    return "∅" if value == "" else value


__all__ = [
    "BLAME_BOTH",
    "BLAME_COMPILER",
    "BLAME_NONE",
    "BLAME_PLATFORM",
    "BLAME_UNKNOWN",
    "AnswerVector",
    "ParityCase",
    "Verdict",
    "adjudicate",
    "build_cases",
    "summarize",
]
