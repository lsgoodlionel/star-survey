"""引用解析：DSL 里的题目代码／UUID → 题目、子题、尺度，以及它的类型与引擎变量名。

引擎变量名一律是 ExpressionScript 的 qcode 形式（``QCODE``、``QCODE_SQ001``、
``QCODE_SQ001_1``、``QCODE_other``），不是 SGQA：导入时引擎会重新分配
sid/gid/qid，qcode 却只取决于题目代码与子题代码——而这两者的一致性由
validate.py 的代码规则与发布后的回读校验共同保证（ADR 0009）。
"""

from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, Optional, Tuple

from . import ast
from ..model import Question, SurveyDefinition

NUMBER = "number"
TEXT = "text"
BOOL = "bool"
CHOICE = "choice"
SET = "set"
ANY = "any"

#: 单选题「其他」在答卷里的代码。
OTHER_CODE = "-oth-"
OTHER_MEMBER = "other"

_TEXT_TYPES = frozenset({"S", "T", "U", "D"})
_CHOICE_TYPES = frozenset({"L", "!"})
_SET_TYPES = frozenset({"M", "P"})
_DUAL_SCALES = (0, 1)

#: 引擎每页一组（G）、每页一题（Q）、全部一页（A）。
FORMAT_BY_GROUP = "G"
FORMAT_BY_QUESTION = "Q"
FORMAT_ALL_IN_ONE = "A"


@dataclass(frozen=True)
class Type:
    kind: str
    domain: FrozenSet[str] = frozenset()

    @property
    def is_textual(self) -> bool:
        return self.kind in (TEXT, CHOICE)

    def describe(self) -> str:
        return self.kind


TYPE_NUMBER = Type(NUMBER)
TYPE_TEXT = Type(TEXT)
TYPE_BOOL = Type(BOOL)
TYPE_ANY = Type(ANY)

#: 引用的形态：value（普通取值）、selected（多选的某个选项，"Y" 表示选中）、set（整道多选题）。
VALUE = "value"
SELECTED = "selected"
WHOLE_SET = "set"


@dataclass(frozen=True)
class Resolved:
    question: Question
    variable: str
    type: Type
    shape: str = VALUE
    members: Tuple[str, ...] = ()
    is_label_capable: bool = False


