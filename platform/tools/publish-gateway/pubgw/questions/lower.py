"""题型扩展键 → 引擎属性与服务端规则。

在逻辑降级（pubgw/logic/lower.py）之后运行，产物仍是不可变的 SurveyDefinition：

- ``format`` → ``em_validation_q`` 的一段（见 formats.py）；
- ``maxLength`` → ``maximum_chars``（浏览器端截断）＋ 按字符计数的服务端规则；
- 子题 ``exclusive`` → ``exclude_all_others``（引擎前端联动）＋ 服务端互斥规则。

已有的 ``em_validation_q``（v2 的 validation 降级结果或 v1 直写的属性）放在最前面，
各段用 ``and`` 连接；提示用「；」连接。**没有任何扩展键的题目原样返回同一个对象**，
旧定义的 LSS 因而逐字节不变。
"""

from dataclasses import replace
from typing import Dict, List, Sequence

from ..model import Group, Question, SurveyDefinition
from .formats import FORMATS
from .themes import lower_theme, read_theme_options

VALIDATION_ATTRIBUTE = "em_validation_q"
TIP_ATTRIBUTE = "em_validation_q_tip"
_TIP_SEPARATOR = "；"
_EXCLUSIVE_TIP = "互斥选项不能与其他选项同时选择"
_MAX_LENGTH_TIP = "最多 {} 个字"


def lower_question_types(definition: SurveyDefinition) -> SurveyDefinition:
    groups = tuple(_lower_group(group) for group in definition.groups)
    if all(new is old for new, old in zip(groups, definition.groups)):
        return definition
    return replace(definition, groups=groups)


def _lower_group(group: Group) -> Group:
    questions = tuple(_lower_question(question) for question in group.questions)
    if all(new is old for new, old in zip(questions, group.questions)):
        return group
    return replace(group, questions=questions)


def _lower_question(question: Question) -> Question:
    rules: List[str] = []
    tips: List[str] = []
    attributes: Dict[str, str] = dict(question.attributes)
    variable = question.code + ".NAOK"

    if question.format:
        rule = FORMATS[question.format]
        rules.append(rule.expression(variable))
        tips.append(rule.tip)
    if question.max_length is not None:
        attributes["maximum_chars"] = str(question.max_length)
        rules.append(
            "(is_empty({v}) or strlen(html_entity_decode({v})) <= {n})".format(v=variable, n=question.max_length)
        )
        tips.append(_MAX_LENGTH_TIP.format(question.max_length))
    theme = lower_theme(question, read_theme_options(question)[0])
    attributes.update(theme.attributes)
    rules.extend(theme.rules)
    tips.extend(theme.tips)
    exclusive = [sub.code for sub in question.subquestions if sub.exclusive]
    if exclusive:
        attributes["exclude_all_others"] = ";".join(exclusive)
        exclusive_rules = _exclusive_rules(question, exclusive)
        rules.extend(exclusive_rules)
        if exclusive_rules:
            tips.append(_EXCLUSIVE_TIP)

    if not rules:
        return question if attributes == question.attributes else replace(question, attributes=attributes)
    _merge(attributes, VALIDATION_ATTRIBUTE, rules, " and ", wrap=True)
    _merge(attributes, TIP_ATTRIBUTE, tips, _TIP_SEPARATOR, wrap=False)
    return replace(question, attributes=attributes)


def _exclusive_rules(question: Question, exclusive: Sequence[str]) -> List[str]:
    """互斥项选中时，其余每一列（含其他项与「其他」文本）都必须为空。P 的评论列不算。"""
    rules = []
    for code in exclusive:
        others = ["{}_{}.NAOK".format(question.code, sub.code) for sub in question.subquestions if sub.code != code]
        if question.other:
            others.append("{}_other.NAOK".format(question.code))
        if not others:
            continue
        rules.append('({q}_{c}.NAOK != "Y" or count({others}) == 0)'.format(
            q=question.code, c=code, others=", ".join(others)))
    return rules


def _merge(attributes: Dict[str, str], name: str, parts: List[str], separator: str, wrap: bool) -> None:
    existing = attributes.get(name, "").strip()
    pieces = ([existing] if existing else []) + parts
    if len(pieces) == 1:
        attributes[name] = pieces[0]
        return
    attributes[name] = separator.join("({})".format(piece) if wrap else piece for piece in pieces)
