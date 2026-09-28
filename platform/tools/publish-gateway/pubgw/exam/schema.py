"""``exam`` 块的校验与解析（契约 survey-exam-v1 §2、§3）。

与 ``policy/schema.py`` 同一路数：一次遍历同时收集全部问题并构造不可变的
:class:`ExamKey`；有任何一个问题，调用方就拿不到答案键。未知键一律报错——
一个拼错的 ``correct`` 被静默忽略，等于这道题悄悄变成"怎么答都对"。

题型与选项代码不自己判定，走 ``logic/scope.py`` 已有的解析：那里已经把
choice／set／text／number 四类与各自的合法成员钉死了，考试没有理由再造一套。
"""

from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from ..model import Question, SurveyDefinition
from ..validate import ValidationIssue
from ..logic.model import LogicModel
from ..logic.scope import CHOICE, NUMBER, SET, TEXT, ResolutionError

EXAM_VERSION = 1
MAX_POINTS = 1000
#: 客观题：有唯一确定的对错。数组题、排序题等不在其列。
GRADABLE_KINDS = (CHOICE, SET, TEXT, NUMBER)

_TOP_KEYS = frozenset({"examVersion", "answerKey"})
_ENTRY_KEYS = frozenset({"question", "correct", "points", "ignoreCase", "trim"})
#: 只有文本题才谈得上大小写与空白；选项代码是精确匹配。
_TEXT_ONLY_KEYS = ("ignoreCase", "trim")


@dataclass(frozen=True)
class AnswerKeyEntry:
    """一道客观题的答案与分值。code 是规范化后的题目代码。"""

    code: str
    kind: str
    correct: Tuple[str, ...]
    points: float
    ignore_case: bool = False
    trim: bool = True


@dataclass(frozen=True)
class ExamKey:
    entries: Tuple[AnswerKeyEntry, ...] = ()

    def question_codes(self) -> Tuple[str, ...]:
        return tuple(entry.code for entry in self.entries)

    def find(self, code: str) -> Optional[AnswerKeyEntry]:
        for entry in self.entries:
            if entry.code == code:
                return entry
        return None


class ExamError(ValueError):
    """在校验不通过的定义上解析答案键。"""


def check_exam(definition: SurveyDefinition) -> List[ValidationIssue]:
    return _Reader(definition).read()[1]


def parse_exam(definition: SurveyDefinition) -> Optional[ExamKey]:
    """定义没有 exam 块返回 None；有问题抛 ExamError（调用方应先校验）。"""
    key, issues = _Reader(definition).read()
    if issues:
        raise ExamError("; ".join(issue.message for issue in issues))
    return key


