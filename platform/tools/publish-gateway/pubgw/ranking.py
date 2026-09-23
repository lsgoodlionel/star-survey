"""排序题（``R``）的名次：从答卷表那一列 JSON 解析出来。

7.x 起排序项是子题，``SurveyActivator`` 只给排序题建**一列**，值是按名次排列的子题代码
JSON 数组；``createFieldMap()`` 另外为每个名次生成一条 ``aid`` ＝ 名次的行
（common_helper.php:2014 起），这些行没有物理列，``export_responses`` 读出来恒为 ``null``。

名次因此必须由网关解析。放在读端点（``pubgw/responses.py``）而不是平台侧，是因为
答卷查询接口与导出作业都只经这一个入口拿作答值，两边一次就都对了。
哪些列属于排序题只能问引擎：名次列的列名形如 ``Q<qid>_S<sqid>``，与多选、数组等
题型的子题列**完全同形**（common_helper.php:2107），光看列名分不出来。
"""

import json
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Dict, Hashable, Mapping, Optional, Sequence, Tuple

#: 引擎的排序题题型代码。
RANKING_TYPE = "R"
#: 缓存的问卷数上限与存活秒数：已发布问卷的列结构不会变（改版是新 sid），
#: 缓存只为免去逐页重复的 get_fieldmap；仍然设过期，漂移修复后不必重启网关。
MAX_CACHED_SURVEYS = 256
CACHE_TTL_SECONDS = 300


class UntrustedRanking(ValueError):
    """排序题主列不是「项代码数组」，据此算出来的名次不可信。"""


@dataclass(frozen=True)
class RankingColumns:
    """一道排序题的列：JSON 主列，加上按名次升序排列的虚列。"""

    main: str
    #: 下标 0 ＝ 第 1 名。
    ranks: Tuple[str, ...]


def ranking_columns(raw_fieldmap: Mapping[str, Mapping[str, Any]]) -> Tuple[RankingColumns, ...]:
    """从 ``get_fieldmap`` 的原样应答里挑出排序题，按 qid 归组。

    主列是 ``aid`` 为空的那一行，名次列的 ``aid`` 是名次本身。缺主列的组丢掉：
    没有主列就无从解析，宁可照旧返回 ``null``，也不要猜。
    """
    mains: Dict[int, str] = {}
    ranks: Dict[int, list] = {}
    for fieldname, entry in raw_fieldmap.items():
        if not isinstance(entry, Mapping) or str(entry.get("type") or "") != RANKING_TYPE:
            continue
        qid = _integer(entry.get("qid"))
        if qid is None:
            continue
        name = str(entry.get("fieldname") or fieldname)
        rank = _integer(entry.get("aid"))
        if rank is None:
            mains[qid] = name
        elif rank > 0:
            ranks.setdefault(qid, []).append((rank, name))
    return tuple(
        RankingColumns(main=main, ranks=tuple(name for _, name in sorted(ranks.get(qid, []))))
        for qid, main in sorted(mains.items())
    )


def requested_columns(
    layouts: Sequence[RankingColumns], fields: Sequence[str]
) -> Tuple[RankingColumns, ...]:
    """只保留请求里真的要名次的排序题；只要主列的照原样读，不必解析。"""
    wanted = set(fields)
    return tuple(columns for columns in layouts if any(rank in wanted for rank in columns.ranks))


def missing_main_columns(
    layouts: Sequence[RankingColumns], fields: Sequence[str]
) -> Tuple[str, ...]:
    """名次要从主列算出来：主列没被请求时网关自己加读一列（应答里再去掉）。"""
    wanted = set(fields)
    return tuple(columns.main for columns in layouts if columns.main not in wanted)


def ranked_items(value: Optional[str]) -> Optional[Tuple[str, ...]]:
    """主列 → 按名次排列的项代码；``None`` 表示这道题不适用（未到达或被隐藏）。"""
    if value is None:
        return None
    if value == "":
        return ()
    try:
        parsed = json.loads(value)
    except ValueError:
        raise UntrustedRanking("ranking column is not JSON") from None
    if not isinstance(parsed, list):
        raise UntrustedRanking("ranking column is not a JSON array")
    items = []
    for item in parsed:
        if isinstance(item, str):
            items.append(item)
        elif isinstance(item, int) and not isinstance(item, bool):
            items.append(str(item))
        else:
            raise UntrustedRanking("ranking column holds a value that is not an item code")
    return tuple(items)


def decompose(
    values: Mapping[str, Optional[str]], layouts: Sequence[RankingColumns]
) -> Dict[str, Optional[str]]:
    """摊平一条答卷：每个名次列换成排在该位的项代码，没排到的位置为空串。"""
    expanded = dict(values)
    for columns in layouts:
        items = ranked_items(values.get(columns.main))
        for position, fieldname in enumerate(columns.ranks):
            if fieldname not in expanded:
                continue
            if items is None:
                expanded[fieldname] = None
            else:
                expanded[fieldname] = items[position] if position < len(items) else ""
    return expanded


class RankingLayoutCache:
    """按 ``(实例, sid)`` 记住列形状，省掉逐页重复的 ``get_fieldmap``。

    条目数有上限（按最近使用淘汰），不会被大量 sid 撑爆。读取引擎时不持锁：
    同一问卷并发首次读取最多多问引擎一次，结果相同。
    """

    def __init__(
        self,
        now: Callable[[], float],
        ttl: float = CACHE_TTL_SECONDS,
        capacity: int = MAX_CACHED_SURVEYS,
    ):
        self._now = now
        self._ttl = ttl
        self._capacity = max(1, capacity)
        self._entries: "OrderedDict[Hashable, Tuple[float, Tuple[RankingColumns, ...]]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(
        self, key: Hashable, load: Callable[[], Tuple[RankingColumns, ...]]
    ) -> Tuple[RankingColumns, ...]:
        now = self._now()
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry[0] > now:
                self._entries.move_to_end(key)
                return entry[1]
        layouts = load()
        with self._lock:
            self._entries[key] = (now + self._ttl, layouts)
            self._entries.move_to_end(key)
            while len(self._entries) > self._capacity:
                self._entries.popitem(last=False)
        return layouts


def _integer(value: Any) -> Optional[int]:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