class ResolutionError(ValueError):
    """解析失败；code 是 ValidationIssue 的错误码。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class Scope:
    """一份定义里所有可引用的东西，以及它们的文档顺序与页码。"""

    def __init__(self, definition: SurveyDefinition, calculated_type: Callable[[Question], Type]):
        self._calculated_type = calculated_type
        self.by_code: Dict[str, Question] = {}
        self.by_uuid: Dict[str, Question] = {}
        self.subquestion_owner: Dict[str, Tuple[Question, str]] = {}
        self.order: Dict[str, int] = {}
        self.group_index: Dict[str, int] = {}
        self.page: Dict[str, int] = {}
        page_format = definition.settings.get("format", FORMAT_BY_GROUP)
        position = 0
        for group_position, group in enumerate(definition.groups):
            for question in group.questions:
                self.by_code.setdefault(question.code, question)
                self.by_uuid.setdefault(question.uuid, question)
                self.order[question.uuid] = position
                self.group_index[question.uuid] = group_position
                self.page[question.uuid] = _page_of(page_format, group_position, position)
                for subquestion in question.subquestions:
                    self.subquestion_owner.setdefault(subquestion.uuid, (question, subquestion.code))
                position += 1

    def resolve(self, ref: ast.Ref) -> Resolved:
        question, member = self._target(ref)
        return _resolve_member(question, member, ref.scale, self._calculated_type)

    def resolve_question(self, question: Question) -> Resolved:
        return _resolve_member(question, None, None, self._calculated_type)

    def _target(self, ref: ast.Ref) -> Tuple[Question, Optional[str]]:
        if not ref.by_uuid:
            question = self.by_code.get(ref.target)
            if question is None:
                raise ResolutionError("E_EXPR_UNKNOWN_REFERENCE", "no question with code {!r}".format(ref.target))
            return question, ref.member
        question = self.by_uuid.get(ref.target)
        if question is not None:
            return question, ref.member
        owner = self.subquestion_owner.get(ref.target)
        if owner is None:
            raise ResolutionError("E_EXPR_UNKNOWN_REFERENCE", "no question or subquestion with uuid {!r}".format(ref.target))
        if ref.member is not None:
            raise ResolutionError("E_EXPR_UNKNOWN_SUBQUESTION", "a subquestion reference cannot take a member")
        return owner


def _page_of(page_format: str, group_position: int, question_position: int) -> int:
    if page_format == FORMAT_ALL_IN_ONE:
        return 0
    if page_format == FORMAT_BY_QUESTION:
        return question_position
    return group_position


def _resolve_member(
    question: Question, member: Optional[str], scale: Optional[int], calculated_type: Callable[[Question], Type]
) -> Resolved:
    kind = question.type
    if scale is not None and kind != "1":
        raise ResolutionError("E_EXPR_SCALE_REQUIRED", "question {} has no answer scales".format(question.code))
    if kind == "X":
        raise ResolutionError("E_EXPR_NOT_A_VALUE", "question {} is display text and has no value".format(question.code))
    if kind in _CHOICE_TYPES:
        return _choice(question, member)
    if kind in _SET_TYPES:
        return _set(question, member)
    if kind in ("F", "1"):
        return _array(question, member, scale)
    if member is not None:
        raise _unknown_member(question, member)
    if kind in _TEXT_TYPES:
        return Resolved(question, question.code, TYPE_TEXT)
    if kind == "N":
        return Resolved(question, question.code, TYPE_NUMBER)
    if kind == "*":
        return Resolved(question, question.code, calculated_type(question))
    return Resolved(question, question.code, TYPE_ANY)


def _choice(question: Question, member: Optional[str]) -> Resolved:
    if member is None:
        codes = {answer.code for answer in question.answers_on_scale(0)}
        if question.other:
            codes.add(OTHER_CODE)
        return Resolved(question, question.code, Type(CHOICE, frozenset(codes)), is_label_capable=True)
    if member == OTHER_MEMBER and question.other:
        return Resolved(question, question.code + "_other", TYPE_TEXT)
    raise _unknown_member(question, member)


def _set(question: Question, member: Optional[str]) -> Resolved:
    codes = tuple(subquestion.code for subquestion in question.subquestions)
    if member is None:
        variables = tuple("{}_{}".format(question.code, code) for code in codes)
        if question.other:
            variables += (question.code + "_other",)
        return Resolved(question, question.code, Type(SET, frozenset(codes)), WHOLE_SET, variables)
    if member == OTHER_MEMBER and question.other:
        return Resolved(question, question.code + "_other", TYPE_TEXT)
    if member in codes:
        return Resolved(question, "{}_{}".format(question.code, member), TYPE_BOOL, SELECTED)
    raise _unknown_member(question, member)


def _array(question: Question, member: Optional[str], scale: Optional[int]) -> Resolved:
    if member is None:
        raise ResolutionError(
            "E_EXPR_MEMBER_REQUIRED", "array question {} must be referenced by row, e.g. {}.ROW".format(question.code, question.code)
        )
    if member not in {subquestion.code for subquestion in question.subquestions}:
        raise _unknown_member(question, member)
    if question.type == "F":
        variable = "{}_{}".format(question.code, member)
        return Resolved(question, variable, _answer_domain(question, 0), is_label_capable=True)
    if scale not in _DUAL_SCALES:
        raise ResolutionError(
            "E_EXPR_SCALE_REQUIRED",
            "dual-scale question {} needs a scale: {}.{}[0] or [1]".format(question.code, question.code, member),
        )
    variable = "{}_{}_{}".format(question.code, member, scale)
    return Resolved(question, variable, _answer_domain(question, scale), is_label_capable=True)


def _answer_domain(question: Question, scale: int) -> Type:
    return Type(CHOICE, frozenset(answer.code for answer in question.answers_on_scale(scale)))


def _unknown_member(question: Question, member: str) -> ResolutionError:
    return ResolutionError(
        "E_EXPR_UNKNOWN_SUBQUESTION", "question {} has no subquestion or member {!r}".format(question.code, member)
    )
