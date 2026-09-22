"""v2 定义 → 引擎层定义：把逻辑编译进 relevance、题目属性与文本。

产物仍是 SurveyDefinition（不可变，全部经 dataclasses.replace 生成新对象），
LSS 编译器照旧处理它。v1 定义原样返回——同一个对象，逐字节不变。

落点：

- 题目 condition → ``questions.relevance``；题组 condition → ``groups.grelevance``；
- validation → 属性 ``em_validation_q``（未作答时放行）与 ``em_validation_q_tip``；
- calculation → 属性 ``equation``（``{表达式}``）、``hidden=1``（作者可覆盖）、
  数值结果再加 ``numbers_only=1``；
- 文本里的 ``{{ 表达式 }}`` → ``{表达式}``；文本里其余的 ``{`` ``}`` 一律写成
  ``&#123;`` ``&#125;``，引擎永远不会把作者的原文当成表达式执行。
"""

from dataclasses import replace
from typing import Dict, Optional

from . import ast
from .emit import Emitter
from .model import LogicModel
from .parser import parse_expression, parse_template
from .scope import NUMBER
from .types import CALCULATION, CONDITION, GROUP_CONDITION, VALIDATION
from ..model import AnswerOption, Group, Question, SubQuestion, SurveyDefinition

ALWAYS_RELEVANT = "1"


def escape_braces(text: str) -> str:
    return text.replace("{", "&#123;").replace("}", "&#125;")


class LogicCompiler:
    """把单个表达式／模板编译成 ExpressionScript。调用方保证定义已通过校验。"""

    def __init__(self, definition: SurveyDefinition):
        self._model = LogicModel(definition)

    def _owner(self, owner_uuid: Optional[str]) -> Optional[Question]:
        return self._model.scope.by_uuid.get(owner_uuid) if owner_uuid else None

    def expression(self, source: str, kind: str, owner_uuid: Optional[str] = None) -> str:
        owner = self._owner(owner_uuid)
        emitter = Emitter(self._model.scope, owner)
        compiled = emitter.emit(parse_expression(source))
        if kind == VALIDATION:
            own = self._model.scope.resolve_question(owner)
            return "(is_empty({}.NAOK) or {})".format(own.variable, compiled)
        return compiled

    def template(self, text: str, owner_uuid: Optional[str] = None) -> str:
        emitter = Emitter(self._model.scope, self._owner(owner_uuid))
        parts = []
        for segment in parse_template(text):
            if isinstance(segment, ast.Literal):
                parts.append(escape_braces(segment.text))
            else:
                parts.append("{" + emitter.emit(segment.expression) + "}")
        return "".join(parts)

    def calculated_type(self, question: Question) -> str:
        return self._model.calculated_type(question).kind


def lower_definition(definition: SurveyDefinition) -> SurveyDefinition:
    if not definition.has_logic:
        return definition
    compiler = LogicCompiler(definition)
    return replace(
        definition,
        title=escape_braces(definition.title),
        description=escape_braces(definition.description),
        groups=tuple(_lower_group(compiler, group) for group in definition.groups),
    )


def _lower_group(compiler: LogicCompiler, group: Group) -> Group:
    return replace(
        group,
        title=escape_braces(group.title),
        description=compiler.template(group.description),
        relevance=compiler.expression(group.condition, GROUP_CONDITION) if group.condition else ALWAYS_RELEVANT,
        condition="",
        questions=tuple(_lower_question(compiler, question) for question in group.questions),
    )


def _lower_question(compiler: LogicCompiler, question: Question) -> Question:
    owner = question.uuid
    relevance = compiler.expression(question.condition, CONDITION, owner) if question.condition else ALWAYS_RELEVANT
    return replace(
        question,
        text=compiler.template(question.text, owner),
        help=compiler.template(question.help, owner),
        relevance=relevance,
        attributes=_attributes(compiler, question),
        answers=tuple(
            AnswerOption(answer.code, compiler.template(answer.text, owner), answer.scale, answer.assessment_value)
            for answer in question.answers
        ),
        subquestions=tuple(
            SubQuestion(sub.uuid, sub.code, compiler.template(sub.text, owner), sub.scale)
            for sub in question.subquestions
        ),
        condition="",
        calculation="",
        validation=None,
    )


def _attributes(compiler: LogicCompiler, question: Question) -> Dict[str, str]:
    attributes = dict(question.attributes)
    if question.validation is not None:
        attributes["em_validation_q"] = compiler.expression(question.validation.rule, VALIDATION, question.uuid)
        if question.validation.message:
            attributes["em_validation_q_tip"] = compiler.template(question.validation.message, question.uuid)
    if question.calculation:
        attributes["equation"] = "{" + compiler.expression(question.calculation, CALCULATION, question.uuid) + "}"
        attributes.setdefault("hidden", "1")
        if compiler.calculated_type(question) == NUMBER:
            attributes["numbers_only"] = "1"
    return attributes


