"""WP-03.3 计分：把作者写的计分表展开成 v2 的逻辑 DSL。

**这里没有第二套编译器。** 计分只负责生成 DSL 源码，生成完就交给 03.1／03.2
已有的 parser → types → graph → emit 走完全程：

- 总分 → 一道计算值题（``*``），公式形如
  ``sum(if(QPET == "A1", 3, 0), ..., coalesce(QAGE, 0) * 2)``；
- 分段 → 又一道计算值题（``<CODE>B``），公式是按上界嵌套的 ``if``，值是分段代码；
- 每个带文案的分段 → 一道说明题（``X``），条件是 ``<CODE>B == "<分段代码>"``，
  于是「按分数分支／展示结果」就是普通的 v2 条件，不需要新机制。

生成的题追加在一个**新的末尾题组**里：它不带条件，分数不会被别人的题组条件清空；
排在最后也让「只能引用前面的题」这条规则自动成立。

**作者文本的安全性**：生成的表达式里只有数字、题目引用，以及两种由平台校验过
字符集的代码（选项／子题代码、分段代码），**没有一处是作者的自由文本**。
作者的自由文本（分数标题、分段文案）全部落在题目的 text 上，由 03.2 已有的
模板路径转义（``{`` ``}`` → ``&#123;`` ``&#125;``），永远不会变成可执行表达式。
"""

from dataclasses import replace
from typing import List, Optional, Tuple

from .model import LogicModel
from .scope import CHOICE, NUMBER, SET, ResolutionError, Resolved, Scope
from . import ast
from ..codes import ANSWER_CODE_MAX_LENGTH, check_answer_code, check_question_code
from ..model import Group, Question, Score, ScoreBand, ScoreItem, SurveyDefinition
from ..validate import ValidationIssue

#: 总分题的代码就是分数代码；分段题加后缀 B，分段文案题加 R1、R2……
#: 题目代码上限 20 字符（codes.py），给后缀留出余量。
SCORE_CODE_MAX_LENGTH = 16
BAND_SUFFIX = "B"
RESULT_SUFFIX = "R"

_SCORABLE_KINDS = (CHOICE, SET, NUMBER)


def check_scoring(definition: SurveyDefinition) -> List[ValidationIssue]:
    """计分表的语义校验。结构问题（缺字段、类型不对）已经在 model.py 拦掉了。"""
    if not definition.scoring:
        return []
    model = LogicModel(definition)
    issues: List[ValidationIssue] = []
    taken = {question.code for question in definition.questions()}
    for index, score in enumerate(definition.scoring):
        where = "scoring[{}]".format(index)
        issues.extend(_check_code(score, where, taken))
        for position, item in enumerate(score.items):
            issues.extend(_check_item(model.scope, item, "{}.items[{}]".format(where, position)))
        issues.extend(_check_bands(score.bands, where))
    return issues


def expand_scoring(definition: SurveyDefinition) -> SurveyDefinition:
    """把计分表展开成一个末尾题组。没有计分表时原样返回同一个对象。"""
    if not definition.scoring:
        return definition
    questions: List[Question] = []
    for score in definition.scoring:
        questions.extend(_questions_of(definition, score))
    group = Group(
        uuid="{}-scoring".format(definition.uuid),
        title=definition.scoring[0].group_title,
        questions=tuple(questions),
    )
    return replace(definition, groups=definition.groups + (group,), scoring=())


# ------------------------------------------------------------------ 校验


def _check_code(score: Score, where: str, taken: set) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    if check_question_code(score.code) is not None or len(score.code) > SCORE_CODE_MAX_LENGTH:
        issues.append(
            ValidationIssue(
                "E_SCORING_CODE",
                where + ".code",
                "分数代码 {!r} 必须是字母开头的字母数字串，且不超过 {} 个字符（要给分段题留后缀）".format(
                    score.code, SCORE_CODE_MAX_LENGTH
                ),
            )
        )
        return issues
    for code in _generated_codes(score):
        if code in taken:
            issues.append(
                ValidationIssue(
                    "E_SCORING_CODE_CONFLICT", where + ".code", "计分会生成题目代码 {}，它已经被占用".format(code)
                )
            )
        taken.add(code)
    return issues


def _generated_codes(score: Score) -> List[str]:
    codes = [score.code]
    if score.bands:
        codes.append(score.code + BAND_SUFFIX)
        codes.extend(
            "{}{}{}".format(score.code, RESULT_SUFFIX, position + 1)
            for position, band in enumerate(score.bands)
            if band.text
        )
    return codes


def _check_item(scope: Scope, item: ScoreItem, where: str) -> List[ValidationIssue]:
    try:
        resolved = _resolve(scope, item)
    except ResolutionError as error:
        code = (
            "E_SCORING_UNKNOWN_QUESTION"
            if error.code == "E_EXPR_UNKNOWN_REFERENCE"
            else "E_SCORING_ITEM_SHAPE"
        )
        return [ValidationIssue(code, where + ".question", error.message)]
    kind = resolved.type.kind
    if kind not in _SCORABLE_KINDS:
        return [
            ValidationIssue(
                "E_SCORING_ITEM_SHAPE", where + ".question",
                "题目 {} 的取值是 {}，不能计分（可计分：单选、数组行、多选、数值）".format(item.question, kind),
            )
        ]
    if kind == NUMBER:
        return _check_numeric_item(item, where)
    return _check_points_item(item, where, resolved)


