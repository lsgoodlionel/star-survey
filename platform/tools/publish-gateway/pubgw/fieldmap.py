"""get_fieldmap 的归一化、结构指纹与绑定映射。

指纹的输入是每条题目行的 ``题型|题目代码|aid|尺度``，按引擎返回的原顺序拼接，
再整体 SHA-256 取前 16 位（ADR 0005 决定 4）。刻意排除的东西：

- ``sid`` / ``gid`` / ``qid`` / ``sqid`` / ``fieldname``：跨导入批次全都会变；
- ``question`` / ``help`` / ``group_name`` / ``mandatory``：纯文案与行为属性，
  改了不影响答卷数据的语义；
- **答卷表固有列**（``id`` / ``submitdate`` / ``token`` / ``startdate`` …）。
  这一条与 P0-00.6 的实测口径不同，是刻意收紧的：``activate_tokens`` 会往
  fieldmap 里加一列 ``token``，若把元数据行也算进去，仅仅是给问卷建了参与者表
  就会让指纹漂移，产生假警报。元数据行没有 ``qid``，据此过滤。
"""

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from .model import SurveyDefinition
from .qtypes import expected_rows

#: 指纹算法版本。换算法时一并换掉它，旧绑定记录才不会被误判成漂移。
FINGERPRINT_VERSION = "fm1"

_FINGERPRINT_LENGTH = 16


@dataclass(frozen=True)
class FieldmapRow:
    """一条归一化后的题目行。"""

    fieldname: str
    qid: int
    type: str
    code: str
    aid: str
    scale: int

    @property
    def shape(self) -> Tuple[str, int]:
        return (self.aid, self.scale)


@dataclass(frozen=True)
class FieldBinding:
    fieldname: str
    aid: str
    scale: int


@dataclass(frozen=True)
class QuestionBinding:
    """平台题目 UUID ↔ 引擎题目代码 ↔ 引擎字段名，三段映射的一行。"""

    uuid: str
    code: str
    type: str
    fields: Tuple[FieldBinding, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "code": self.code,
            "type": self.type,
            "fields": [
                {"fieldname": item.fieldname, "aid": item.aid, "scale": item.scale}
                for item in self.fields
            ],
        }


def parse_fieldmap(raw: Mapping[str, Mapping[str, Any]]) -> Tuple[FieldmapRow, ...]:
    """把 RPC 返回的 fieldmap 变成有序的题目行，丢掉答卷表固有列。"""
    rows: List[FieldmapRow] = []
    for fieldname, entry in raw.items():
        qid = _integer(entry.get("qid"))
        if qid is None or qid <= 0:
            continue
        rows.append(
            FieldmapRow(
                fieldname=str(entry.get("fieldname") or fieldname),
                qid=qid,
                type=str(entry.get("type") or ""),
                code=str(entry.get("title") or ""),
                aid=str(entry.get("aid") or ""),
                scale=_integer(entry.get("scale_id")) or 0,
            )
        )
    return tuple(rows)


def signature_lines(rows: Sequence[FieldmapRow]) -> Tuple[str, ...]:
    return tuple("{}|{}|{}|{}".format(row.type, row.code, row.aid, row.scale) for row in rows)


def definition_signature(definition: SurveyDefinition) -> Tuple[str, ...]:
    """从平台定义算出「引擎本该返回」的签名，顺序与 createFieldMap() 一致。"""
    lines: List[str] = []
    for question in definition.questions():
        for code, aid, scale in expected_rows(question):
            lines.append("{}|{}|{}|{}".format(question.type, code, aid, scale))
    return tuple(lines)


def fingerprint(lines: Iterable[str]) -> str:
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    return "{}:{}".format(FINGERPRINT_VERSION, digest[:_FINGERPRINT_LENGTH])


def binding_map(
    definition: SurveyDefinition, rows: Sequence[FieldmapRow]
) -> Tuple[QuestionBinding, ...]:
    """按题目代码把 fieldmap 的行归到平台题目 UUID 名下。

    找不到对应行时返回空的 fields 而不是抛异常：这正是校验环节要报告的事实，
    绑定映射只负责如实呈现。
    """
    by_code: Dict[str, List[FieldmapRow]] = {}
    for row in rows:
        by_code.setdefault(row.code, []).append(row)

    bindings = []
    for question in definition.questions():
        matched = by_code.get(question.code, [])
        bindings.append(
            QuestionBinding(
                uuid=question.uuid,
                code=question.code,
                type=question.type,
                fields=tuple(
                    FieldBinding(row.fieldname, row.aid, row.scale) for row in matched
                ),
            )
        )
    return tuple(bindings)


def _integer(value: Any) -> Any:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
