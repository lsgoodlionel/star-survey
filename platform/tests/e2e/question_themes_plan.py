"""question_themes.py 的作答计划：合法答案、只答必答题的空答路径，以及篡改用例。

列一律写成 (题目代码, aid, 尺度)，由驱动按绑定映射换成引擎字段名。
副表题型（自增表格、热力图）提交的是整块 JSON 信封——插件会重新解析并归一化它，
浏览器端算出来的任何东西都不作数。
"""

import json
from typing import Any, Dict, List, Tuple

Column = Tuple[str, str, int]

PAGES = ("展示型主题", "副表题型")
#: 每页用来判断「是否还停在本页」的必答题。
MARKERS = ("QGRP", "QTABLE")

#: 页面上必须出现的主题标记：主题装错或 twig 触发沙箱限制时这里立刻发现，
#: 而不是等到有人来作答（ADR 0006 限制 6：题型主题要纳入发布前的冒烟渲染）。
THEME_MARKERS = (
    (
        "data-mjy-collapsible",
        "mjy-collapsible.js",
        "data-mjy-option-groups",
        "mjy-grouped-options.js",
        # 多选分支接管了 rows/*.twig：行标记只可能来自本主题的行模板，
        # 它出现就说明模板真的被用上了，而不是静默退回 core 的行。
        "data-mjy-code=",
        "data-mjy-scan",
        "mjy-scan-input.js",
        "data-mjy-stepper",
        "mjy-matrix-stepper.js",
        "data-mjy-inline-blank",
        "mjy-inline-blank.js",
    ),
    (
        "data-mjy-repeating-table",
        "mjy-repeating-table.js",
        "data-mjy-loop-rating",
        "mjy-loop-rating.js",
        "data-mjy-heatmap",
        "mjy-heatmap.js",
    ),
)

#: 扫码题的 maxLength（与 fixture 一致）；填空的长度上限。
SCAN_MAX_LENGTH = 12
BLANK_MAX_LENGTH = 8


def envelope(rows: List[Dict[str, str]], version: int = 1) -> str:
    return json.dumps({"v": version, "rows": rows}, ensure_ascii=False, separators=(",", ":"))


TABLE_ROWS = [
    # 单元格里放 & 与 <：信封要原样存下来，不能被实体编码或净化改写。
    {"item": "苹果 & <汁>", "qty": "2"},
    {"item": "纸巾", "qty": "99"},
    {"item": "电池", "qty": "1"},
]
#: 坐标取两端：0 与 1 都在允许范围内，闭区间不能被写成开区间。
HEAT_ROWS = [{"x": "0.0000", "y": "1.0000"}, {"x": "0.5000", "y": "0.5000"}]

#: 循环评价：一行一个评价对象，行序与 themeOptions.objects 一致，取值取量表两端。
LOOP_ROWS = [
    {"target": "B1", "price": "1", "service": "3"},
    {"target": "B2", "price": "3", "service": "2"},
]


def valid_pages() -> List[Dict[Column, str]]:
    return [
        {
            ("QGRP", "", 0): "A3",
            # 多选分组：勾两个（分属两组），「其他」填字，不勾的那一项留空。
            ("QGRPM", "M1", 0): "Y", ("QGRPM", "M3", 0): "Y",
            ("QGRPM", "other", 0): "枇杷 & <梨>",
            ("QSCAN", "", 0): "6" * SCAN_MAX_LENGTH,
            ("QSTEP", "R1", 0): "L1", ("QSTEP", "R2", 0): "L2",
            ("QSTEP", "R3", 0): "L3", ("QSTEP", "R4", 0): "L1",
            ("QBLANK", "S1", 0): "Y", ("QBLANK", "S1comment", 0): "X9&<型>",
        },
        {
            ("QTABLE", "", 0): envelope(TABLE_ROWS),
            ("QLOOP", "", 0): envelope(LOOP_ROWS),
            ("QHEAT", "", 0): envelope(HEAT_ROWS),
        },
    ]


def blank_pages() -> List[Dict[Column, str]]:
    """只答必答题：数组题的每一行都必答，副表题型各取下限。"""
    return [
        {
            ("QGRP", "", 0): "A1",
            ("QSTEP", "R1", 0): "L1", ("QSTEP", "R2", 0): "L1",
            ("QSTEP", "R3", 0): "L1", ("QSTEP", "R4", 0): "L1",
        },
        {
            ("QTABLE", "", 0): envelope([{"item": "米", "qty": "1"}]),
            # 循环评价的行数被钉死成对象个数，没有「取下限」这回事。
            ("QLOOP", "", 0): envelope(LOOP_ROWS),
            ("QHEAT", "", 0): envelope([{"x": "0.2500", "y": "0.7500"}]),
        },
    ]


