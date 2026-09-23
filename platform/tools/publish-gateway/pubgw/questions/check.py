"""题型专属的发布前校验：子题尺度、排序项数、扩展键（format／maxLength／exclusive）与属性取值。

通用的结构规则（选项、子题、代码、「其他」）仍在 validate.py；这里只补各题型自己的约束，
全部收集后一次返回，路径与 validate.py 同一格式（``groups[g].questions[q]...``）。
"""

from typing import List

from ..model import Question
from ..qtypes import MATRIX, shape_of
from ..validate import ValidationIssue
from .attributes import MAX_CHARS_LIMIT, check_attributes
from .formats import FORMAT_TYPES, FORMATS
from .themes import MANAGED_ATTRIBUTES, read_theme_options

#: 允许 maxLength 的题型：单列自由文本。
MAX_LENGTH_TYPES = frozenset({"S", "T", "U"})
#: 允许子题 exclusive 的题型：多选。
EXCLUSIVE_TYPES = frozenset({"M", "P"})
#: WP-02 之前就支持的题型：它们的子题尺度规则保持原样，避免旧定义突然被拒。
_LEGACY_TYPES = frozenset({"L", "!", "M", "P", "F", "1", "S", "T", "U", "N", "D", "X", "*"})
MIN_RANKING_ITEMS = 2


def check_question_type(question: Question, where: str) -> List[ValidationIssue]:
    shape = shape_of(question.type)
    if shape is None:
        return []
    issues: List[ValidationIssue] = []
    issues.extend(_subquestion_scales(question, shape, where))
    issues.extend(_ranking(question, where))
    issues.extend(_format(question, where))
    issues.extend(_max_length(question, where))
    issues.extend(_exclusive(question, where))
    issues.extend(_theme(question, where))
    issues.extend(
        ValidationIssue(code, "{}.attributes.{}".format(where, name), message)
        for code, name, message in check_attributes(question)
    )
    return issues


def _theme(question: Question, where: str) -> List[ValidationIssue]:
    """平台题型主题与它的 themeOptions；作者直写主题管理的属性一律拒绝。"""
    _, found = read_theme_options(question)
    issues = [
        ValidationIssue(code, "{}.{}".format(where, path), message) for code, path, message in found
    ]
    issues.extend(
        ValidationIssue(
            "E_THEME_ATTRIBUTE_MANAGED",
            "{}.attributes.{}".format(where, name),
            "属性 {} 由题型主题的 themeOptions 生成，不能在定义里直写".format(name),
        )
        for name in sorted(set(question.attributes) & MANAGED_ATTRIBUTES)
    )
    return issues


def _subquestion_scales(question: Question, shape, where: str) -> List[ValidationIssue]:
    if question.type in _LEGACY_TYPES or not shape.needs_subquestions:
        return []
    issues = [
        ValidationIssue(
            "E_SUBQUESTION_SCALE_INVALID",
            "{}.subquestions[{}].scale".format(where, index),
            "题型 {} 的子题尺度只能是 {}".format(question.type, "/".join(str(s) for s in shape.subquestion_scales)),
        )
        for index, sub in enumerate(question.subquestions)
        if sub.scale not in shape.subquestion_scales
    ]
    if shape.column_layout == MATRIX and question.subquestions:
        present = {sub.scale for sub in question.subquestions}
        for scale, role in ((0, "行"), (1, "列")):
            if scale not in present:
                issues.append(
                    ValidationIssue(
                        "E_MISSING_SUBQUESTION_SCALE",
                        "{}.subquestions".format(where),
                        "矩阵题 {} 缺少{}子题（尺度 {}），答卷表里不会有任何一列".format(question.code, role, scale),
                    )
                )
    return issues


def _ranking(question: Question, where: str) -> List[ValidationIssue]:
    if question.type != "R" or not question.subquestions or len(question.subquestions) >= MIN_RANKING_ITEMS:
        return []
    return [
        ValidationIssue(
            "E_RANKING_TOO_FEW_ITEMS", "{}.subquestions".format(where), "排序题至少要有 {} 个排序项".format(MIN_RANKING_ITEMS)
        )
    ]


def _format(question: Question, where: str) -> List[ValidationIssue]:
    if not question.format:
        return []
    path = "{}.format".format(where)
    if question.format not in FORMATS:
        return [ValidationIssue("E_FORMAT_UNKNOWN", path, "未知的输入格式 {!r}（支持：{}）".format(
            question.format, ", ".join(sorted(FORMATS))))]
    if question.type not in FORMAT_TYPES:
        return [ValidationIssue("E_FORMAT_UNSUPPORTED_TYPE", path, "只有短文本题（S）可以设置输入格式")]
    return []


def _max_length(question: Question, where: str) -> List[ValidationIssue]:
    if question.max_length is None:
        return []
    path = "{}.maxLength".format(where)
    if question.type not in MAX_LENGTH_TYPES:
        return [ValidationIssue("E_MAX_LENGTH_UNSUPPORTED_TYPE", path, "只有文本题（S T U）可以设置 maxLength")]
    issues = []
    if not 1 <= question.max_length <= MAX_CHARS_LIMIT:
        issues.append(ValidationIssue("E_MAX_LENGTH_RANGE", path, "maxLength 必须在 1 到 {} 之间".format(MAX_CHARS_LIMIT)))
    if "maximum_chars" in question.attributes:
        issues.append(ValidationIssue(
            "E_MAX_LENGTH_CONFLICT", "{}.attributes.maximum_chars".format(where),
            "maxLength 会写入 maximum_chars，二者只能用一个"))
    return issues


def _exclusive(question: Question, where: str) -> List[ValidationIssue]:
    flagged = [index for index, sub in enumerate(question.subquestions) if sub.exclusive]
    if not flagged:
        return []
    if question.type not in EXCLUSIVE_TYPES:
        return [
            ValidationIssue(
                "E_EXCLUSIVE_UNSUPPORTED_TYPE",
                "{}.subquestions[{}].exclusive".format(where, index),
                "只有多选题（M P）的子题可以设为互斥",
            )
            for index in flagged
        ]
    if "exclude_all_others" in question.attributes:
        return [ValidationIssue(
            "E_EXCLUSIVE_CONFLICT", "{}.attributes.exclude_all_others".format(where),
            "子题 exclusive 会写入 exclude_all_others，二者只能用一个")]
    return []
