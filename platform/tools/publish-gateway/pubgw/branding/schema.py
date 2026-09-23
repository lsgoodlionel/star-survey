"""``branding`` 块的校验与解析（契约 survey-branding-v1 §2）。

和访问策略一样：一次遍历同时收集全部问题并构造不可变对象，有一个问题调用方就拿不到品牌。

这里最重要的不是它接受什么，而是它**不接受**什么。租户能提供的只有
颜色、纯文本与主题目录内的文件名——没有 CSS，没有 JS，没有 HTML 片段，没有 URL。
理由写在契约 §2.1：一行 ``background-image: url(...)`` 就能把作答页变成对外请求，
一行 JS 就与管理端同源。封闭取值比"安全子集解析器"可靠得多。
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ..model import SurveyDefinition
from ..validate import ValidationIssue

BRANDING_VERSION = 1

#: 唯一带品牌位的主题（themes/survey/zh-business）。
BRAND_THEME = "zh-business"

_TOP_KEYS = frozenset(
    {
        "brandingVersion",
        "primaryColor",
        "pageTitle",
        "footerText",
        "logoFile",
        "logoAlt",
        "faviconFile",
    }
)
#: 单独报错的键：作者写了它说明误以为能塞 CSS，要给出明确的理由而不是"未知键"。
_CUSTOM_CSS_KEY = "customCss"

_COLOR = re.compile(r"\A#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\Z")
#: 文件名：字母数字开头，其后只允许字母数字与 ``.``、``_``、``-``。
#: 斜杠、冒号、问号、空格一律不在其中，所以它既跳不出主题目录，也拼不出 URL。
_ASSET_NAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp")
#: svg 不在其中：它是可以带脚本的文档，不是图片。
_FAVICON_SUFFIXES = _IMAGE_SUFFIXES + (".ico",)
_MAX_TEXT_LENGTH = 500


@dataclass(frozen=True)
class BrandText:
    """一段品牌文本：默认值 ＋ 按语言的覆盖。"""

    default: str = ""
    by_language: Dict[str, str] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.default and not self.by_language


@dataclass(frozen=True)
class Branding:
    primary_color: str = ""
    page_title: BrandText = BrandText()
    footer_text: BrandText = BrandText()
    logo_file: str = ""
    logo_alt: str = ""
    favicon_file: str = ""


class BrandingError(ValueError):
    """在校验不通过的定义上解析品牌。"""


def check_branding(definition: SurveyDefinition) -> List[ValidationIssue]:
    return _Reader(definition).read()[1]


def parse_branding(definition: SurveyDefinition) -> Optional[Branding]:
    """定义没有 ``branding`` 返回 None；有问题抛 BrandingError（调用方应先校验）。"""
    branding, issues = _Reader(definition).read()
    if issues:
        raise BrandingError("; ".join("{} {}".format(i.code, i.message) for i in issues))
    return branding


def languages_of(definition: SurveyDefinition) -> Tuple[str, ...]:
    return (definition.language,) + definition.additional_languages


class _Reader:
    def __init__(self, definition: SurveyDefinition):
        self._definition = definition
        self._issues: List[ValidationIssue] = []

    def read(self) -> Tuple[Optional[Branding], List[ValidationIssue]]:
        raw = self._definition.branding
        if raw is None:
            return None, []
        if not isinstance(raw, Mapping):
            return None, [
                ValidationIssue(
                    "E_BRAND_INVALID", "branding", "branding 必须是对象，收到 {}".format(type(raw).__name__)
                )
            ]
        self._check_version(raw)
        self._check_theme()
        self._check_unknown_keys(raw)
        branding = Branding(
            primary_color=self._color(raw.get("primaryColor")),
            page_title=self._text(raw.get("pageTitle"), "pageTitle"),
            footer_text=self._text(raw.get("footerText"), "footerText"),
            logo_file=self._asset(raw.get("logoFile"), "logoFile", _IMAGE_SUFFIXES),
            logo_alt=self._plain(raw.get("logoAlt"), "logoAlt"),
            favicon_file=self._asset(raw.get("faviconFile"), "faviconFile", _FAVICON_SUFFIXES),
        )
        if self._issues:
            return None, self._issues
        return branding, []

    # ------------------------------------------------------------ 零件

    def _fail(self, code: str, key: str, message: str) -> None:
        self._issues.append(ValidationIssue(code, "branding.{}".format(key), message))

    def _check_version(self, raw: Mapping[str, Any]) -> None:
        version = raw.get("brandingVersion")
        if isinstance(version, bool) or version != BRANDING_VERSION:
            self._fail(
                "E_BRAND_VERSION",
                "brandingVersion",
                "brandingVersion 必须是 {}，收到 {!r}".format(BRANDING_VERSION, version),
            )

    def _check_theme(self) -> None:
        if self._definition.theme == BRAND_THEME:
            return
        self._issues.append(
            ValidationIssue(
                "E_BRAND_THEME",
                "theme",
                "带 branding 的问卷主题必须是 {!r}，收到 {!r}：只有它有品牌位".format(
                    BRAND_THEME, self._definition.theme
                ),
            )
        )

    def _check_unknown_keys(self, raw: Mapping[str, Any]) -> None:
        if _CUSTOM_CSS_KEY in raw:
            self._fail(
                "E_BRAND_CUSTOM_CSS_UNSUPPORTED",
                _CUSTOM_CSS_KEY,
                "不支持租户自定义 CSS：一行 url() 就能让作答页向第三方发请求，"
                "属性选择器还能外传已填内容。品牌能力只开放颜色、文本与主题内的图片",
            )
        for key in sorted(set(raw) - _TOP_KEYS - {_CUSTOM_CSS_KEY}):
            self._fail("E_BRAND_UNKNOWN_KEY", key, "未知的品牌键 {!r}；不接受任何 HTML 或脚本片段".format(key))

    def _color(self, value: Any) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            self._fail("E_BRAND_TYPE", "primaryColor", "primaryColor 必须是字符串")
            return ""
        if not _COLOR.match(value):
            self._fail(
                "E_BRAND_COLOR",
                "primaryColor",
                "颜色只接受 #rgb 或 #rrggbb，收到 {!r}；不接受函数式或变量式取值".format(value),
            )
            return ""
        return value.lower()

    def _plain(self, value: Any, key: str) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            self._fail("E_BRAND_TYPE", key, "{} 必须是字符串".format(key))
            return ""
        if len(value) > _MAX_TEXT_LENGTH:
            self._fail("E_BRAND_TYPE", key, "{} 最长 {} 个字符".format(key, _MAX_TEXT_LENGTH))
            return ""
        return value

    def _text(self, value: Any, key: str) -> BrandText:
        if value is None:
            return BrandText()
        if isinstance(value, str):
            return BrandText(default=self._plain(value, key))
        if not isinstance(value, Mapping):
            self._fail("E_BRAND_TYPE", key, "{} 必须是字符串或 语言→字符串 的对象".format(key))
            return BrandText()

        allowed = languages_of(self._definition)
        by_language: Dict[str, str] = {}
        for language in sorted(value):
            if language not in allowed:
                self._fail(
                    "E_BRAND_LANGUAGE",
                    "{}.{}".format(key, language),
                    "语言 {!r} 不在问卷的语言列表 {} 里".format(language, ", ".join(allowed)),
                )
                continue
            by_language[language] = self._plain(value[language], "{}.{}".format(key, language))
        return BrandText(
            default=by_language.get(self._definition.language, ""), by_language=by_language
        )

    def _asset(self, value: Any, key: str, suffixes: Tuple[str, ...]) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            self._fail("E_BRAND_TYPE", key, "{} 必须是字符串".format(key))
            return ""
        if not _ASSET_NAME.match(value) or ".." in value:
            self._fail(
                "E_BRAND_ASSET",
                key,
                "{} 只能是主题 files/ 目录下的文件名（字母数字开头，只含字母数字与 . _ -），"
                "收到 {!r}；不接受路径、URL 或 data:".format(key, value),
            )
            return ""
        if not value.lower().endswith(suffixes):
            self._fail(
                "E_BRAND_ASSET",
                key,
                "{} 的扩展名必须是 {} 之一，收到 {!r}".format(key, "、".join(suffixes), value),
            )
            return ""
        return value