def _tamper(label: str, page: int, answers: Dict[Column, str], **extra: Any) -> Dict[str, Any]:
    payload = {"label": label, "page": page, "answers": answers}
    payload.update(extra)
    return payload


TAMPERS = (
    # 第 1 页：展示型主题的数据形状与原生题一致，闸门是引擎自己的。
    _tamper("grouped options: a code outside the answer list", 0, {("QGRP", "", 0): "A9"}),
    _tamper("grouped options (multiple choice): one selection over max_answers", 0,
            {("QGRPM", "M2", 0): "Y"}),
    _tamper("scan input: one character over maxLength", 0,
            {("QSCAN", "", 0): "6" * (SCAN_MAX_LENGTH + 1)}),
    _tamper("inline blank: comment filled while its option is unchecked", 0,
            {("QBLANK", "S1", 0): "", ("QBLANK", "S1comment", 0): "偷填"}),
    _tamper("inline blank: comment over blankMaxLength", 0,
            {("QBLANK", "S1", 0): "Y", ("QBLANK", "S1comment", 0): "一" * (BLANK_MAX_LENGTH + 1)}),
    _tamper("matrix stepper: only the first row answered", 0,
            {("QSTEP", "R2", 0): "", ("QSTEP", "R3", 0): "", ("QSTEP", "R4", 0): ""}),
    # 第 2 页：副表题型，闸门是插件的 beforeSurveyPage ＋ 题目必答。
    _tamper("repeating table: one row over maxRows", 1,
            {("QTABLE", "", 0): envelope(TABLE_ROWS + [{"item": "多的一行", "qty": "1"}])}),
    _tamper("repeating table: quantity below the column minimum", 1,
            {("QTABLE", "", 0): envelope([{"item": "米", "qty": "0"}])}),
    _tamper("repeating table: quantity is not an integer", 1,
            {("QTABLE", "", 0): envelope([{"item": "米", "qty": "1.5"}])}),
    _tamper("repeating table: a column nobody declared", 1,
            {("QTABLE", "", 0): envelope([{"item": "米", "qty": "1", "price": "9"}])}),
    _tamper("repeating table: required cell left empty", 1,
            {("QTABLE", "", 0): envelope([{"item": "", "qty": "1"}])}),
    _tamper("repeating table: wrong envelope version", 1,
            {("QTABLE", "", 0): envelope([{"item": "米", "qty": "1"}], version=2)}),
    _tamper("repeating table: not JSON at all", 1, {("QTABLE", "", 0): "米 x1"}),
    _tamper("heatmap: x outside 0..1", 1,
            {("QHEAT", "", 0): envelope([{"x": "2.0000", "y": "0.5000"}])}),
    _tamper("heatmap: negative y", 1,
            {("QHEAT", "", 0): envelope([{"x": "0.5000", "y": "-0.0100"}])}),
    _tamper("heatmap: one point over maxPoints", 1,
            {("QHEAT", "", 0): envelope(HEAT_ROWS + [{"x": "0.1", "y": "0.1"}, {"x": "0.2", "y": "0.2"}])}),
    _tamper("heatmap: a coordinate that is not a number", 1,
            {("QHEAT", "", 0): envelope([{"x": "left", "y": "0.5000"}])}),
    # 循环评价（R02-11）：对象列是枚举＋唯一，行数被钉死成对象个数。
    _tamper("loop rating: a score outside the declared scale", 1,
            {("QLOOP", "", 0): envelope([dict(LOOP_ROWS[0], price="9"), LOOP_ROWS[1]])}),
    _tamper("loop rating: an object nobody declared", 1,
            {("QLOOP", "", 0): envelope([dict(LOOP_ROWS[0], target="B9"), LOOP_ROWS[1]])}),
    _tamper("loop rating: the same object rated twice", 1,
            {("QLOOP", "", 0): envelope([LOOP_ROWS[0], dict(LOOP_ROWS[1], target="B1")])}),
    _tamper("loop rating: one object dropped", 1,
            {("QLOOP", "", 0): envelope([LOOP_ROWS[0]])}),
    _tamper("loop rating: a dimension left empty", 1,
            {("QLOOP", "", 0): envelope([dict(LOOP_ROWS[0], service=""), LOOP_ROWS[1]])}),
    # 影子字段：浏览器端算出的相关性全写 1，服务端照样重算。
    _tamper("repeating table: bad payload with every relevance* shadow forced to 1", 1,
            {("QTABLE", "", 0): envelope([{"item": "米", "qty": "0"}])}, shadow="all-relevant"),
)

#: 说明文字题（X）的那一列恒为空，但表单里确实有一个同名的 hidden input。
#: 这一条不断言「被拒绝」，而是把引擎的实际行为记下来（映射表第四节）。
BOILERPLATE_TAMPER = _tamper("boilerplate column: a value posted into the X hidden field", 0,
                             {("QSEC", "", 0): "注入的说明文字"})
