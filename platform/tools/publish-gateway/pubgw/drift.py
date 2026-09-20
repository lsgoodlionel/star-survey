"""漂移检查：发布之后引擎那边还是不是同一份结构。

要检出的核心情形是**激活后改题目代码**。引擎接受它
（``set_question_properties`` 没有 ``isActive`` 守卫，
remotecontrol_handle.php:2034-2039 摘掉的字段里没有 ``title``），而且
答卷表的列名由 qid 派生，改代码**不会改动任何一列**——库层面看不出异常，
只有平台的代码映射悄悄失配。

两条互补的线索：

- **指纹**便宜，适合周期性扫；只回答"变没变"。
- **字段名 → 代码**精确，适合报警；在一次激活期内字段名稳定，
  所以能指名道姓说出是哪道题被改成了什么。
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

from .binding import BindingRecord
from .fieldmap import FieldmapRow, fingerprint, signature_lines


@dataclass(frozen=True)
class DriftIssue:
    code: str
    detail: str


@dataclass(frozen=True)
class DriftReport:
    drifted: bool
    recorded_fingerprint: str
    current_fingerprint: str
    renamed: Tuple[Tuple[str, str, str], ...]
    issues: Tuple[DriftIssue, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "drifted": self.drifted,
            "recordedFingerprint": self.recorded_fingerprint,
            "currentFingerprint": self.current_fingerprint,
            "renamed": [
                {"uuid": uuid, "from": old, "to": new} for uuid, old, new in self.renamed
            ],
            "issues": [{"code": issue.code, "detail": issue.detail} for issue in self.issues],
        }


def check_drift(record: BindingRecord, rows: Sequence[FieldmapRow]) -> DriftReport:
    current = fingerprint(signature_lines(rows))
    issues: List[DriftIssue] = []

    if not rows:
        issues.append(
            DriftIssue(
                "E_SURVEY_EMPTY",
                "sid {} 的 get_fieldmap 里没有任何题目行：问卷可能被停用或删除".format(
                    record.survey_id
                ),
            )
        )

    if record.fingerprint_version != _version_of(current):
        issues.append(
            DriftIssue(
                "E_FINGERPRINT_VERSION",
                "绑定记录用的是 {} 算法，当前是 {}，无法直接比对".format(
                    record.fingerprint_version, _version_of(current)
                ),
            )
        )

    renamed = _renamed_questions(record, rows)
    for uuid, old, new in renamed:
        issues.append(
            DriftIssue(
                "E_CODE_DRIFT",
                "题目 {}（uuid {}）的代码在发布后被改成了 {}；"
                "答卷表的列没有变化，只有平台的映射失配了".format(old, uuid, new),
            )
        )

    issues.extend(_disappeared_fields(record, rows))
    issues.extend(_new_fields(record, rows))

    if current != record.fingerprint and not any(
        issue.code in ("E_CODE_DRIFT", "E_FIELD_DISAPPEARED", "E_FIELD_ADDED") for issue in issues
    ):
        issues.append(
            DriftIssue(
                "E_FINGERPRINT_DRIFT",
                "结构指纹从 {} 变成了 {}，但逐字段比对没有定位到原因".format(
                    record.fingerprint, current
                ),
            )
        )

    return DriftReport(
        drifted=bool(issues),
        recorded_fingerprint=record.fingerprint,
        current_fingerprint=current,
        renamed=renamed,
        issues=tuple(issues),
    )


def _renamed_questions(
    record: BindingRecord, rows: Sequence[FieldmapRow]
) -> Tuple[Tuple[str, str, str], ...]:
    owners = record.field_owners()
    seen = {}
    for row in rows:
        owner = owners.get(row.fieldname)
        if owner is None:
            continue
        uuid, recorded_code = owner
        if row.code != recorded_code:
            seen[uuid] = (uuid, recorded_code, row.code)
    return tuple(seen[key] for key in sorted(seen))


def _disappeared_fields(record: BindingRecord, rows: Sequence[FieldmapRow]) -> List[DriftIssue]:
    present = {row.fieldname for row in rows}
    issues = []
    for question in record.questions:
        for item in question.fields:
            if item.fieldname in present:
                continue
            issues.append(
                DriftIssue(
                    "E_FIELD_DISAPPEARED",
                    "题目 {}（uuid {}）的列 {} 不见了".format(
                        question.code, question.uuid, item.fieldname
                    ),
                )
            )
    return issues


def _new_fields(record: BindingRecord, rows: Sequence[FieldmapRow]) -> List[DriftIssue]:
    known = set(record.field_owners())
    return [
        DriftIssue(
            "E_FIELD_ADDED",
            "出现了绑定记录里没有的列 {}（题目代码 {}）".format(row.fieldname, row.code),
        )
        for row in rows
        if row.fieldname not in known
    ]


def _version_of(value: str) -> str:
    return value.split(":", 1)[0]
