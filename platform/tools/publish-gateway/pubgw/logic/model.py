"""一份 v2 定义里所有表达式出现的位置（site），以及共用的引用作用域。"""

from dataclasses import dataclass
from typing import Dict, Iterator, Optional, Set

from . import ast
from .parser import ExpressionSyntaxError, parse_expression, parse_template
from .scope import CHOICE, TYPE_ANY, TYPE_TEXT, Scope, Type
from .types import CALCULATION, CONDITION, GROUP_CONDITION, PIPE, VALIDATION, Context, check_expression
from ..model import Group, Question, SurveyDefinition


@dataclass(frozen=True)
class Site:
    """一处表达式或模板。is_template 为真时 source 是带 {{ }} 的文本。"""

    path: str
    source: str
    kind: str
    group_index: int
    question: Optional[Question] = None
    is_template: bool = False

    @property
    def context(self) -> Context:
        return Context(self.kind, self.question)


class LogicModel:
    """作用域 ＋ 计算值类型的惰性推导（带递归保护，循环依赖由 graph 另行报告）。"""

    def __init__(self, definition: SurveyDefinition):
        self.definition = definition
        self._calculated: Dict[str, Type] = {}
        self._in_progress: Set[str] = set()
        self.scope = Scope(definition, self.calculated_type)

    def calculated_type(self, question: Question) -> Type:
        known = self._calculated.get(question.uuid)
        if known is not None:
            return known
        if question.uuid in self._in_progress or not question.calculation:
            return TYPE_ANY
        self._in_progress.add(question.uuid)
        try:
            node = parse_expression(question.calculation)
            result = check_expression(node, self.scope, Context(CALCULATION, question)).type
        except ExpressionSyntaxError:
            result = TYPE_ANY
        finally:
            self._in_progress.discard(question.uuid)
        if result.kind == CHOICE:
            result = TYPE_TEXT
        self._calculated[question.uuid] = result
        return result


def sites(definition: SurveyDefinition) -> Iterator[Site]:
    for group_index, group in enumerate(definition.groups):
        where = "groups[{}]".format(group_index)
        yield from _group_sites(group, group_index, where)
        for question_index, question in enumerate(group.questions):
            yield from _question_sites(question, group_index, "{}.questions[{}]".format(where, question_index))


def _group_sites(group: Group, group_index: int, where: str) -> Iterator[Site]:
    if group.condition:
        yield Site(where + ".condition", group.condition, GROUP_CONDITION, group_index)
    if group.description:
        yield Site(where + ".description", group.description, PIPE, group_index, is_template=True)


def _question_sites(question: Question, group_index: int, where: str) -> Iterator[Site]:
    if question.condition:
        yield Site(where + ".condition", question.condition, CONDITION, group_index, question)
    if question.calculation:
        yield Site(where + ".calculation", question.calculation, CALCULATION, group_index, question)
    if question.validation is not None:
        yield Site(where + ".validation.rule", question.validation.rule, VALIDATION, group_index, question)
        yield Site(where + ".validation.message", question.validation.message, PIPE, group_index, question, True)
    yield Site(where + ".text", question.text, PIPE, group_index, question, True)
    if question.help:
        yield Site(where + ".help", question.help, PIPE, group_index, question, True)
    for index, answer in enumerate(question.answers):
        yield Site("{}.answers[{}].text".format(where, index), answer.text, PIPE, group_index, question, True)
    for index, subquestion in enumerate(question.subquestions):
        yield Site("{}.subquestions[{}].text".format(where, index), subquestion.text, PIPE, group_index, question, True)


def expressions_of(site: Site):
    """一个 site 里的全部 (语法树, 模板占位符位置)；解析失败抛 ExpressionSyntaxError。"""

    if not site.is_template:
        return [parse_expression(site.source)]
    return [segment.expression for segment in parse_template(site.source) if isinstance(segment, ast.Placeholder)]
