"""多级下拉（R02-03）：引用平台字典的某一版，一层一列。

与别的副表题型的根本差别只有一条：**取值集合不在题目属性里**。行政区划到区县有三千多个节点，
枚举列的 ``options`` 上限是 200，塞得下也不该塞——每道题重复一份、每次发布重传一份。
所以列的类型是 ``dict``：这一列的合法取值＝这本字典这一版第 N 层的节点，
字典本身作为一份快照随 ``.lss`` 的 ``plugin_settings`` 下发一次，插件据它在服务端判定路径。

版本由平台在发布时固化（ADR 0019 决定 2），作者写不了——作者只写引用哪本字典。
网关这一侧只负责：形状对不对、快照与引用对不对得上、快照自己是不是一棵树。
"""

import hashlib
from typing import Any, Dict, List, Tuple

from ..model import Question
from .theme_kit import (
    DICTIONARY_ATTRIBUTE, DICTIONARY_CODE_PATTERN, DICTIONARY_DIGEST_ATTRIBUTE,
    DICTIONARY_DIGEST_PATTERN, DICTIONARY_LEVELS_ATTRIBUTE, DICTIONARY_NODE_PATTERN,
    DICTIONARY_VERSION_ATTRIBUTE, Issue, Lowering, MAX_DICTIONARY_LABEL, MAX_DICTIONARY_LEVELS,
    MAX_DICTIONARY_NODES, MAX_ROWS_ATTRIBUTE, MIN_ROWS_ATTRIBUTE, OPTION_VALUE, canonical_json,
)

#: 层级列的代码：L1、L2…… 作者改不了，读端据序号就知道是第几级。
LEVEL_CODE = "L{}"


def check_cascading(question: Question, values: Dict[str, Any]) -> List[Issue]:
    """题目这一侧：字典代码、固化下来的版本与摘要、每一级的标题。"""
    issues: List[Issue] = []
    code = values.get("dictionary")
    if not isinstance(code, str) or not DICTIONARY_CODE_PATTERN.match(code):
        issues.append((OPTION_VALUE, "themeOptions.dictionary",
                       "字典代码必须是小写字母开头的 2–64 位字母数字或连字符"))
    digest = values.get("dictionaryDigest")
    if not isinstance(digest, str) or not DICTIONARY_DIGEST_PATTERN.match(digest):
        issues.append((OPTION_VALUE, "themeOptions.dictionaryDigest",
                       "dictionaryDigest 必须形如 dg1: 加 16 位十六进制"))
    issues.extend(_check_levels(values.get("levels")))
    return issues


def _check_levels(raw: Any) -> List[Issue]:
    path = "themeOptions.levels"
    if not isinstance(raw, list) or not raw:
        return [(OPTION_VALUE, path, "levels 必须是非空数组")]
    if len(raw) > MAX_DICTIONARY_LEVELS:
        return [(OPTION_VALUE, path, "最多 {} 级".format(MAX_DICTIONARY_LEVELS))]
    for index, label in enumerate(raw):
        if not isinstance(label, str) or not label.strip():
            return [(OPTION_VALUE, "{}[{}]".format(path, index), "每一级都要有非空标题")]
        if len(label) > MAX_DICTIONARY_LABEL:
            return [(OPTION_VALUE, "{}[{}]".format(path, index),
                     "级标题最长 {} 个字符".format(MAX_DICTIONARY_LABEL))]
    return []


def cascading_columns(values: Dict[str, Any]) -> List[Dict[str, Any]]:
    """一级一列。``type: "dict"`` 的列没有 ``options``，取值由 (字典, 版本, 层) 决定。"""
    return [
        {
            "code": LEVEL_CODE.format(level),
            "label": label,
            "type": "dict",
            "required": True,
            "dictionary": values["dictionary"],
            "dictionaryVersion": values["dictionaryVersion"],
            "level": level,
        }
        for level, label in enumerate(values["levels"], start=1)
    ]


def lower_cascading(question: Question, values: Dict[str, Any]) -> Lowering:
    """整题一行：一个人只在一个地方。行数钉死成 1，作答者改不了。"""
    return Lowering(attributes={
        "mjy_table_columns": canonical_json(cascading_columns(values)),
        DICTIONARY_LEVELS_ATTRIBUTE: canonical_json(list(values["levels"])),
        MIN_ROWS_ATTRIBUTE: "1",
        MAX_ROWS_ATTRIBUTE: "1",
    })


# ------------------------------------------------------------------ 随定义下发的快照