def _check_numeric_item(item: ScoreItem, where: str) -> List[ValidationIssue]:
    if item.weight is None or item.points:
        return [
            ValidationIssue(
                "E_SCORING_ITEM_SHAPE", where, "数值题按 weight 计分（分数 = 答案 × weight），不要写 points"
            )
        ]
    return []


def _check_points_item(item: ScoreItem, where: str, resolved: Resolved) -> List[ValidationIssue]:
    if item.weight is not None or not item.points:
        return [
            ValidationIssue(
                "E_SCORING_ITEM_SHAPE", where, "按选项计分的题必须写 points（选项代码 → 分数），不要写 weight"
            )
        ]
    what = "子题" if resolved.type.kind == SET else "选项"
    return [
        ValidationIssue(
            "E_SCORING_UNKNOWN_KEY",
            "{}.points.{}".format(where, key),
            "{} 不是题目 {} 的{}代码（有效：{}）".format(key, item.question, what, ", ".join(sorted(resolved.type.domain))),
        )
        for key, _ in item.points
        if key not in resolved.type.domain
    ]


def _check_bands(bands: Tuple[ScoreBand, ...], where: str) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    seen = set()
    for index, band in enumerate(bands):
        path = "{}.bands[{}]".format(where, index)
        if check_answer_code(band.code) is not None:
            issues.append(
                ValidationIssue(
                    "E_SCORING_BAND_CODE", path + ".code",
                    "分段代码 {!r} 必须是不超过 {} 个字符的字母数字串".format(band.code, ANSWER_CODE_MAX_LENGTH),
                )
            )
        elif band.code in seen:
            issues.append(ValidationIssue("E_SCORING_BAND_CODE", path + ".code", "分段代码 {} 重复".format(band.code)))
        seen.add(band.code)
        issues.extend(_check_band_bound(bands, band, index, path))
    return issues


def _check_band_bound(bands, band: ScoreBand, index: int, path: str) -> List[ValidationIssue]:
    is_last = index == len(bands) - 1
    if is_last:
        if band.up_to is not None:
            return [ValidationIssue("E_SCORING_BAND_ORDER", path + ".upTo", "最后一个分段不写 upTo，表示「及以上」")]
        return []
    if band.up_to is None:
        return [ValidationIssue("E_SCORING_BAND_ORDER", path + ".upTo", "只有最后一个分段可以不写 upTo")]
    previous = bands[index - 1].up_to if index else None
    if previous is not None and band.up_to <= previous:
        return [
            ValidationIssue(
                "E_SCORING_BAND_ORDER", path + ".upTo",
                "分段上界必须递增：{} 不大于上一段的 {}".format(_number_text(band.up_to), _number_text(previous)),
            )
        ]
    return []


def _resolve(scope: Scope, item: ScoreItem) -> Resolved:
    by_uuid = item.question in scope.by_uuid or item.question in scope.subquestion_owner
    return scope.resolve(ast.Ref(item.question, by_uuid, item.member or None, None, 0))


# ------------------------------------------------------------------ 展开


def _questions_of(definition: SurveyDefinition, score: Score) -> List[Question]:
    questions = [
        Question(
            uuid=score.uuid,
            code=score.code,
            type="*",
            text=score.title,
            calculation=_total_expression(definition, score),
        )
    ]
    if not score.bands:
        return questions
    questions.append(
        Question(
            uuid="{}-band".format(score.uuid),
            code=score.code + BAND_SUFFIX,
            type="*",
            text=score.title,
            calculation=_band_expression(score),
        )
    )
    questions.extend(_result_questions(score))
    return questions


def _result_questions(score: Score) -> List[Question]:
    return [
        Question(
            uuid="{}-result-{}".format(score.uuid, position + 1),
            code="{}{}{}".format(score.code, RESULT_SUFFIX, position + 1),
            type="X",
            text=band.text,
            condition='{}{} == "{}"'.format(score.code, BAND_SUFFIX, band.code),
        )
        for position, band in enumerate(score.bands)
        if band.text
    ]


def _total_expression(definition: SurveyDefinition, score: Score) -> str:
    scope = LogicModel(definition).scope
    terms: List[str] = []
    for item in score.items:
        terms.extend(_terms_of(scope, item))
    return "sum({})".format(", ".join(terms))


def _terms_of(scope: Scope, item: ScoreItem) -> List[str]:
    reference = _reference_text(scope, item)
    if item.weight is not None:
        return ["coalesce({}, 0) * {}".format(reference, _number_text(item.weight))]
    resolved = _resolve(scope, item)
    if resolved.type.kind == SET:
        return [
            'if({}.{}, {}, 0)'.format(reference, key, _number_text(points)) for key, points in item.points
        ]
    return [
        'if({} == "{}", {}, 0)'.format(reference, key, _number_text(points)) for key, points in item.points
    ]


def _reference_text(scope: Scope, item: ScoreItem) -> str:
    if item.question in scope.by_uuid or item.question in scope.subquestion_owner:
        head = 'q("{}")'.format(item.question)
    else:
        head = item.question
    return "{}.{}".format(head, item.member) if item.member else head


def _band_expression(score: Score) -> str:
    return _nested_band(score.code, score.bands, 0)


def _nested_band(code: str, bands: Tuple[ScoreBand, ...], index: int) -> str:
    band = bands[index]
    if index == len(bands) - 1:
        return '"{}"'.format(band.code)
    return 'if({} <= {}, "{}", {})'.format(
        code, _number_text(band.up_to), band.code, _nested_band(code, bands, index + 1)
    )


def _number_text(value: Optional[float]) -> str:
    if value is None:
        return "0"
    return str(int(value)) if float(value).is_integer() else repr(float(value))
