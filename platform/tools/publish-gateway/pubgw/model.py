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

#: 参与者条目里平台自选的引用键（ADR 0016）。网关摘掉它，只用来回指邀请码。
PARTICIPANT_REF = "ref"

#: 只有 v2 才认识的键。v1 定义里出现它们直接拒绝，绝不静默丢掉一条显示条件。
_LOGIC_KEYS = ("condition", "calculation", "validation")

#: 计分展开出来的题组标题（WP-03.3）。
DEFAULT_SCORING_GROUP_TITLE = "计分结果"

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
    #: WP-02：多选的互斥项（选中它时其余选项必须为空），编译成 exclude_all_others ＋ 服务端规则。
    exclusive: bool = False


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
    #: WP-02 题型扩展（v1、v2 通用，缺省即不生效）：输入格式与按字符计的长度上限，
    #: 均编译成服务端校验（platform/docs/p2/question-type-map.md 第三节）。
    format: str = ""
    max_length: Optional[int] = None
    #: WP-02 切片 02.3：平台题型主题的配置（pubgw/questions/themes.py）。
    #: 缺省为空＝不带任何主题配置，编译结果与旧定义逐字节一致。
    theme_options: Dict[str, Any] = field(default_factory=dict)

    def answers_on_scale(self, scale: int) -> Tuple[AnswerOption, ...]:
        return tuple(answer for answer in self.answers if answer.scale == scale)


@dataclass(frozen=True)
class ScoreItem:
    """一个计分项：一道题按选项给分（points），或按数值乘权重（weight）。

    question 可以写题目代码，也可以写题目 UUID（推荐编辑器用 UUID）。
    member 是数组题的行代码。points 保持作者给的顺序，编译出的表达式才稳定。
    """

    question: str
    member: str = ""
    points: Tuple[Tuple[str, float], ...] = ()
    weight: Optional[float] = None


@dataclass(frozen=True)
class ScoreBand:
    """一个分段。up_to 是这一段的上界（含），最后一段不写上界表示「及以上」。"""

    code: str
    up_to: Optional[float] = None
    text: str = ""


@dataclass(frozen=True)
class Score:
    """一份计分表：若干计分项加总成一个分数，再按分段给出结果。"""

    uuid: str
    code: str
    title: str
    items: Tuple[ScoreItem, ...]
    bands: Tuple[ScoreBand, ...] = ()
    group_title: str = DEFAULT_SCORING_GROUP_TITLE


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
    #: 每个参与者的平台自选引用（ADR 0016）。与 ``participants`` 同序、同长；没写的是 None。
    #: 网关自己留着回显邀请码，不转发给引擎——引擎只认 token 表的列，别的键本来也会被丢掉。
    participant_refs: Tuple[Optional[str], ...] = ()
    definition_version: int = DEFINITION_VERSION
    #: 访问策略原文（WP-04，ADR 0016）。结构与语义都在 pubgw/policy/ 里校验（422），这里只保存副本。
    policy: Any = None
    #: 品牌原文（WP-19，契约 survey-branding-v1）。校验与编译在 pubgw/branding/ 里，这里只保存副本。
    branding: Any = None
    #: 按语言的文本原文（同上）。缺项按基础语言回退，回退发生在编译期。
    translations: Any = None
    #: 计分表（WP-03.3，v2 专有）。语义校验在 logic/scoring.py，展开成计算值题后
    #: 与作者手写的 DSL 走同一条解析、检查、编译链路。
    scoring: Tuple[Score, ...] = ()

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
        participants, participant_refs = _participants(payload.get("participants"))
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
            participants=tuple(participants),
            participant_refs=tuple(participant_refs),
            definition_version=version,
            policy=copy.deepcopy(payload.get("policy")),
            branding=copy.deepcopy(payload.get("branding")),
            translations=copy.deepcopy(payload.get("translations")),
            scoring=_scoring(payload.get("scoring"), is_logic),
        )


def _scoring(payload: Any, is_logic: bool) -> Tuple[Score, ...]:
    if payload is None:
        return ()
    if not is_logic:
        raise DefinitionError(
            "scoring requires definitionVersion {}".format(LOGIC_DEFINITION_VERSION)
        )
    if not isinstance(payload, list):
        raise DefinitionError("'scoring' must be a list, got {}".format(type(payload).__name__))
    return tuple(_score(entry, "scoring[{}]".format(index)) for index, entry in enumerate(payload))


