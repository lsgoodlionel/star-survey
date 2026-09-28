"""需求↔测试登记表的解析。

表在 `platform/docs/traceability/requirement-tests.md`，一行一条证据：

    | 需求ID | 层级 | 证据定位符 | 说明 |

同一条需求可以有多行（单测＋集成＋端到端各一行）。用 Markdown 表格而不是 JSON/YAML：
这份东西是给人评审的，diff 要看得懂；解析它只用正则，不引第三方库，也避开了
3.9 没有 `tomllib` 这类跨版本坑。
"""

import re
from typing import Dict, List, NamedTuple, Tuple

from .index import REQUIREMENT_PATTERN
from .locators import Locator, parse_locator

#: 证据层级。分类只为看清「这条需求有没有真正跑过引擎」，不参与解析逻辑。
LEVELS = ("单测", "集成", "端到端")

_ROW = re.compile(r"^\|\s*(R\d{2}-\d{2})\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*$")


class Entry(NamedTuple):
    """登记表里的一行。``line`` 是行号，报错时好定位。"""

    requirement_id: str
    level: str
    locator: Locator
    note: str
    line: int


def parse_registry(text: str) -> Tuple[List[Entry], List[str]]:
    """解析登记表，返回（条目, 格式问题）。

    格式问题不抛异常而是收集起来：一次把整张表的毛病都报出来，比改一行跑一次快。
    """
    entries: List[Entry] = []
    problems: List[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.lstrip().startswith("|"):
            continue
        match = _ROW.match(line.strip())
        if match is None:
            # 表头、分隔行与说明性表格都走这里；只有「像需求行但列数不对」才值得报。
            if REQUIREMENT_PATTERN.search(line):
                problems.append("第 {} 行像一条登记但列数不对：{}".format(number, line.strip()))
            continue
        requirement_id, level, raw_locator, note = match.groups()
        if level not in LEVELS:
            problems.append(
                "第 {} 行的层级「{}」不在 {} 里".format(number, level, "／".join(LEVELS))
            )
            continue
        try:
            locator = parse_locator(raw_locator.strip("`"))
        except ValueError as error:
            problems.append("第 {} 行：{}".format(number, error))
            continue
        entries.append(Entry(requirement_id, level, locator, note.strip(), number))
    return entries, problems


def duplicate_entries(entries: List[Entry]) -> List[str]:
    """同一条需求登记了同一个定位符两次——不是错误但是噪音，钉住免得越滚越多。"""
    seen: Dict[Tuple[str, str], int] = {}
    problems: List[str] = []
    for entry in entries:
        key = (entry.requirement_id, entry.locator.raw)
        if key in seen:
            problems.append(
                "第 {} 行与第 {} 行重复登记了 {} → {}".format(
                    entry.line, seen[key], entry.requirement_id, entry.locator.raw
                )
            )
            continue
        seen[key] = entry.line
    return problems


def covered_requirements(entries: List[Entry]) -> Dict[str, List[Entry]]:
    grouped: Dict[str, List[Entry]] = {}
    for entry in entries:
        grouped.setdefault(entry.requirement_id, []).append(entry)
    return grouped
