"""``translations`` 块的校验与解析（契约 survey-branding-v1 §3）。

在此之前，附加语言只在 ``surveys_languagesettings`` 里多一行，题目、选项、题组一个字都没有：
作答页切到该语言是空白。本模块补上按语言的文本，并规定**缺译文按基础语言回退**——
半份译文也要能用，不能把已有内容变没。

uuid 与选项代码都要在定义里真实存在：一个拼错的 uuid 被静默忽略，
就等于作者以为翻译了、上线后却没有。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..model import SurveyDefinition
from ..validate import ValidationIssue

_LANGUAGE_KEYS = frozenset(
    {"title", "description", "groups", "questions", "subquestions", "answers"}
)
_GROUP_KEYS = frozenset({"title", "description"})
_QUESTION_KEYS = frozenset({"text", "help"})
_SUBQUESTION_KEYS = frozenset({"text"})
_ANSWER_KEYS = frozenset({"question", "code", "scale", "text"})

#: 选项在定义里的唯一键。
AnswerKey = Tuple[str, str, int]


@dataclass(frozen=True)
class LanguageTexts:
    """一个语言的全部译文。取不到的条目一律返回调用方给的基础语言文本。"""

    title: str = ""
    description: str = ""
    groups: Dict[str, Dict[str, str]] = field(default_factory=dict)
    questions: Dict[str, Dict[str, str]] = field(default_factory=dict)
    subquestions: Dict[str, Dict[str, str]] = field(default_factory=dict)
    answers: Dict[AnswerKey, str] = field(default_factory=dict)

    def group(self, uuid: str, key: str, fallback: str) -> str:
        return self.groups.get(uuid, {}).get(key) or fallback

    def question(self, uuid: str, key: str, fallback: str) -> str:
        return self.questions.get(uuid, {}).get(key) or fallback

    def subquestion(self, uuid: str, fallback: str) -> str:
        return self.subquestions.get(uuid, {}).get("text") or fallback

    def answer(self, key: AnswerKey, fallback: str) -> str:
        return self.answers.get(key) or fallback


#: 基础语言用它：任何查询都回退到传入的原文。
BASE_TEXTS = LanguageTexts()


class TranslationError(ValueError):
    """在校验不通过的定义上解析译文。"""


def check_translations(definition: SurveyDefinition) -> List[ValidationIssue]:
    return _Reader(definition).read()[1]


def parse_translations(definition: SurveyDefinition) -> Dict[str, LanguageTexts]:
    """没有 ``translations`` 返回空字典；有问题抛 TranslationError（调用方应先校验）。"""
    parsed, issues = _Reader(definition).read()
    if issues:
        raise TranslationError("; ".join("{} {}".format(i.code, i.message) for i in issues))
    return parsed


def texts_for(translations: Mapping[str, LanguageTexts], language: str) -> LanguageTexts:
    return translations.get(language, BASE_TEXTS)


class _Reader:
    def __init__(self, definition: SurveyDefinition):
        self._definition = definition
        self._issues: List[ValidationIssue] = []
        self._groups = {group.uuid for group in definition.groups}
        self._questions = {question.uuid for question in definition.questions()}
        self._subquestions = {
            sub.uuid for question in definition.questions() for sub in question.subquestions
        }
        self._answers = {
            (question.uuid, answer.code, answer.scale)
            for question in definition.questions()
            for answer in question.answers
        }

    def read(self) -> Tuple[Dict[str, LanguageTexts], List[ValidationIssue]]:
        raw = self._definition.translations
        if raw is None:
            return {}, []
        if not isinstance(raw, Mapping):
            return {}, [
                ValidationIssue(
                    "E_TRANSLATION_INVALID",
                    "translations",
                    "translations 必须是 语言→对象 的映射，收到 {}".format(type(raw).__name__),
                )
            ]
        parsed: Dict[str, LanguageTexts] = {}
        for language in sorted(raw):
            if language not in self._definition.additional_languages:
                self._issue(
                    "E_TRANSLATION_LANGUAGE",
                    language,
                    "语言 {!r} 不在 additionalLanguages 里；基础语言的文本就在定义本体中".format(language),
                )
                continue
            texts = self._language(raw[language], language)
            if texts is not None:
                parsed[language] = texts
        if self._issues:
            return {}, self._issues
        return parsed, []

    # ------------------------------------------------------------ 零件

    def _issue(self, code: str, path: str, message: str) -> None:
        self._issues.append(ValidationIssue(code, "translations.{}".format(path), message))

    def _language(self, raw: Any, language: str) -> Optional[LanguageTexts]:
        if not isinstance(raw, Mapping):
            self._issue("E_TRANSLATION_TYPE", language, "语言对象必须是对象")
            return None
        for key in sorted(set(raw) - _LANGUAGE_KEYS):
            self._issue(
                "E_TRANSLATION_UNKNOWN_KEY", "{}.{}".format(language, key), "未知的译文键 {!r}".format(key)
            )
        return LanguageTexts(
            title=self._string(raw.get("title"), "{}.title".format(language)),
            description=self._string(raw.get("description"), "{}.description".format(language)),
            groups=self._entities(raw.get("groups"), language, "groups", _GROUP_KEYS, self._groups),
            questions=self._entities(
                raw.get("questions"), language, "questions", _QUESTION_KEYS, self._questions
            ),
            subquestions=self._entities(
                raw.get("subquestions"), language, "subquestions", _SUBQUESTION_KEYS, self._subquestions
            ),
            answers=self._answer_texts(raw.get("answers"), language),
        )

    def _string(self, value: Any, path: str) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            self._issue("E_TRANSLATION_TYPE", path, "译文必须是字符串")
            return ""
        return value

    def _entities(
        self, raw: Any, language: str, section: str, keys: frozenset, known: set
    ) -> Dict[str, Dict[str, str]]:
        where = "{}.{}".format(language, section)
        if raw is None:
            return {}
        if not isinstance(raw, Mapping):
            self._issue("E_TRANSLATION_TYPE", where, "{} 必须是 uuid→对象 的映射".format(section))
            return {}
        parsed: Dict[str, Dict[str, str]] = {}
        for uuid in sorted(raw):
            path = "{}.{}".format(where, uuid)
            if uuid not in known:
                self._issue("E_TRANSLATION_TARGET", path, "定义里没有 {} 这个 {}".format(uuid, section[:-1]))
                continue
            entry = raw[uuid]
            if not isinstance(entry, Mapping):
                self._issue("E_TRANSLATION_TYPE", path, "译文条目必须是对象")
                continue
            for key in sorted(set(entry) - keys):
                self._issue(
                    "E_TRANSLATION_UNKNOWN_KEY",
                    "{}.{}".format(path, key),
                    "{} 的译文只能有 {}".format(section[:-1], "、".join(sorted(keys))),
                )
            parsed[uuid] = {
                key: self._string(entry[key], "{}.{}".format(path, key))
                for key in sorted(set(entry) & keys)
            }
        return parsed

    def _answer_texts(self, raw: Any, language: str) -> Dict[AnswerKey, str]:
        where = "{}.answers".format(language)
        if raw is None:
            return {}
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            self._issue("E_TRANSLATION_TYPE", where, "answers 必须是数组")
            return {}
        parsed: Dict[AnswerKey, str] = {}
        for index, entry in enumerate(raw):
            path = "{}[{}]".format(where, index)
            key = self._answer_key(entry, path)
            if key is None:
                continue
            if key in parsed:
                self._issue(
                    "E_TRANSLATION_DUPLICATE",
                    path,
                    "同一语言内 {} 的选项 {} 被翻译了两次".format(key[0], key[1]),
                )
                continue
            parsed[key] = self._string(entry.get("text"), path + ".text")
        return parsed

    def _answer_key(self, entry: Any, path: str) -> Optional[AnswerKey]:
        if not isinstance(entry, Mapping):
            self._issue("E_TRANSLATION_TYPE", path, "answers 的每一项必须是对象")
            return None
        for key in sorted(set(entry) - _ANSWER_KEYS):
            self._issue("E_TRANSLATION_UNKNOWN_KEY", "{}.{}".format(path, key), "未知的键 {!r}".format(key))
        question = entry.get("question")
        code = entry.get("code")
        scale = entry.get("scale", 0)
        if not isinstance(question, str) or not isinstance(code, str) or not isinstance(entry.get("text"), str):
            self._issue("E_TRANSLATION_TYPE", path, "answers 的每一项必须有 question、code、text 三个字符串")
            return None
        if isinstance(scale, bool) or not isinstance(scale, int):
            self._issue("E_TRANSLATION_TYPE", path + ".scale", "scale 必须是整数")
            return None
        key = (question, code, scale)
        if key not in self._answers:
            self._issue(
                "E_TRANSLATION_TARGET",
                path,
                "定义里没有题目 {} 的尺度 {} 选项 {}".format(question, scale, code),
            )
            return None
        return key
