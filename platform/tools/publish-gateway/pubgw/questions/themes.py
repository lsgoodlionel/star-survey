"""平台自带题型主题（``themes/question/mjy-*``）的声明式注册表。

题型主题只换展示，不改答卷列的形状（[ADR 0006] 决定 1 的 B 档），所以本模块里的
任何一项都不会改变 ``qtypes.expected_rows`` 的结果。主题携带的配置走题目的
``themeOptions`` 键：编译时降成题目属性，需要服务端把关的部分再降成 ``em_validation_q``
的一段（与 ``format`` / ``maxLength`` 同一条管线，见 questions/lower.py）。

C 档（副表）的主题额外声明 ``side_table``：作答以 JSON 信封存进基础题型的那一列，
插件按列定义投影进副表，绑定记录里带上结构版本与结构摘要
（platform/contracts/question-extension-tables-v1.md）。

**不认识的主题名照旧透传**——引擎自带主题（``bootstrap_buttons`` 等）由发布后的回读
核对（publish.py 的 ``_check_question_themes``）。只有 ``mjy-`` 前缀的名字必须在这里注册：
那是平台自己的主题，拼错了就会静默降级成基础主题。
"""

import hashlib
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


def read_theme_options(question: Question) -> Tuple[Dict[str, Any], List[Issue]]:
    """解析并校验 ``themeOptions``，返回补齐默认值后的取值与全部问题。

    题目没有用平台主题时返回空取值：不认识的主题名原样透传给引擎。
    """
    spec = THEMES.get(question.theme)
    if spec is None:
        if question.theme.startswith(PLATFORM_PREFIX):
            return {}, [(UNKNOWN, "theme", "未知的平台题型主题 {!r}（支持：{}）".format(
                question.theme, "、".join(sorted(THEMES))))]
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


# ------------------------------------------------------------------ 降级


def lower_theme(question: Question, values: Dict[str, Any]) -> Lowering:
    """校验通过之后才调用：把主题选项降成题目属性与服务端规则。"""
    spec = THEMES.get(question.theme)
    if spec is None:
        return Lowering()
    attributes = {
        option.attribute: _attribute_value(values[option.name])
        for option in spec.options
        if option.attribute and option.name in values
    }
    if spec.lower is None:
        return Lowering(attributes=attributes)
    extra = spec.lower(question, values)
    attributes.update(extra.attributes)
    return Lowering(attributes=attributes, rules=extra.rules, tips=extra.tips)


