"""平台问卷定义的数据模型。

这是系统边界：定义来自平台的作者端，属于外部数据，必须先解析成不可变对象
再进入后续环节。这里只做**结构**校验（缺项、类型错误），语义规则在
validate.py。解析失败一律抛 DefinitionError，绝不带着半成品往下走。
"""

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

DEFINITION_VERSION = 1

#: 带逻辑 DSL 的定义版本（WP-03）：条件、校验、计算值、答案引用。
LOGIC_DEFINITION_VERSION = 2
SUPPORTED_DEFINITION_VERSIONS = (DEFINITION_VERSION, LOGIC_DEFINITION_VERSION)

#: 只有 v2 才认识的键。v1 定义里出现它们直接拒绝，绝不静默丢掉一条显示条件。
_LOGIC_KEYS = ("condition", "calculation", "validation")

_INHERIT_THEME = "inherit"


class DefinitionError(ValueError):
    """定义结构非法：缺字段、类型不对、版本不认识。"""


@dataclass(frozen=True)
class AnswerOption:
    """一个答案选项。code 对应 lime_answers.code（最多 5 个字符）。"""

    code: str
    text: str
    scale: int = 0
    assessment_value: int = 0


@dataclass(frozen=True)
class SubQuestion:
    """一个子题。code 对应 lime_questions.title，也是 get_fieldmap 的 aid。"""

    uuid: str
    code: str
    text: str
    scale: int = 0


@dataclass(frozen=True)
class ValidationRule:
    """v2 的题目校验：rule 是逻辑 DSL 表达式，message 是可含引用的提示文本。"""

    rule: str
    message: str = ""


@dataclass(frozen=True)
class Question:
    """一道题。uuid 是平台侧的稳定标识，code 是引擎侧的映射键。"""

    uuid: str
    code: str
    type: str
    text: str
    help: str = ""
    mandatory: bool = False
    other: bool = False
    theme: str = ""
    relevance: str = "1"
    answers: Tuple[AnswerOption, ...] = ()
    subquestions: Tuple[SubQuestion, ...] = ()
    attributes: Dict[str, str] = field(default_factory=dict)
    condition: str = ""
    calculation: str = ""
    validation: Optional[ValidationRule] = None

    def answers_on_scale(self, scale: int) -> Tuple[AnswerOption, ...]:
        return tuple(answer for answer in self.answers if answer.scale == scale)


@dataclass(frozen=True)
class Group:
    """一个题组。题组本身不出现在答卷列里，只影响分页与顺序。"""

    uuid: str
    title: str
    questions: Tuple[Question, ...]
    description: str = ""
    relevance: str = "1"
    condition: str = ""


@dataclass(frozen=True)
class SurveyDefinition:
    """一份完整的平台问卷定义。"""

    uuid: str
    title: str
    language: str
    settings: Dict[str, str]
    groups: Tuple[Group, ...]
    description: str = ""
    theme: str = "fruity_twentythree"
    additional_languages: Tuple[str, ...] = ()
    participants: Tuple[Dict[str, str], ...] = ()
    definition_version: int = DEFINITION_VERSION
    #: 访问策略原文（WP-04，ADR 0016）。结构与语义都在 pubgw/policy/ 里校验（422），这里只保存副本。
    policy: Any = None

    @property
    def has_logic(self) -> bool:
        return self.definition_version == LOGIC_DEFINITION_VERSION

    def questions(self) -> Iterator[Question]:
        """按文档顺序遍历所有题目。"""
        for group in self.groups:
            for question in group.questions:
                yield question

    @property
    def is_theme_inherited(self) -> bool:
        return self.theme.strip().lower() == _INHERIT_THEME

    @classmethod
    def from_json(cls, text: str) -> "SurveyDefinition":
        try:
            payload = json.loads(text)
        except ValueError as error:
            raise DefinitionError("definition is not valid JSON: {}".format(error)) from None
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SurveyDefinition":
        _require_mapping(payload, "definition")
        version = payload.get("definitionVersion")
        if isinstance(version, bool) or version not in SUPPORTED_DEFINITION_VERSIONS:
            raise DefinitionError(
                "unsupported definitionVersion {!r}, expected one of {}".format(
                    version, ", ".join(str(item) for item in SUPPORTED_DEFINITION_VERSIONS)
                )
            )
        is_logic = version == LOGIC_DEFINITION_VERSION
        return cls(
            uuid=_text(payload, "uuid", "definition"),
            title=_text(payload, "title", "definition"),
            language=_text(payload, "language", "definition"),
            description=_optional_text(payload, "description"),
            theme=_optional_text(payload, "theme") or "fruity_twentythree",
            settings=_settings(payload.get("settings")),
            additional_languages=tuple(_string_list(payload.get("additionalLanguages"), "additionalLanguages")),
            groups=tuple(
                _group(entry, index, is_logic) for index, entry in enumerate(_list(payload, "groups"))
            ),
            participants=tuple(_participant(entry) for entry in payload.get("participants") or []),
            definition_version=version,
            policy=copy.deepcopy(payload.get("policy")),
        )


