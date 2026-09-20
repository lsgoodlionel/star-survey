"""回读校验：用 get_fieldmap 判定「引擎里的问卷」和「平台提交的定义」是不是同一份。

为什么不能信任 import_survey 的返回值：导入端在代码非法或冲突时会自动改名
（import_helper.php:2646-2668），改名只写进 importwarnings，RemoteControl 的
import_survey **只返回新的 sid**，警告全部丢弃。平台唯一能拿到真相的地方是
get_fieldmap 回读。

任何一条不一致都判定发布失败——包括顺序。顺序变化意味着作答者看到的问卷
不是平台设计的那一份。
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

from .compiler import CompiledSurvey
from .fieldmap import FieldmapRow, QuestionBinding, binding_map, fingerprint, signature_lines
from .model import SurveyDefinition
from .qtypes import expected_rows


@dataclass(frozen=True)
class VerificationIssue:
    code: str
    detail: str


@dataclass(frozen=True)
class VerificationReport:
    ok: bool
    fingerprint: str
    expected_fingerprint: str
    issues: Tuple[VerificationIssue, ...]
    renamed: Tuple[Tuple[str, str], ...]
    bindings: Tuple[QuestionBinding, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "fingerprint": self.fingerprint,
            "expectedFingerprint": self.expected_fingerprint,
            "issues": [{"code": issue.code, "detail": issue.detail} for issue in self.issues],
            "renamed": [{"from": old, "to": new} for old, new in self.renamed],
        }


def verify_publication(
    definition: SurveyDefinition,
    compiled: CompiledSurvey,
    rows: Sequence[FieldmapRow],
) -> VerificationReport:
    actual_fingerprint = fingerprint(signature_lines(rows))
    issues: List[VerificationIssue] = []

    renamed = _detect_renames(definition, rows)
    issues.extend(_code_issues(definition, rows, renamed))
    issues.extend(_field_issues(definition, rows))
    if actual_fingerprint != compiled.fingerprint:
        issues.append(
            VerificationIssue(
                "E_FINGERPRINT_MISMATCH",
                "结构指纹不一致：编译期 {}，引擎回读 {}".format(
                    compiled.fingerprint, actual_fingerprint
                ),
            )
        )

    return VerificationReport(
        ok=not issues,
        fingerprint=actual_fingerprint,
        expected_fingerprint=compiled.fingerprint,
        issues=tuple(issues),
        renamed=renamed,
        bindings=binding_map(definition, rows),
    )


def _detect_renames(
    definition: SurveyDefinition, rows: Sequence[FieldmapRow]
) -> Tuple[Tuple[str, str], ...]:
    """按「列形状相同」把丢失的代码和多出来的代码配对，认定为一次改名。

    形状相同并不能证明就是同一道题，但足以把最常见的情况说清楚：
    引擎把 Q1 改成了 r7q0，两者的子题与尺度完全一样。
    """
    expected_shapes = {
        question.code: tuple((aid, scale) for _, aid, scale in expected_rows(question))
        for question in definition.questions()
        if expected_rows(question)
    }
    actual_shapes: Dict[str, List[Tuple[str, int]]] = {}
    for row in rows:
        actual_shapes.setdefault(row.code, []).append(row.shape)

    missing = [code for code in expected_shapes if code not in actual_shapes]
    extra = [code for code in actual_shapes if code not in expected_shapes]

    pairs = []
    available = list(extra)
    for code in missing:
        for candidate in available:
            if tuple(actual_shapes[candidate]) == expected_shapes[code]:
                pairs.append((code, candidate))
                available.remove(candidate)
                break
    return tuple(pairs)


def _code_issues(
    definition: SurveyDefinition,
    rows: Sequence[FieldmapRow],
    renamed: Sequence[Tuple[str, str]],
) -> List[VerificationIssue]:
    expected = [
        question.code for question in definition.questions() if expected_rows(question)
    ]
    actual = list(dict.fromkeys(row.code for row in rows))
    renamed_from = {old for old, _ in renamed}
    renamed_to = {new for _, new in renamed}

    issues = []
    for old, new in renamed:
        issues.append(
            VerificationIssue(
                "E_CODE_RENAMED",
                "引擎把题目代码 {} 改成了 {}；平台的 UUID ↔ 代码映射已经失配".format(old, new),
            )
        )
    for code in expected:
        if code not in actual and code not in renamed_from:
            issues.append(
                VerificationIssue("E_CODE_MISSING", "引擎里没有题目代码 {}".format(code))
            )
    for code in actual:
        if code not in expected and code not in renamed_to:
            issues.append(
                VerificationIssue(
                    "E_CODE_UNEXPECTED", "引擎里多出了定义中没有的题目代码 {}".format(code)
                )
            )
    return issues


def _field_issues(
    definition: SurveyDefinition, rows: Sequence[FieldmapRow]
) -> List[VerificationIssue]:
    actual = {(row.code, row.aid, row.scale) for row in rows}
    issues = []
    for question in definition.questions():
        for wanted in expected_rows(question):
            if wanted in actual:
                continue
            issues.append(
                VerificationIssue(
                    "E_FIELD_MISSING",
                    "题目 {}（uuid {}）缺少列 aid={!r} scale={}".format(
                        question.code, question.uuid, wanted[1], wanted[2]
                    ),
                )
            )
    return issues