def _attribute_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (list, dict)):
        return _canonical_json(value)
    return str(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


# ------------------------------------------------------------------ 副表声明


def side_table_binding(question: Question) -> Optional[Dict[str, Any]]:
    """这道题的副表声明；不走副表的题目返回 None。

    结构版本由平台声明（作者端必须在改列定义时换一个），结构摘要由列定义算出，
    用来在重新发布时发现「改了结构却没换版本」。
    """
    spec = THEMES.get(question.theme)
    if spec is None or not spec.has_side_table:
        return None
    values, issues = read_theme_options(question)
    if issues:
        return None
    columns = spec.side_columns(values)
    return {
        "contract": SIDE_TABLE_CONTRACT,
        "cellTable": CELL_TABLE,
        "stateTable": STATE_TABLE,
        "structureVersion": str(values["structureVersion"]),
        "structureDigest": structure_digest(columns),
        "columns": columns,
    }


def structure_digest(columns: List[Dict[str, Any]]) -> str:
    """列定义的摘要：列序、列代码、类型与约束全进去，标签不进去（改标签不影响读回）。

    枚举列的**取值集合**与唯一约束也算结构：改了可选项等于换了一本字典，
    早先的答卷必须按它自己那一版读回（副表契约 v1 第二节）。
    """
    lines = [
        "{}|{}|{}|{}|{}|{}|{}|{}".format(
            column["code"], column["type"], "1" if column.get("required") else "0",
            column.get("maxLength", ""), column.get("min", ""), column.get("max", ""),
            ",".join(option["code"] for option in column.get("options", ())),
            "1" if column.get("distinct") else "0",
        )
        for column in columns
    ]
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    return "{}:{}".format(STRUCTURE_DIGEST_VERSION, digest[:16])


# ------------------------------------------------------------------ 各主题


def _code_list(raw: Any, path: str, limit: int, pattern: Any = None) -> Tuple[List[Dict[str, str]], List[Issue]]:
    """一串「代码＋标签」的取值，写成 ``["A","B"]`` 或 ``[{"code":"A","label":"优"}]`` 都可以。

    返回规范形式（键序固定，缺省标签取代码本身）。给枚举列的取值集合与循环评价的
    对象／维度／量表共用：它们的形状与约束完全一样，两份实现迟早会漂。

    ``pattern`` 缺省是**取值**的字符集（可以数字打头，量表常写成 1…5）；
    要当列代码用的那几处（循环评价的维度）自己传列代码的字符集。
    """
    shape = pattern if pattern is not None else _VALUE_CODE_PATTERN
    if not isinstance(raw, list) or not raw:
        return [], [(OPTION_VALUE, path, "必须是非空数组")]
    if len(raw) > limit:
        return [], [(OPTION_VALUE, path, "最多 {} 项".format(limit))]
    items: List[Dict[str, str]] = []
    issues: List[Issue] = []
    seen = set()
    for index, entry in enumerate(raw):
        where = "{}[{}]".format(path, index)
        code = entry if isinstance(entry, str) else (entry.get("code") if isinstance(entry, dict) else None)
        if not isinstance(code, str) or not shape.match(code):
            issues.append((OPTION_VALUE, where + ".code", "代码不合法：{!r}".format(code)))
            continue
        label = entry.get("label") if isinstance(entry, dict) else None
        if label is not None and not isinstance(label, str):
            issues.append((OPTION_VALUE, where + ".label", "label 必须是字符串"))
            continue
        if code in seen:
            issues.append((OPTION_VALUE, where + ".code", "代码重复：{}".format(code)))
            continue
        seen.add(code)
        items.append({"code": code, "label": label or code})
    return (([], issues) if issues else (items, []))


def _enum_issues(raw: Dict[str, Any], column: Dict[str, Any], path: str) -> List[Issue]:
    """枚举列的取值集合：枚举列必须有，别的列不许有。"""
    declared = raw.get("options")
    if column["type"] != "enum":
        if declared is not None:
            return [(OPTION_VALUE, path + ".options", "只有 enum 列可以声明 options")]
        return []
    if declared is None:
        return [(OPTION_VALUE, path + ".options", "enum 列必须声明 options")]
    options, issues = _code_list(declared, path + ".options", MAX_COLUMN_OPTIONS)
    if issues:
        return issues
    column["options"] = options
    return []


def _column_issues(raw: Any, index: int) -> Tuple[Optional[Dict[str, Any]], List[Issue]]:
    """一列的形状，与插件的 MjyTableColumnSpec 逐条对齐——两边不一致等于发布出去才发现。"""
    path = "themeOptions.columns[{}]".format(index)
    if not isinstance(raw, dict):
        return None, [(OPTION_VALUE, path, "每一列都必须是对象")]
    code = raw.get("code")
    if not isinstance(code, str) or not _COLUMN_CODE_PATTERN.match(code):
        return None, [(OPTION_VALUE, path + ".code", "列代码必须以字母开头，只含字母数字与下划线，最长 32 位")]
    column_type = raw.get("type", "text")
    if column_type not in _COLUMN_TYPES:
        return None, [(OPTION_VALUE, path + ".type", "列类型只能是 {}".format("、".join(_COLUMN_TYPES)))]
    column: Dict[str, Any] = {
        "code": code,
        "label": raw.get("label") if isinstance(raw.get("label"), str) and raw.get("label") else code,
        "type": column_type,
        "required": bool(raw.get("required")),
    }
    issues: List[Issue] = _enum_issues(raw, column, path)
    if raw.get("distinct"):
        # 唯一约束：同一列的非空取值在一次作答里不得重复（插件逐行判）。
        column["distinct"] = True
    length = raw.get("maxLength")
    if length is not None:
        if not _int(length) or not 1 <= length <= CELL_MAX_LENGTH:
            issues.append((OPTION_VALUE, path + ".maxLength", "maxLength 必须在 1 到 {} 之间".format(CELL_MAX_LENGTH)))
        else:
            column["maxLength"] = length
    for bound in ("min", "max"):
        value = raw.get(bound)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            issues.append((OPTION_VALUE, "{}.{}".format(path, bound), "{} 必须是数字".format(bound)))
            continue
        if column_type in ("text", "enum"):
            issues.append((OPTION_VALUE, "{}.{}".format(path, bound), "非数值列不能设数值上下限"))
            continue
        column[bound] = value
    if "min" in column and "max" in column and column["min"] > column["max"]:
        issues.append((OPTION_VALUE, path + ".min", "min 不能大于 max"))
    return (None, issues) if issues else (column, [])


def _check_columns(question: Question, values: Dict[str, Any]) -> List[Issue]:
    columns = values.get("columns") or []
    issues: List[Issue] = []
    if len(columns) > MAX_COLUMNS:
        issues.append((OPTION_VALUE, "themeOptions.columns", "列数不能超过 {}".format(MAX_COLUMNS)))
    seen = set()
    for index, raw in enumerate(columns):
        column, found = _column_issues(raw, index)
        issues.extend(found)
        if column is None:
            continue
        if column["code"] in seen:
            issues.append((OPTION_VALUE, "themeOptions.columns[{}].code".format(index),
                           "列代码重复：{}".format(column["code"])))
        seen.add(column["code"])
    issues.extend(_check_rows(values))
    return issues


def _parse_columns(raw_columns: Any) -> List[Dict[str, Any]]:
    """校验通过之后才调用：把列定义转成插件认识的规范形式（键序固定）。"""
    return [column for column, _ in (_column_issues(raw, index) for index, raw in enumerate(raw_columns))
            if column is not None]


def _check_rows(values: Dict[str, Any]) -> List[Issue]:
    least, most = values.get("minRows", 0), values.get("maxRows", 0)
    if least > most:
        return [(OPTION_VALUE, "themeOptions.minRows", "minRows 不能大于 maxRows")]
    return []


def _lower_table(question: Question, values: Dict[str, Any]) -> Lowering:
    return Lowering(attributes={COLUMNS_ATTRIBUTE: _canonical_json(_parse_columns(values["columns"]))})


# ---------------------------------------------------------------- 循环评价（R02-11）

#: 评价对象那一列的代码。维度不许叫这个名字，否则对象列会被顶掉。
LOOP_OBJECT_COLUMN = "target"


def _loop_lists(values: Dict[str, Any]) -> Tuple[List[Dict[str, str]], List[Dict[str, str]],
                                                 List[Dict[str, str]], List[Issue]]:
    """解析三份清单，顺带把三处 422 合在一起报出去。"""
    objects, issues = _code_list(values.get("objects"), "themeOptions.objects", HARD_MAX_ROWS)
    # 维度直接当列代码用，所以按列代码的字符集判（不能数字打头）。
    dimensions, found = _code_list(values.get("dimensions"), "themeOptions.dimensions",
                                   MAX_COLUMNS - 1, _COLUMN_CODE_PATTERN)
    issues.extend(found)
    scale, found = _code_list(values.get("scale"), "themeOptions.scale", MAX_COLUMN_OPTIONS)
    issues.extend(found)
    return objects, dimensions, scale, issues


def _check_loop_rating(question: Question, values: Dict[str, Any]) -> List[Issue]:
    objects, dimensions, _scale, issues = _loop_lists(values)
    if issues:
        return issues
    collision = [item["code"] for item in dimensions if item["code"] == LOOP_OBJECT_COLUMN]
    if collision:
        issues.append((OPTION_VALUE, "themeOptions.dimensions",
                       "维度代码不能叫 {}：那是评价对象列".format(LOOP_OBJECT_COLUMN)))
    if not objects:
        issues.append((OPTION_VALUE, "themeOptions.objects", "至少要有一个评价对象"))
    return issues


def _loop_rating_columns(values: Dict[str, Any]) -> List[Dict[str, Any]]:
    """一行一个评价对象：第一列认对象（枚举＋唯一），其余每个维度一列（枚举到量表）。

    行数在 ``_lower_loop_rating`` 里被钉死成对象个数，「枚举＋唯一＋行数」三者
    合起来就逼出「每个对象恰好评一次」，不必再写一套按行下标的规则。
    """
    objects, dimensions, scale, _issues = _loop_lists(values)
    columns = [{"code": LOOP_OBJECT_COLUMN, "label": "评价对象", "type": "enum",
                "required": True, "options": objects, "distinct": True}]
    columns.extend({"code": item["code"], "label": item["label"], "type": "enum",
                    "required": True, "options": scale} for item in dimensions)
    return columns


def _lower_loop_rating(question: Question, values: Dict[str, Any]) -> Lowering:
    objects, _dimensions, _scale, _issues = _loop_lists(values)
    count = str(len(objects))
    return Lowering(attributes={
        COLUMNS_ATTRIBUTE: _canonical_json(_loop_rating_columns(values)),
        LOOP_OBJECTS_ATTRIBUTE: _canonical_json(objects),
        # 每个对象各一行，不多不少：行数不是作答者能改的东西。
        MIN_ROWS_ATTRIBUTE: count,
        MAX_ROWS_ATTRIBUTE: count,
    })


def _heatmap_columns(values: Dict[str, Any]) -> List[Dict[str, Any]]:
    """归一化坐标：两列 decimal，范围写死在 0…1，服务端由插件逐格校验。"""
    return [
        {"code": "x", "label": "横向比例", "type": "decimal", "required": True, "min": 0, "max": 1},
        {"code": "y", "label": "纵向比例", "type": "decimal", "required": True, "min": 0, "max": 1},
    ]


def _lower_heatmap(question: Question, values: Dict[str, Any]) -> Lowering:
    return Lowering(attributes={COLUMNS_ATTRIBUTE: _canonical_json(_heatmap_columns(values))})


def _grouped_option_codes(question: Question) -> List[str]:
    """本题「一个选项行一个代码」的那一批代码。

    单选（``L``）的选项是答案选项，多选（``M``）的选项是子题——两边的代码都在答卷里
    对应一列（多选是子题列，单选是同一列的取值），但来源不是同一张表，不能混用。
    「其他」项没有代码，不参与分组。
    """
    if question.type == "M":
        return [sub.code for sub in question.subquestions]
    return [answer.code for answer in question.answers_on_scale(0)]


def _check_groups(question: Question, values: Dict[str, Any]) -> List[Issue]:
    """分组必须恰好覆盖本题的全部选项：漏掉一个就是拼错了，别让它在页面上消失。"""
    codes = _grouped_option_codes(question)
    issues: List[Issue] = []
    listed: List[str] = []
    for index, raw in enumerate(values.get("groups") or []):
        path = "themeOptions.groups[{}]".format(index)
        if not isinstance(raw, dict) or not isinstance(raw.get("label"), str) or not raw["label"].strip():
            issues.append((OPTION_VALUE, path + ".label", "每一组都要有非空的 label"))
            continue
        members = raw.get("codes")
        if not isinstance(members, list) or not members or any(not isinstance(item, str) for item in members):
            issues.append((OPTION_VALUE, path + ".codes", "每一组的 codes 必须是非空字符串数组"))
            continue
        listed.extend(members)
    if issues:
        return issues
    unknown = sorted(set(listed) - set(codes))
    if unknown:
        issues.append((OPTION_VALUE, "themeOptions.groups", "分组里的 {} 不是本题的选项代码".format("、".join(unknown))))
    duplicated = sorted({code for code in listed if listed.count(code) > 1})
    if duplicated:
        issues.append((OPTION_VALUE, "themeOptions.groups", "选项 {} 被分进了多个组".format("、".join(duplicated))))
    missing = [code for code in codes if code not in listed]
    if missing:
        issues.append((OPTION_VALUE, "themeOptions.groups", "选项 {} 没有分组".format("、".join(missing))))
    return issues


def _lower_groups(question: Question, values: Dict[str, Any]) -> Lowering:
    groups = [{"label": group["label"], "codes": list(group["codes"])} for group in values["groups"]]
    return Lowering(attributes={"mjy_option_groups": _canonical_json(groups)})


def _check_scan(question: Question, values: Dict[str, Any]) -> List[Issue]:
    """扫码得到的字符串完全由客户端决定，没有长度闸门就等于没有闸门。"""
    if question.max_length is None:
        return [(OPTION_REQUIRED, "maxLength", "扫码题必须设 maxLength：扫到的内容整串由客户端提交")]
    return []


def _lower_inline_blank(question: Question, values: Dict[str, Any]) -> Lowering:
    """清值策略用引擎自带的 commented_checkbox=checked（服务端 _ValidateQuestion 重算），
    长度上限没有原生属性，降成 em_validation_q 的一段。"""
    limit = values["blankMaxLength"]
    columns = ["{}_{}comment.NAOK".format(question.code, sub.code) for sub in question.subquestions]
    if question.other:
        columns.append("{}_othercomment.NAOK".format(question.code))
    rules = tuple(
        "(is_empty({v}) or strlen(html_entity_decode({v})) <= {n})".format(v=column, n=limit)
        for column in columns
    )
    return Lowering(
        attributes={"commented_checkbox": "checked"},
        rules=rules,
        tips=("每处填空最多 {} 个字".format(limit),) if rules else (),
    )


_THEMES = (
    ThemeSpec(
        name="mjy-collapsible",
        label="折叠栏目",
        requirement="R02-43",
        types=("X",),
        options=(
            OptionSpec("summary", "text", attribute="mjy_collapse_summary", required=True, max_length=120),
            OptionSpec("collapsed", "bool", attribute="mjy_collapse_default", default=True),
        ),
    ),
    ThemeSpec(
        name="mjy-scan-input",
        label="扫码录入",
        requirement="R02-28",
        types=("S",),
        options=(
            OptionSpec("scanFormat", "enum", attribute="mjy_scan_format", default="qr",
                       choices=("qr", "barcode", "any")),
            OptionSpec("manualEntry", "bool", attribute="mjy_scan_manual", default=True),
        ),
        check=_check_scan,
    ),
    ThemeSpec(
        name="mjy-grouped-options",
        label="选项分类",
        requirement="R02-04",
        # 单选按 answers 分组，多选按 subquestions 分组（_grouped_option_codes）。
        # 多选的选项行由 rows/*.twig 包含进来，主题连行模板一起接管，
        # 把子题代码打进行标记，浏览器端才认得出哪一行属于哪一组。
        types=("L", "M"),
        options=(
            OptionSpec("groups", "list", required=True),
            OptionSpec("collapsible", "bool", attribute="mjy_option_groups_collapsible", default=False),
        ),
        check=_check_groups,
        lower=_lower_groups,
    ),
    ThemeSpec(
        name="mjy-matrix-stepper",
        label="矩阵单题作答",
        requirement="R02-14",
        types=("F",),
        options=(
            OptionSpec("rowsPerStep", "integer", attribute="mjy_stepper_rows", default=1, minimum=1, maximum=10),
            OptionSpec("showProgress", "bool", attribute="mjy_stepper_progress", default=True),
        ),
    ),
    ThemeSpec(
        name="mjy-inline-blank",
        label="选项内嵌填空",
        requirement="R02-07",
        types=("P",),
        options=(
            OptionSpec("blankLabel", "text", attribute="mjy_inline_blank_label", default="补充", max_length=40),
            OptionSpec("blankMaxLength", "integer", attribute="mjy_inline_blank_max", required=True,
                       minimum=1, maximum=CELL_MAX_LENGTH),
        ),
        lower=_lower_inline_blank,
    ),
    ThemeSpec(
        name="mjy-repeating-table",
        label="自增表格",
        requirement="R02-13",
        types=("T",),
        options=(
            OptionSpec("structureVersion", "text", attribute=STRUCTURE_VERSION_ATTRIBUTE, required=True,
                       max_length=32, pattern=STRUCTURE_VERSION_PATTERN,
                       pattern_hint="必须以字母或数字开头，只含字母数字与 . _ -（副表契约 v1）"),
            OptionSpec("columns", "list", required=True),
            OptionSpec("minRows", "integer", attribute=MIN_ROWS_ATTRIBUTE, default=0, minimum=0, maximum=HARD_MAX_ROWS),
            OptionSpec("maxRows", "integer", attribute=MAX_ROWS_ATTRIBUTE, default=20, minimum=1, maximum=HARD_MAX_ROWS),
        ),
        check=_check_columns,
        lower=_lower_table,
        side_columns=lambda values: _parse_columns(values["columns"]),
    ),
    ThemeSpec(
        name="mjy-heatmap",
        label="热力图选区",
        requirement="R02-19",
        types=("T",),
        options=(
            OptionSpec("structureVersion", "text", attribute=STRUCTURE_VERSION_ATTRIBUTE, required=True,
                       max_length=32, pattern=STRUCTURE_VERSION_PATTERN,
                       pattern_hint="必须以字母或数字开头，只含字母数字与 . _ -（副表契约 v1）"),
            OptionSpec("image", "text", attribute="mjy_heatmap_image", required=True, max_length=500),
            OptionSpec("minPoints", "integer", attribute=MIN_ROWS_ATTRIBUTE, default=0, minimum=0, maximum=HARD_MAX_ROWS),
            OptionSpec("maxPoints", "integer", attribute=MAX_ROWS_ATTRIBUTE, default=10, minimum=1, maximum=HARD_MAX_ROWS),
        ),
        check=lambda question, values: (
            [(OPTION_VALUE, "themeOptions.minPoints", "minPoints 不能大于 maxPoints")]
            if values.get("minPoints", 0) > values.get("maxPoints", 0) else []
        ),
        lower=_lower_heatmap,
        side_columns=_heatmap_columns,
    ),
    ThemeSpec(
        name="mjy-loop-rating",
        label="循环评价",
        requirement="R02-11",
        types=("T",),
        options=(
            OptionSpec("structureVersion", "text", attribute=STRUCTURE_VERSION_ATTRIBUTE, required=True,
                       max_length=32, pattern=STRUCTURE_VERSION_PATTERN,
                       pattern_hint="必须以字母或数字开头，只含字母数字与 . _ -（副表契约 v1）"),
            # 三份清单都不落成各自的属性：对象进 mjy_loop_objects，
            # 维度与量表已经在生成的列定义里，再存一份等于埋一个会漂的副本。
            OptionSpec("objects", "list", required=True),
            OptionSpec("dimensions", "list", required=True),
            OptionSpec("scale", "list", required=True),
        ),
        check=_check_loop_rating,
        lower=_lower_loop_rating,
        side_columns=_loop_rating_columns,
    ),
)

THEMES: Dict[str, ThemeSpec] = {theme.name: theme for theme in _THEMES}

#: 作答走 JSON 信封＋副表的主题（插件按它认题，见 MjyThemedQuestionMap）。
STRUCTURED_THEMES = tuple(theme.name for theme in _THEMES if theme.has_side_table)

#: 结构版本必须是这个形状才会被插件采纳，否则回落到「不知道是哪一版」。
STRUCTURE_VERSION_ATTRIBUTE_NAME = STRUCTURE_VERSION_ATTRIBUTE

#: 校验时拒绝作者直写的属性：它们由 themeOptions 生成，两边都写等于埋一个冲突。
MANAGED_ATTRIBUTES = frozenset(
    {COLUMNS_ATTRIBUTE, MIN_ROWS_ATTRIBUTE, MAX_ROWS_ATTRIBUTE, STRUCTURE_VERSION_ATTRIBUTE,
     LOOP_OBJECTS_ATTRIBUTE, "mjy_option_groups", "commented_checkbox"}
)
