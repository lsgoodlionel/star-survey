"""副表题型的**列字典**：一列长什么样，以及列定义的结构摘要。

列定义与插件的 ``MjyTableColumnSpec`` 逐条对齐——两边不一致等于发布出去才发现。
枚举列的取值集合与唯一约束是给多个题型共用的通用约束，不属于任何一个主题。
"""

import hashlib
from typing import Any, Dict, List, Optional, Tuple

from ..model import Question
from .theme_kit import (
    CELL_MAX_LENGTH, COLUMNS_ATTRIBUTE, Issue, Lowering, MAX_COLUMNS,
    MAX_COLUMN_OPTIONS, OPTION_VALUE, STRUCTURE_DIGEST_VERSION, _COLUMN_CODE_PATTERN,
    _COLUMN_TYPES, _VALUE_CODE_PATTERN, _int, canonical_json,
)

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
    return Lowering(attributes={COLUMNS_ATTRIBUTE: canonical_json(_parse_columns(values["columns"]))})