class _Reader:
    def __init__(self, definition: SurveyDefinition):
        self._definition = definition
        self._issues: List[ValidationIssue] = []
        self._scope = LogicModel(definition).scope

    def read(self) -> Tuple[Optional[ExamKey], List[ValidationIssue]]:
        payload = self._definition.exam
        if payload is None:
            return None, []
        if not isinstance(payload, Mapping):
            return self._fail("E_EXAM_INVALID", "exam", "exam 必须是对象")
        unknown = sorted(set(payload) - _TOP_KEYS)
        for name in unknown:
            self._add("E_EXAM_UNKNOWN_KEY", "exam." + name, "未知的键 {}".format(name))
        if payload.get("examVersion") != EXAM_VERSION:
            self._add("E_EXAM_VERSION", "exam.examVersion",
                      "examVersion 必须是 {}".format(EXAM_VERSION))
        entries = self._entries(payload.get("answerKey"))
        if self._issues:
            return None, self._issues
        return ExamKey(tuple(entries)), []

    # ------------------------------------------------------------ answerKey

    def _entries(self, payload: Any) -> List[AnswerKeyEntry]:
        if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
            self._add("E_EXAM_TYPE", "exam.answerKey", "answerKey 必须是数组")
            return []
        if not payload:
            self._add("E_EXAM_EMPTY", "exam.answerKey", "answerKey 不能为空")
            return []
        entries: List[AnswerKeyEntry] = []
        seen: List[str] = []
        for index, raw in enumerate(payload):
            entry = self._entry(raw, "exam.answerKey[{}]".format(index))
            if entry is None:
                continue
            if entry.code in seen:
                self._add("E_EXAM_DUPLICATE", "exam.answerKey[{}].question".format(index),
                          "题目 {} 被重复指定了答案".format(entry.code))
                continue
            seen.append(entry.code)
            entries.append(entry)
        return entries

    def _entry(self, raw: Any, path: str) -> Optional[AnswerKeyEntry]:
        if not isinstance(raw, Mapping):
            self._add("E_EXAM_TYPE", path, "答案键条目必须是对象")
            return None
        for name in sorted(set(raw) - _ENTRY_KEYS):
            self._add("E_EXAM_UNKNOWN_KEY", "{}.{}".format(path, name), "未知的键 {}".format(name))
        resolved = self._question(raw.get("question"), path)
        if resolved is None:
            return None
        question, kind, domain = resolved
        points = self._points(raw.get("points"), path)
        correct = self._correct(raw.get("correct"), kind, domain, path)
        flags = self._flags(raw, kind, path)
        if points is None or correct is None or flags is None:
            return None
        return AnswerKeyEntry(question.code, kind, correct, points, flags[0], flags[1])

    def _question(self, value: Any, path: str) -> Optional[Tuple[Question, str, Tuple[str, ...]]]:
        if not isinstance(value, str) or not value:
            self._add("E_EXAM_TYPE", path + ".question", "question 必须是题目代码或 UUID")
            return None
        question = self._scope.by_code.get(value) or self._scope.by_uuid.get(value)
        if question is None:
            self._add("E_EXAM_QUESTION", path + ".question", "定义里没有题目 {}".format(value))
            return None
        try:
            resolved = self._scope.resolve_question(question)
        except ResolutionError as error:
            self._add("E_EXAM_QUESTION_TYPE", path + ".question", error.message)
            return None
        if resolved.type.kind not in GRADABLE_KINDS:
            self._add("E_EXAM_QUESTION_TYPE", path + ".question",
                      "题目 {}（类型 {}）不是客观题，无法判定对错".format(question.code, question.type))
            return None
        return question, resolved.type.kind, tuple(sorted(resolved.type.domain))

    def _points(self, value: Any, path: str) -> Optional[float]:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self._add("E_EXAM_TYPE", path + ".points", "points 必须是数字")
            return None
        if not 0 < value <= MAX_POINTS:
            self._add("E_EXAM_RANGE", path + ".points",
                      "points 必须在 (0, {}] 之间".format(MAX_POINTS))
            return None
        return float(value)

    def _correct(self, value: Any, kind: str, domain: Tuple[str, ...],
                 path: str) -> Optional[Tuple[str, ...]]:
        where = path + ".correct"
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            self._add("E_EXAM_TYPE", where, "correct 必须是字符串数组")
            return None
        if not value:
            self._add("E_EXAM_EMPTY", where, "correct 不能为空")
            return None
        if any(isinstance(item, bool) or not isinstance(item, str) for item in value):
            self._add("E_EXAM_TYPE", where, "correct 的每一项都必须是字符串")
            return None
        answers = tuple(value)
        if len(set(answers)) != len(answers):
            self._add("E_EXAM_ANSWER", where, "correct 里有重复项")
            return None
        if kind in (CHOICE, SET):
            return self._correct_codes(answers, kind, domain, where)
        if any(not item.strip() for item in answers):
            self._add("E_EXAM_ANSWER", where, "correct 里有空白答案")
            return None
        return answers

    def _correct_codes(self, answers: Tuple[str, ...], kind: str, domain: Tuple[str, ...],
                       where: str) -> Optional[Tuple[str, ...]]:
        unknown = [item for item in answers if item not in domain]
        if unknown:
            self._add("E_EXAM_ANSWER", where,
                      "{} 不是这道题的选项（可选：{}）".format(", ".join(unknown), ", ".join(domain)))
            return None
        if kind == CHOICE and len(answers) != 1:
            self._add("E_EXAM_ANSWER", where, "单选题只能有一个正确选项")
            return None
        # 多选的正确集合与顺序无关：排序让同一份答案键编译出同一个摘要。
        return tuple(sorted(answers))

    def _flags(self, raw: Mapping[str, Any], kind: str, path: str) -> Optional[Tuple[bool, bool]]:
        present = [name for name in _TEXT_ONLY_KEYS if name in raw]
        if present and kind not in (TEXT, NUMBER):
            self._add("E_EXAM_MATCH", "{}.{}".format(path, present[0]),
                      "{} 只对文本题有意义".format(present[0]))
            return None
        values = []
        for name, default in (("ignoreCase", False), ("trim", True)):
            value = raw.get(name, default)
            if not isinstance(value, bool):
                self._add("E_EXAM_TYPE", "{}.{}".format(path, name), "{} 必须是布尔值".format(name))
                return None
            values.append(value)
        return values[0], values[1]

    # ------------------------------------------------------------ 工具

    def _fail(self, code: str, path: str, message: str):
        self._add(code, path, message)
        return None, self._issues

    def _add(self, code: str, path: str, message: str) -> None:
        self._issues.append(ValidationIssue(code, path, message))