def snapshot_issues(payload: Any, where: str) -> List[Tuple[str, str, str]]:
    """一份字典快照自己站不站得住：形状、体量、以及「它真是一棵树吗」。

    摘要**不在这里重算**：平台的 dg1 覆盖的是平台侧节点的规范形式，网关拿到的是紧凑三元组，
    两边各算一遍等于再造一份规范化规则（ADR 0016 决定 5 的代价已经领教过）。
    网关只核对题目引用的摘要与快照声明的摘要一致；真正的核对在引擎侧——
    插件按 (字典, 版本, 摘要) 判断已装的那份是不是同一份。
    """
    issues: List[Tuple[str, str, str]] = []
    if not isinstance(payload, dict):
        return [("E_DICTIONARY_SHAPE", where, "每一本字典都必须是对象")]
    code = payload.get("code")
    if not isinstance(code, str) or not DICTIONARY_CODE_PATTERN.match(code):
        issues.append(("E_DICTIONARY_SHAPE", where + ".code", "字典代码不合法"))
    if not isinstance(payload.get("version"), str):
        issues.append(("E_DICTIONARY_SHAPE", where + ".version", "version 必须是字符串"))
    digest = payload.get("digest")
    if not isinstance(digest, str) or not DICTIONARY_DIGEST_PATTERN.match(digest):
        issues.append(("E_DICTIONARY_SHAPE", where + ".digest", "digest 必须形如 dg1: 加 16 位十六进制"))
    if issues:
        return issues
    return _node_issues(payload.get("nodes"), where + ".nodes")


def _node_issues(raw: Any, where: str) -> List[Tuple[str, str, str]]:
    if not isinstance(raw, list) or not raw:
        return [("E_DICTIONARY_SHAPE", where, "nodes 必须是非空数组")]
    if len(raw) > MAX_DICTIONARY_NODES:
        return [("E_DICTIONARY_SHAPE", where,
                 "一本字典最多 {} 个节点，收到 {}".format(MAX_DICTIONARY_NODES, len(raw)))]
    parents: Dict[str, str] = {}
    for index, node in enumerate(raw):
        path = "{}[{}]".format(where, index)
        if not isinstance(node, list) or len(node) != 3 or any(not isinstance(item, str) for item in node):
            return [("E_DICTIONARY_SHAPE", path, "每个节点都是 [代码, 父代码, 标签] 三元组")]
        code, parent, label = node
        if not DICTIONARY_NODE_PATTERN.match(code):
            return [("E_DICTIONARY_SHAPE", path, "节点代码不合法：{!r}".format(code))]
        if parent and not DICTIONARY_NODE_PATTERN.match(parent):
            return [("E_DICTIONARY_SHAPE", path, "父代码不合法：{!r}".format(parent))]
        if not label.strip() or len(label) > MAX_DICTIONARY_LABEL:
            return [("E_DICTIONARY_SHAPE", path, "标签必须是 1–{} 个字符".format(MAX_DICTIONARY_LABEL))]
        if code in parents:
            return [("E_DICTIONARY_SHAPE", path, "节点代码重复：{}".format(code))]
        parents[code] = parent
    dangling = sorted({parent for parent in parents.values() if parent and parent not in parents})
    if dangling:
        return [("E_DICTIONARY_SHAPE", where, "父节点不在这一版里：{}".format("、".join(dangling)))]
    return []


def depth_of(nodes: List[List[str]]) -> int:
    """这份快照最深几层。深度由根往下推，推不出来的（孤儿、环）在形状检查里已经挡掉。"""
    parents = {node[0]: node[1] for node in nodes}
    depths: Dict[str, int] = {}
    deepest = 0
    for code in parents:
        deepest = max(deepest, _depth(code, parents, depths))
    return deepest


def _depth(code: str, parents: Dict[str, str], depths: Dict[str, int]) -> int:
    chain: List[str] = []
    cursor = code
    while cursor and cursor not in depths:
        if len(chain) > len(parents):
            return 0  # 成环：形状检查已经拒过，这里只求不要死循环
        chain.append(cursor)
        cursor = parents.get(cursor, "")
    depth = depths.get(cursor, 0) if cursor else 0
    for item in reversed(chain):
        depth += 1
        depths[item] = depth
    return depth


def setting_payload(dictionaries: Tuple[Any, ...]) -> str:
    """写进 ``lime_plugin_settings`` 的那一段。键序固定，插件读的就是这个形状。"""
    return canonical_json({
        "v": 1,
        "dictionaries": [
            {
                "code": one["code"],
                "version": one["version"],
                "digest": one["digest"],
                "nodes": [list(node) for node in one["nodes"]],
            }
            for one in dictionaries
        ],
    })


def snapshot_digest(nodes: List[List[str]]) -> str:
    """网关自己算一遍快照的指纹，只用在日志与诊断里——不参与发布判定（理由见 snapshot_issues）。"""
    canonical = "\n".join("|".join(node) for node in nodes)
    return "sn1:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


#: 题目这一侧引用的三件事，检查快照时要拿它们比对。
def reference_of(values: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        str(values.get("dictionary", "")),
        str(values.get("dictionaryVersion", "")),
        str(values.get("dictionaryDigest", "")),
    )


#: 主题降级时额外写进题目属性的那几项（由 OptionSpec.attribute 自动落的除外）。
MANAGED = (
    DICTIONARY_ATTRIBUTE, DICTIONARY_VERSION_ATTRIBUTE, DICTIONARY_DIGEST_ATTRIBUTE,
    DICTIONARY_LEVELS_ATTRIBUTE,
)