def _score(payload: Any, where: str) -> Score:
    _require_mapping(payload, where)
    items = _list(payload, "items")
    if not items:
        raise DefinitionError("{}.items must not be empty".format(where))
    return Score(
        uuid=_text(payload, "uuid", where),
        code=_text(payload, "code", where),
        title=_text(payload, "title", where),
        group_title=_optional_text(payload, "groupTitle") or DEFAULT_SCORING_GROUP_TITLE,
        items=tuple(
            _score_item(entry, "{}.items[{}]".format(where, index)) for index, entry in enumerate(items)
        ),
        bands=tuple(
            _score_band(entry, "{}.bands[{}]".format(where, index))
            for index, entry in enumerate(payload.get("bands") or [])
        ),
    )


def _score_item(payload: Any, where: str) -> ScoreItem:
    _require_mapping(payload, where)
    weight = payload.get("weight")
    return ScoreItem(
        question=_text(payload, "question", where),
        member=_optional_text(payload, "member"),
        points=_points(payload.get("points"), where),
        weight=None if weight is None else _number(weight, where, "weight"),
    )


def _points(payload: Any, where: str) -> Tuple[Tuple[str, float], ...]:
    """保持作者给的顺序：编译出的表达式必须可复现。"""
    if payload is None:
        return ()
    _require_mapping(payload, where + ".points")
    entries = []
    for key, value in payload.items():
        if not isinstance(key, str) or not key:
            raise DefinitionError("{}.points keys must be non-empty strings".format(where))
        entries.append((key, _number(value, where + ".points", key)))
    return tuple(entries)


def _score_band(payload: Any, where: str) -> ScoreBand:
    _require_mapping(payload, where)
    up_to = payload.get("upTo")
    return ScoreBand(
        code=_text(payload, "code", where),
        up_to=None if up_to is None else _number(up_to, where, "upTo"),
        text=_optional_text(payload, "text"),
    )


def _number(value: Any, where: str, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DefinitionError("{}.{} must be a number, got {!r}".format(where, key, value))
    return float(value)


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
        format=_optional_text(payload, "format"),
        max_length=_optional_integer(payload.get("maxLength"), where, "maxLength"),
        theme_options=_theme_options(payload.get("themeOptions"), where),
    )


def _theme_options(payload: Any, where: str) -> Dict[str, Any]:
    """只查「是不是一个键为字符串的对象」；每个主题自己的取值规则在 questions/themes.py（422）。"""
    if payload is None:
        return {}
    _require_mapping(payload, where + ".themeOptions")
    for key in payload:
        if not isinstance(key, str):
            raise DefinitionError("{}.themeOptions keys must be strings".format(where))
    return copy.deepcopy(dict(payload))


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
        exclusive=_flag(payload.get("exclusive"), where, "exclusive"),
    )


def _participants(payload: Any) -> Tuple[List[Dict[str, str]], List[Optional[str]]]:
    """拆出平台引用，剩下的原样交给引擎。引用重复就没法回指，当场拒绝。"""
    entries: List[Dict[str, str]] = []
    refs: List[Optional[str]] = []
    seen: Dict[str, int] = {}
    for index, item in enumerate(payload or []):
        _require_mapping(item, "participants[]")
        fields = {str(key): str(value) for key, value in item.items()}
        ref = fields.pop(PARTICIPANT_REF, None)
        if ref is not None:
            if not ref.strip():
                raise DefinitionError(
                    "participants[{}].{} must not be empty".format(index, PARTICIPANT_REF)
                )
            if ref in seen:
                raise DefinitionError(
                    "participants[{}].{} {!r} repeats participants[{}]".format(
                        index, PARTICIPANT_REF, ref, seen[ref]
                    )
                )
            seen[ref] = index
        entries.append(fields)
        refs.append(ref)
    return entries, refs


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


def _optional_integer(value: Any, where: str, key: str) -> Optional[int]:
    return None if value is None else _integer(value, where, key)


def _integer(value: Any, where: str, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DefinitionError("{}.{} must be an integer".format(where, key))
    return value
