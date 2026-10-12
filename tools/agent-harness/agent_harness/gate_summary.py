"""Deterministic, bounded Gate summaries backed by local raw evidence."""

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import tempfile
from types import MappingProxyType
from typing import Mapping, Optional

from .config import GateDefinition
from .diagnostics import bounded_diagnostic
from .state import GateEvidence, GateStatus, validate_document


_SCHEMA = json.loads((Path(__file__).resolve().parents[3]
                      / "docs/agent/GATE_SUMMARY_SCHEMA.json").read_text(encoding="utf-8"))
_ZERO_COUNTS = {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "errors": 0}
_READ_LIMIT = 65536


@dataclass(frozen=True)
class GateSummary:
    schema_version: int
    gate_id: str
    status: str
    exit_code: Optional[int]
    duration_ms: int
    counts: Mapping[str, int]
    evidence_key: Optional[str]
    head_commit: str
    diagnostic: Optional[str]
    source: str = "local"
    reused: bool = False

    def __post_init__(self):
        object.__setattr__(self, "counts", MappingProxyType(dict(self.counts)))
        validate_document(_summary_document(self), _SCHEMA)


def _summary_document(summary: GateSummary) -> dict:
    return {
        "schemaVersion": summary.schema_version,
        "gateId": summary.gate_id,
        "status": summary.status,
        "exitCode": summary.exit_code,
        "durationMs": summary.duration_ms,
        "counts": dict(summary.counts),
        "evidenceKey": summary.evidence_key,
        "headCommit": summary.head_commit,
        "source": summary.source,
        "reused": summary.reused,
        "diagnostic": summary.diagnostic,
    }


def _read_log(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            return stream.read(_READ_LIMIT).decode("utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def _count(total: int, failed: int, errors: int, skipped: int) -> dict[str, int]:
    if min(total, failed, errors, skipped) < 0 or failed + errors + skipped > total:
        return dict(_ZERO_COUNTS)
    return {
        "total": total, "passed": total - failed - errors - skipped,
        "failed": failed, "skipped": skipped, "errors": errors,
    }


def _unittest_counts(text: str) -> dict[str, int]:
    total_match = re.search(r"(?m)^Ran (\d+) tests?\b", text)
    if total_match is None:
        return dict(_ZERO_COUNTS)
    total = int(total_match.group(1))
    details = {name: int(value) for name, value in re.findall(
        r"\b(failures|errors|skipped)=(\d+)\b", text
    )}
    return _count(total, details.get("failures", 0), details.get("errors", 0),
                  details.get("skipped", 0))


def _surefire_counts(text: str) -> dict[str, int]:
    matches = list(re.finditer(
        r"Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+),\s*Skipped:\s*(\d+)",
        text,
    ))
    if not matches:
        return dict(_ZERO_COUNTS)
    total = failed = errors = skipped = 0
    for match in matches:
        values = tuple(map(int, match.groups()))
        total += values[0]
        failed += values[1]
        errors += values[2]
        skipped += values[3]
    return _count(total, failed, errors, skipped)


def _vitest_counts(text: str) -> dict[str, int]:
    line = next((item for item in text.splitlines() if re.search(r"\bTests\b", item)), "")
    if not line:
        return dict(_ZERO_COUNTS)
    values = {name: int(value) for value, name in re.findall(
        r"(\d+)\s+(failed|passed|skipped)\b", line
    )}
    total_match = re.search(r"\((\d+)\)\s*$", line.strip())
    if total_match is None:
        return dict(_ZERO_COUNTS)
    total = int(total_match.group(1))
    counts = _count(total, values.get("failed", 0), 0, values.get("skipped", 0))
    if counts["passed"] != values.get("passed", 0):
        return dict(_ZERO_COUNTS)
    return counts


def _playwright_counts(text: str) -> dict[str, int]:
    values = {}
    for value, name in re.findall(r"(?m)^\s*(\d+)\s+(failed|passed|skipped)\b", text):
        if name in values:
            return dict(_ZERO_COUNTS)
        values[name] = int(value)
    if not values:
        return dict(_ZERO_COUNTS)
    return _count(sum(values.values()), values.get("failed", 0), 0,
                  values.get("skipped", 0))


def _counts(summary_format: str, text: str) -> dict[str, int]:
    parser = {
        "generic": lambda _: dict(_ZERO_COUNTS),
        "unittest": _unittest_counts,
        "surefire": _surefire_counts,
        "vitest": _vitest_counts,
        "playwright": _playwright_counts,
    }.get(summary_format)
    if parser is None:
        return dict(_ZERO_COUNTS)
    return parser(text)


def _duration_ms(started_at: Optional[datetime], ended_at: Optional[datetime]) -> int:
    if started_at is None or ended_at is None or ended_at < started_at:
        return 0
    return max(0, round((ended_at - started_at).total_seconds() * 1000))


def build_gate_summary(repo: Path, gate: GateDefinition,
                       evidence: GateEvidence, *,
                       max_diagnostic_bytes: int = 4096) -> GateSummary:
    root = Path(repo).resolve(strict=True)
    if evidence.evidence_path is None:
        raise ValueError("Gate evidence path is required")
    metadata = (root / evidence.evidence_path).resolve(strict=True)
    if not metadata.is_relative_to(root) or metadata.name != "metadata.json":
        raise ValueError("Gate evidence path is outside the repository")
    stdout = _read_log(metadata.parent / "stdout.log")
    stderr = _read_log(metadata.parent / "stderr.log")
    diagnostic = None
    if evidence.status != GateStatus.PASSED:
        diagnostic = bounded_diagnostic(stdout, stderr,
                                        max_bytes=max_diagnostic_bytes)
        if not diagnostic:
            diagnostic = "Gate failed without a textual diagnostic"
    return GateSummary(
        1, gate.id, evidence.status.value, evidence.exit_code,
        _duration_ms(evidence.started_at, evidence.ended_at),
        _counts(gate.summary_format, "\n".join((stdout, stderr))),
        evidence.evidence_key, evidence.head_commit, diagnostic,
        evidence.source, evidence.reused,
    )


def write_gate_summary_atomic(path: Path, summary: GateSummary) -> None:
    path = Path(path)
    document = _summary_document(summary)
    validate_document(document, _SCHEMA)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="." + path.name + ".", suffix=".tmp",
                                         delete=False) as stream:
            temporary_path = Path(stream.name)
            json.dump(document, stream, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
