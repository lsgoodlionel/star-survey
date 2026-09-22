"""把逻辑的全部静态检查汇总成 ValidationIssue：语法、类型、引用、顺序、循环，
以及 v2 定义里不允许出现的「直写引擎表达式」。
"""

from typing import List

from .graph import DependencyGraph, Problem, Reference
from .model import LogicModel, Site, expressions_of, sites
from .parser import ExpressionSyntaxError
from .types import check_expression
from ..model import Question, SurveyDefinition
from ..validate import ValidationIssue

CALCULATED_TYPE = "*"

#: 能设校验规则的题型：只有一列答案、值的含义明确。
VALIDATION_TYPES = frozenset({"L", "!", "S", "T", "U", "N", "D"})

#: 装着引擎表达式的题目属性。v2 里它们只能由编译器写，作者端直写一律拒绝。
RAW_EXPRESSION_ATTRIBUTES = frozenset(
    {
        "em_validation_q", "em_validation_q_tip", "em_validation_sq", "em_validation_sq_tip",
        "equation", "array_filter", "array_filter_exclude", "array_filter_style",
    }
)
_DEFAULT_RELEVANCE = "1"


def check_question_shape(question: Question, where: str) -> List[ValidationIssue]:
    """计算值与校验规则的结构约束；v1 定义同样适用（v1 里 * 题永远缺计算公式）。"""
    issues: List[ValidationIssue] = []
    if question.type == CALCULATED_TYPE:
        if not question.calculation:
            issues.append(ValidationIssue("E_CALCULATION_MISSING", where + ".calculation", "计算值题（*）必须给出 calculation"))
        if question.mandatory:
            issues.append(ValidationIssue("E_CALCULATION_MANDATORY", where + ".mandatory", "计算值由引擎写入，不能设为必答"))
    elif question.calculation:
        issues.append(
            ValidationIssue("E_CALCULATION_UNEXPECTED", where + ".calculation", "只有计算值题（*）可以带 calculation")
        )
    if question.validation is not None and question.type not in VALIDATION_TYPES:
        issues.append(
            ValidationIssue(
                "E_VALIDATION_UNSUPPORTED_TYPE",
                where + ".validation",
                "题型 {} 不支持校验规则（支持：{}）".format(question.type, " ".join(sorted(VALIDATION_TYPES))),
            )
        )
    return issues


def check_logic(definition: SurveyDefinition) -> List[ValidationIssue]:
    """v2 定义的逻辑检查。v1 定义不带逻辑，直接返回空。"""
    if not definition.has_logic:
        return []
    issues = _raw_expressions(definition)
    model = LogicModel(definition)
    references: List[Reference] = []
    for site in sites(definition):
        issues.extend(_check_site(model, site, references))
    graph = DependencyGraph(definition, references, model.scope.page)
    issues.extend(_issue(problem) for problem in graph.problems())
    return issues


def _check_site(model: LogicModel, site: Site, references: List[Reference]) -> List[ValidationIssue]:
    try:
        nodes = expressions_of(site)
    except ExpressionSyntaxError as error:
        return [ValidationIssue("E_EXPR_SYNTAX", site.path, str(error))]
    issues = []
    for node in nodes:
        analysis = check_expression(node, model.scope, site.context)
        issues.extend(
            _issue(Problem(finding.code, site.path, finding.message, finding.pos)) for finding in analysis.findings
        )
        references.extend(
            Reference(
                site.path,
                site.kind,
                site.group_index,
                site.question.uuid if site.question is not None else None,
                question.uuid,
                pos,
            )
            for question, pos in analysis.references
        )
    return issues


def _issue(problem: Problem) -> ValidationIssue:
    if problem.pos is None:
        return ValidationIssue(problem.code, problem.path, problem.message)
    return ValidationIssue(problem.code, problem.path, "{} (column {})".format(problem.message, problem.pos + 1))


def _raw_expressions(definition: SurveyDefinition) -> List[ValidationIssue]:
    issues = []
    message = "v2 定义不能直写引擎表达式（{}）；请改用 condition / validation / calculation"
    for group_index, group in enumerate(definition.groups):
        where = "groups[{}]".format(group_index)
        if group.relevance != _DEFAULT_RELEVANCE:
            issues.append(ValidationIssue("E_RAW_EXPRESSION", where + ".relevance", message.format("relevance")))
        for question_index, question in enumerate(group.questions):
            path = "{}.questions[{}]".format(where, question_index)
            if question.relevance != _DEFAULT_RELEVANCE:
                issues.append(ValidationIssue("E_RAW_EXPRESSION", path + ".relevance", message.format("relevance")))
            for name in sorted(RAW_EXPRESSION_ATTRIBUTES.intersection(question.attributes)):
                issues.append(
                    ValidationIssue("E_RAW_EXPRESSION", "{}.attributes.{}".format(path, name), message.format(name))
                )
    return issues
