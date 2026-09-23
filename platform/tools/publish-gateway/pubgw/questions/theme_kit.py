"""题型主题的骨架：一个主题长什么样，以及它的 ``themeOptions`` 怎么读。

只有形状，没有任何一个具体主题——具体主题在 ``theme_specs.py``，
列字典在 ``theme_columns.py``，对外的入口在 ``themes.py``。
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..model import Question

#: 平台自带主题的名字前缀：带这个前缀却没注册的，一定是拼错了。
PLATFORM_PREFIX = "mjy-"

UNKNOWN = "E_THEME_UNKNOWN"
TYPE_MISMATCH = "E_THEME_TYPE_MISMATCH"
OPTIONS_UNSUPPORTED = "E_THEME_OPTIONS_UNSUPPORTED"
OPTION_UNKNOWN = "E_THEME_OPTION_UNKNOWN"
OPTION_REQUIRED = "E_THEME_OPTION_REQUIRED"
OPTION_VALUE = "E_THEME_OPTION_VALUE"

#: 与 MjyRepeatingTableValidator / MjyTableColumnSpec 的代码级硬上限保持一致。
HARD_MAX_ROWS = 500
MAX_COLUMNS = 40
CELL_MAX_LENGTH = 2000
#: 与 MjyStructuredAnswerStore::normaliseStructureVersion 的字符集一致。
STRUCTURE_VERSION_PATTERN = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,31}\Z")
_COLUMN_CODE_PATTERN = re.compile(r"\A[A-Za-z][A-Za-z0-9_]{0,31}\Z")
#: 枚举列**取值**的字符集：比列代码宽一位——量表常写成 1…5，引擎的选项代码也允许数字打头。
_VALUE_CODE_PATTERN = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_-]{0,31}\Z")
_COLUMN_TYPES = ("text", "integer", "decimal", "enum")
#: 枚举列一列最多多少个取值：取值集合随 .lss 下发，不是给人塞字典用的。
MAX_COLUMN_OPTIONS = 200

#: 副表契约与两张表的名字（不带引擎表前缀）。
SIDE_TABLE_CONTRACT = "question-extension-tables-v1"
CELL_TABLE = "mjyquestionextensions_answer_cell"
STATE_TABLE = "mjyquestionextensions_answer_state"
#: 结构摘要算法版本。换算法时一并换掉它。
STRUCTURE_DIGEST_VERSION = "sd1"

#: 插件读的三个题目属性（MjyQuestionAttributeDefinitions）。
COLUMNS_ATTRIBUTE = "mjy_table_columns"
MIN_ROWS_ATTRIBUTE = "mjy_table_min_rows"
MAX_ROWS_ATTRIBUTE = "mjy_table_max_rows"
STRUCTURE_VERSION_ATTRIBUTE = "mjy_structure_version"
#: 循环评价的评价对象（R02-11）：一行一个对象，标签由主题渲染成行首。
LOOP_OBJECTS_ATTRIBUTE = "mjy_loop_objects"
#: 图片 PK（R02-17）：参赛图片与配对，主题据此渲染成对的图。
PK_ITEMS_ATTRIBUTE = "mjy_pk_items"
PK_PAIRS_ATTRIBUTE = "mjy_pk_pairs"
#: 货架题（R02-18）：货架图与商品热区，主题据此在图上画可点区域。
SHELF_IMAGE_ATTRIBUTE = "mjy_shelf_image"
SHELF_PRODUCTS_ATTRIBUTE = "mjy_shelf_products"
#: 文字点睛（R02-22）：原文与可标记的片段（偏移＋长度），主题据此把原文切成可点的片段。
HIGHLIGHT_TEXT_ATTRIBUTE = "mjy_highlight_text"
HIGHLIGHT_SEGMENTS_ATTRIBUTE = "mjy_highlight_segments"
#: 心理实验（R02-46）：试次（刺激＋正确按键）。**正确按键不进列定义**——
#: 正确与否由平台按声明推导，作答者提交不了「我答对了」。
PSYCH_TRIALS_ATTRIBUTE = "mjy_psych_trials"
#: 专业模型（R02-47）：模型名与该模型的采集对象。读端按模型名取对应的分析口径，
#: 不靠猜列名——「连接可复现分析」的锚点就是这一对属性加上结构版本。
MODEL_NAME_ATTRIBUTE = "mjy_model_name"
MODEL_FEATURES_ATTRIBUTE = "mjy_model_features"

#: 题型字母 → 引擎存放该题型视图的目录名（``QuestionTemplate::getFolderName``）。
#: 主题必须在 ``themes/question/<名字>/survey/questions/answer/<这里的值>/`` 下放 config.xml
#: 与视图，少一个目录引擎就静默降级成基础主题（ADR 0006 决定 6）。
VIEW_FOLDERS = {
    "X": "boilerplate",
    "S": "shortfreetext",
    "T": "longfreetext",
    "L": "listradio",
    "M": "multiplechoice",
    "P": "multiplechoice_with_comments",
    "F": "arrays/array",
}

Issue = Tuple[str, str, str]  # (错误码, 相对题目的路径, 消息)


@dataclass(frozen=True)
class OptionSpec:
    """一个主题选项的形状。``attribute`` 为空表示它不直接落成题目属性。"""

    name: str
    kind: str  # bool | integer | text | enum | list
    attribute: str = ""
    required: bool = False
    default: Any = None
    minimum: int = 0
    maximum: int = 0
    choices: Tuple[str, ...] = ()
    max_length: int = 0
    #: text 取值还必须匹配的形状（例如结构版本的字符集）。
    pattern: Optional[Any] = None
    pattern_hint: str = ""


@dataclass(frozen=True)
class Lowering:
    """一个主题降级后的产物。"""

    attributes: Dict[str, str] = field(default_factory=dict)
    rules: Tuple[str, ...] = ()
    tips: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ThemeSpec:
    name: str
    label: str
    requirement: str
    types: Tuple[str, ...]
    options: Tuple[OptionSpec, ...] = ()
    #: 需要题目上下文的语义检查（选项代码是否存在之类）。
    check: Optional[Callable[[Question, Dict[str, Any]], List[Issue]]] = None
    #: 属性与服务端规则；缺省时只写各选项自己的 attribute。
    lower: Optional[Callable[[Question, Dict[str, Any]], Lowering]] = None
    #: 作答投影进插件副表时的列定义生成器。
    side_columns: Optional[Callable[[Dict[str, Any]], List[Dict[str, Any]]]] = None

    @property
    def has_side_table(self) -> bool:
        return self.side_columns is not None

    def option(self, name: str) -> Optional[OptionSpec]:
        for spec in self.options:
            if spec.name == name:
                return spec
        return None

# ------------------------------------------------------------------ 取值解析


def _flag(value: Any) -> bool:
    return isinstance(value, bool)


def _int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _read_option(spec: OptionSpec, raw: Any, path: str) -> Tuple[Any, List[Issue]]:
    if spec.kind == "bool":
        return (raw, []) if _flag(raw) else (spec.default, [(OPTION_VALUE, path, "{} 必须是布尔值".format(spec.name))])
    if spec.kind == "integer":
        if not _int(raw):
            return spec.default, [(OPTION_VALUE, path, "{} 必须是整数".format(spec.name))]
        if not spec.minimum <= raw <= spec.maximum:
            return spec.default, [
                (OPTION_VALUE, path, "{} 必须在 {} 到 {} 之间".format(spec.name, spec.minimum, spec.maximum))
            ]
        return raw, []
    if spec.kind == "text":
        if not isinstance(raw, str) or raw.strip() == "":
            return spec.default, [(OPTION_VALUE, path, "{} 必须是非空字符串".format(spec.name))]
        if spec.max_length and len(raw) > spec.max_length:
            return spec.default, [(OPTION_VALUE, path, "{} 最长 {} 个字符".format(spec.name, spec.max_length))]
        if spec.pattern is not None and not spec.pattern.match(raw):
            return spec.default, [(OPTION_VALUE, path, "{} {}".format(spec.name, spec.pattern_hint))]
        return raw, []
    if spec.kind == "enum":
        if raw not in spec.choices:
            return spec.default, [
                (OPTION_VALUE, path, "{} 只能是 {}".format(spec.name, "、".join(spec.choices)))
            ]
        return raw, []
    if not isinstance(raw, list) or not raw:
        return spec.default, [(OPTION_VALUE, path, "{} 必须是非空数组".format(spec.name))]
    return raw, []

def read_options(themes: Dict[str, "ThemeSpec"], question: Question) -> Tuple[Dict[str, Any], List[Issue]]:
    """解析并校验 ``themeOptions``，返回补齐默认值后的取值与全部问题。

    题目没有用平台主题时返回空取值：不认识的主题名原样透传给引擎。
    """
    spec = themes.get(question.theme)
    if spec is None:
        if question.theme.startswith(PLATFORM_PREFIX):
            return {}, [(UNKNOWN, "theme", "未知的平台题型主题 {!r}（支持：{}）".format(
                question.theme, "、".join(sorted(themes))))]
        if question.theme_options:
            return {}, [(OPTIONS_UNSUPPORTED, "themeOptions",
                         "只有平台题型主题可以带 themeOptions，{!r} 不是".format(question.theme or "（缺省主题）"))]
        return {}, []

    issues: List[Issue] = []
    if question.type not in spec.types:
        issues.append((TYPE_MISMATCH, "theme", "题型主题 {} 只能用在题型 {} 上".format(
            spec.name, "/".join(spec.types))))
        return {}, issues

    values: Dict[str, Any] = {}
    for name in sorted(question.theme_options):
        if spec.option(name) is None:
            issues.append((OPTION_UNKNOWN, "themeOptions.{}".format(name),
                           "题型主题 {} 没有选项 {}（支持：{}）".format(
                               spec.name, name, "、".join(item.name for item in spec.options) or "无")))
    for option in spec.options:
        path = "themeOptions.{}".format(option.name)
        if option.name not in question.theme_options:
            if option.required:
                issues.append((OPTION_REQUIRED, path, "题型主题 {} 必须提供 {}".format(spec.name, option.name)))
            elif option.default is not None:
                values[option.name] = option.default
            continue
        value, found = _read_option(option, question.theme_options[option.name], path)
        issues.extend(found)
        if not found:
            values[option.name] = value
    if issues:
        return values, issues
    if spec.check is not None:
        issues.extend(spec.check(question, values))
    return values, issues


def lower_theme_options(spec: Optional["ThemeSpec"], values: Dict[str, Any]) -> Dict[str, str]:
    """各选项自己的 ``attribute``；主题另有 ``lower`` 时由调用方再合并它的产物。"""
    if spec is None:
        return {}
    return {
        option.attribute: attribute_value(values[option.name])
        for option in spec.options
        if option.attribute and option.name in values
    }


# ------------------------------------------------------------------ 降级


def attribute_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (list, dict)):
        return canonical_json(value)
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
