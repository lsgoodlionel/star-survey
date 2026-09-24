"""平台自带题型主题（``themes/question/mjy-*``）的对外入口。

题型主题只换展示，不改答卷列的形状（[ADR 0006] 决定 1 的 B 档），所以注册表里的
任何一项都不会改变 ``qtypes.expected_rows`` 的结果。主题携带的配置走题目的
``themeOptions`` 键：编译时降成题目属性，需要服务端把关的部分再降成 ``em_validation_q``
的一段（与 ``format`` / ``maxLength`` 同一条管线，见 questions/lower.py）。

C 档（副表）的主题额外声明 ``side_columns``：作答以 JSON 信封存进基础题型的那一列，
插件按列定义投影进副表，绑定记录里带上结构版本与结构摘要
（platform/contracts/question-extension-tables-v1.md）。

**不认识的主题名照旧透传**——引擎自带主题（``bootstrap_buttons`` 等）由发布后的回读
核对（publish.py 的 ``_check_question_themes``）。只有 ``mjy-`` 前缀的名字必须注册：
那是平台自己的主题，拼错了就会静默降级成基础主题。

本模块只做三件事：读取、降级、副表声明。骨架在 ``theme_kit.py``，
列字典在 ``theme_columns.py``，各个主题在 ``theme_specs.py``。
"""

from typing import Any, Dict, List, Optional, Tuple

from ..model import Question
from .theme_columns import structure_digest  # noqa: F401  （检验列字典摘要的入口）
from .theme_kit import (  # noqa: F401  （VIEW_FOLDERS 供注册表检查用）
    CELL_TABLE, Issue, Lowering, SIDE_TABLE_CONTRACT, STATE_TABLE, VIEW_FOLDERS,
    lower_theme_options, read_options,
)
from .theme_specs import (  # noqa: F401  （STRUCTURED_THEMES / 结构版本属性名是对插件公开的契约）
    MANAGED_ATTRIBUTES, STRUCTURE_VERSION_ATTRIBUTE_NAME, STRUCTURED_THEMES, THEMES,
)


def read_theme_options(question: Question) -> Tuple[Dict[str, Any], List[Issue]]:
    """解析并校验 ``themeOptions``，返回补齐默认值后的取值与全部问题。"""
    return read_options(THEMES, question)


def lower_theme(question: Question, values: Dict[str, Any]) -> Lowering:
    """校验通过之后才调用：把主题选项降成题目属性与服务端规则。"""
    spec = THEMES.get(question.theme)
    if spec is None:
        return Lowering()
    attributes = lower_theme_options(spec, values)
    if spec.lower is None:
        return Lowering(attributes=attributes)
    extra = spec.lower(question, values)
    attributes.update(extra.attributes)
    return Lowering(attributes=attributes, rules=extra.rules, tips=extra.tips)


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
