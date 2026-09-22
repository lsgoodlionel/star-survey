"""按需漂移检查：给定实例、sid 与期望指纹，从引擎回读并判定（契约 v1.2 ``POST /v1/drift-check``）。

漂移＝有人绕过平台在引擎管理端改了已发布的问卷。本模块只读：``get_fieldmap`` 与
``get_survey_properties``，**绝不改写引擎**——发现漂移只报告，由平台标记、由人决定怎么办。

- 带绑定记录时复用 :func:`pubgw.drift.check_drift`，能指名道姓说出哪道题的代码被改成了什么；
- 不带时只比指纹（便宜，只回答"变没变"）；
- 问卷被删 → ``E_SURVEY_MISSING``；被停用（``active`` 不是 ``Y``）→ ``E_SURVEY_NOT_ACTIVE``。
  **过期不算漂移**：那是平台自己收口旧版本的正常状态（见 close.py）。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .binding import BindingRecord
from .close import is_invalid_survey
from .drift import DriftIssue, check_drift
from .fieldmap import fingerprint, parse_fieldmap, signature_lines
from .rpc import RemoteControlClient, RpcError


@dataclass(frozen=True)
class DriftCheckResult:
    survey_id: int
    expected_fingerprint: str
    current_fingerprint: Optional[str]
    active: Optional[str]
    renamed: Tuple[Tuple[str, str, str], ...] = ()
    issues: Tuple[DriftIssue, ...] = field(default_factory=tuple)

    @property
    def drifted(self) -> bool:
        return bool(self.issues)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "surveyId": self.survey_id,
            "expectedFingerprint": self.expected_fingerprint,
            "currentFingerprint": self.current_fingerprint,
            "drifted": self.drifted,
            "active": self.active,
            "renamed": [{"uuid": uuid, "from": old, "to": new} for uuid, old, new in self.renamed],
            "issues": [{"code": issue.code, "detail": issue.detail} for issue in self.issues],
        }


def check_published_survey(
    client: RemoteControlClient,
    survey_id: int,
    expected_fingerprint: str,
    record: Optional[BindingRecord] = None,
) -> DriftCheckResult:
    """回读并判定。引擎读不出来（权限、传输）时抛 :class:`RpcError`，由调用方归为 502。"""
    try:
        raw = client.get_fieldmap(survey_id)
    except RpcError as error:
        if not is_invalid_survey(error):
            raise
        return DriftCheckResult(
            survey_id=survey_id,
            expected_fingerprint=expected_fingerprint,
            current_fingerprint=None,
            active=None,
            issues=(DriftIssue("E_SURVEY_MISSING", "引擎里已经没有 sid {} 这份问卷".format(survey_id)),),
        )

    rows = parse_fieldmap(raw)
    if record is not None:
        report = check_drift(record, rows)
        current, renamed, issues = report.current_fingerprint, report.renamed, list(report.issues)
    else:
        current, renamed = fingerprint(signature_lines(rows)), ()
        issues = _fingerprint_only_issues(survey_id, expected_fingerprint, current, bool(rows))

    active = _active_flag(client, survey_id)
    if active != "Y":
        issues.append(DriftIssue(
            "E_SURVEY_NOT_ACTIVE", "sid {} 在引擎里被停用了（active={!r}）".format(survey_id, active)))

    return DriftCheckResult(
        survey_id=survey_id,
        expected_fingerprint=expected_fingerprint,
        current_fingerprint=current,
        active=active,
        renamed=renamed,
        issues=tuple(issues),
    )


def _fingerprint_only_issues(survey_id: int, expected: str, current: str, has_rows: bool) -> List[DriftIssue]:
    if not has_rows:
        return [DriftIssue("E_SURVEY_EMPTY", "sid {} 的 get_fieldmap 里没有任何题目行".format(survey_id))]
    if current == expected:
        return []
    if current.split(":", 1)[0] != expected.split(":", 1)[0]:
        return [DriftIssue("E_FINGERPRINT_VERSION", "期望指纹 {} 与当前算法 {} 不同版本".format(expected, current))]
    return [DriftIssue("E_FINGERPRINT_DRIFT", "结构指纹从 {} 变成了 {}".format(expected, current))]


def _active_flag(client: RemoteControlClient, survey_id: int) -> Optional[str]:
    value = client.get_survey_properties(survey_id).get("active")
    return None if value is None else str(value)