def _group(payload: Any, index: int, is_logic: bool) -> Group:
    where = "groups[{}]".format(index)
    _require_mapping(payload, where)
    _check_logic_keys(payload, where, is_logic)
    return Group(
        uuid=_text(payload, "uuid", where),
        title=_text(payload, "title", where),
        description=_optional_text(payload, "description"),
        relevance=_optional_text(payload, "relevance") or "1",
        condition=_optional_text(payload, "condition"),
        questions=tuple(
            _question(entry, "{}.questions[{}]".format(where, position), is_logic)
            for position, entry in enumerate(_list(payload, "questions", allow_empty=True))
        ),
    )


def _question(payload: Any, where: str, is_logic: bool) -> Question:
    _require_mapping(payload, where)
    _check_logic_keys(payload, where, is_logic)
    return Question(
        uuid=_text(payload, "uuid", where),
        code=_text(payload, "code", where),
        type=_text(payload, "type", where),
        text=_text(payload, "text", where),
        help=_optional_text(payload, "help"),
        mandatory=_flag(payload.get("mandatory"), where, "mandatory"),
        other=_flag(payload.get("other"), where, "other"),
        theme=_optional_text(payload, "theme"),
        relevance=_optional_text(payload, "relevance") or "1",
        attributes=_settings(payload.get("attributes")),
        answers=tuple(
            _answer(entry, "{}.answers[{}]".format(where, position))
            for position, entry in enumerate(payload.get("answers") or [])
        ),
        subquestions=tuple(
            _subquestion(entry, "{}.subquestions[{}]".format(where, position))
            for position, entry in enumerate(payload.get("subquestions") or [])
        ),
        condition=_optional_text(payload, "condition"),
        calculation=_optional_text(payload, "calculation"),
        validation=_validation(payload.get("validation"), where),
    )


def _check_logic_keys(payload: Mapping[str, Any], where: str, is_logic: bool) -> None:
    if is_logic:
        return
    for key in _LOGIC_KEYS:
        if key in payload:
            raise DefinitionError(
                "{}.{} requires definitionVersion {}".format(where, key, LOGIC_DEFINITION_VERSION)
            )


def _validation(payload: Any, where: str) -> Optional[ValidationRule]:
    if payload is None:
        return None
    _require_mapping(payload, where + ".validation")
    return ValidationRule(
        rule=_text(payload, "rule", where + ".validation"),
        message=_optional_text(payload, "message"),
    )


def _answer(payload: Any, where: str) -> AnswerOption:
    _require_mapping(payload, where)
    return AnswerOption(
        code=_text(payload, "code", where),
        text=_text(payload, "text", where),
        scale=_integer(payload.get("scale", 0), where, "scale"),
        assessment_value=_integer(payload.get("assessmentValue", 0), where, "assessmentValue"),
    )


def _subquestion(payload: Any, where: str) -> SubQuestion:
    _require_mapping(payload, where)
    return SubQuestion(
        uuid=_text(payload, "uuid", where),
        code=_text(payload, "code", where),
        text=_text(payload, "text", where),
        scale=_integer(payload.get("scale", 0), where, "scale"),
    )


def _participant(payload: Any) -> Dict[str, str]:
    _require_mapping(payload, "participants[]")
    return {str(key): str(value) for key, value in payload.items()}


def _settings(payload: Any) -> Dict[str, str]:
    if payload is None:
        return {}
    _require_mapping(payload, "settings")
    for key, value in payload.items():
        if not isinstance(value, str):
            raise DefinitionError("setting {!r} must be a string, got {!r}".format(key, value))
    return dict(payload)


def _require_mapping(payload: Any, where: str) -> None:
    if not isinstance(payload, Mapping):
        raise DefinitionError("{} must be an object, got {}".format(where, type(payload).__name__))


def _list(payload: Mapping[str, Any], key: str, allow_empty: bool = False) -> Sequence[Any]:
    value = payload.get(key)
    if value is None and allow_empty:
        return []
    if not isinstance(value, list):
        raise DefinitionError("{!r} must be a list, got {}".format(key, type(value).__name__))
    return value


def _string_list(value: Any, key: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise DefinitionError("{!r} must be a list of strings".format(key))
    return list(value)


def _text(payload: Mapping[str, Any], key: str, where: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or value == "":
        raise DefinitionError("{}.{} is required and must be a non-empty string".format(where, key))
    return value


def _optional_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise DefinitionError("{!r} must be a string".format(key))
    return value


def _flag(value: Any, where: str, key: str) -> bool:
    if value is None:
        return False
    if not isinstance(value, bool):
        raise DefinitionError("{}.{} must be a boolean".format(where, key))
    return value


def _integer(value: Any, where: str, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DefinitionError("{}.{} must be an integer".format(where, key))
    return value
