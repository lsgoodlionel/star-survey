"""Aggregate numeric Codex usage metadata without reading message payloads."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Optional

from .state import validate_document


_SCHEMA = json.loads((Path(__file__).resolve().parents[3]
                      / "docs/agent/TOKEN_REPORT_SCHEMA.json").read_text(encoding="utf-8"))
_MAX_FILE_BYTES = 128 * 1024 * 1024
_MAX_LINE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class TokenUsage:
    raw_tokens: int = 0
    cached_input_tokens: int = 0
    active_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    def __add__(self, other):
        if not isinstance(other, TokenUsage):
            return NotImplemented
        return TokenUsage(*(getattr(self, name) + getattr(other, name) for name in (
            "raw_tokens", "cached_input_tokens", "active_tokens",
            "output_tokens", "reasoning_tokens",
        )))

    def subtract(self, baseline):
        values = [max(0, getattr(self, name) - getattr(baseline, name)) for name in (
            "raw_tokens", "cached_input_tokens", "active_tokens",
            "output_tokens", "reasoning_tokens",
        )]
        # Active usage is derived from cumulative primitives, not cumulative active deltas.
        return TokenUsage(*values)

    def to_dict(self):
        return {
            "rawTokens": self.raw_tokens,
            "cachedInputTokens": self.cached_input_tokens,
            "activeTokens": self.active_tokens,
            "outputTokens": self.output_tokens,
            "reasoningTokens": self.reasoning_tokens,
        }


@dataclass(frozen=True)
class TokenReport:
    since: datetime
    until: datetime
    plan_path: str
    main_sessions: int
    subagent_sessions: int
    reviewer_sessions: int
    usage: TokenUsage
    review_usage: TokenUsage

    def to_dict(self):
        document = {
            "schemaVersion": 1,
            "since": _timestamp(self.since), "until": _timestamp(self.until),
            "planPath": self.plan_path,
            "sessions": {"main": self.main_sessions,
                         "subagents": self.subagent_sessions,
                         "reviewers": self.reviewer_sessions},
            "usage": self.usage.to_dict(),
            "reviewUsage": self.review_usage.to_dict(),
            "metrics": {"gateExecutionCount": 0, "gateReuseCount": 0,
                        "duplicateGateAvoidedCount": 0,
                        "failureFingerprintCount": 0,
                        "diagnosticBytesExposed": 0},
        }
        validate_document(document, _SCHEMA)
        return document


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value) -> Optional[datetime]:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result.astimezone(timezone.utc) if result.utcoffset() is not None else None


def _integer(value) -> int:
    return value if type(value) is int and value >= 0 else 0


def _usage(value) -> Optional[TokenUsage]:
    if not isinstance(value, dict):
        return None
    input_tokens = _integer(value.get("input_tokens"))
    cached = _integer(value.get("cached_input_tokens"))
    output = _integer(value.get("output_tokens"))
    reasoning = _integer(value.get("reasoning_output_tokens",
                                   value.get("reasoning_tokens")))
    raw = _integer(value.get("total_tokens"))
    if raw == 0:
        raw = input_tokens + output + reasoning
    active = max(0, input_tokens - cached) + output + reasoning
    return TokenUsage(raw, cached, active, output, reasoning)


def _category(source) -> str:
    if isinstance(source, dict):
        if "subagent" in source:
            nested = source["subagent"]
            labels = []
            pending = [nested]
            while pending:
                value = pending.pop()
                if isinstance(value, str):
                    labels.append(value.lower())
                elif isinstance(value, dict):
                    pending.extend(value.keys())
                    pending.extend(value.values())
                elif isinstance(value, (list, tuple)):
                    pending.extend(value)
            joined = " ".join(labels)
            if "guardian" in joined or "review" in joined:
                return "reviewer"
            return "subagent"
        values = [value for key, value in source.items()
                  if key in {"subagent", "role", "type", "name"}
                  and isinstance(value, str)]
        joined = " ".join(values).lower()
        if any(label in joined for label in ("guardian", "review")):
            return "reviewer"
        if values:
            return "subagent"
    if isinstance(source, str):
        lowered = source.lower()
        if "guardian" in lowered or "review" in lowered:
            return "reviewer"
        if "subagent" in lowered:
            return "subagent"
    return "main"


def _session(path: Path, project_root: Path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_FILE_BYTES:
        return None
    metadata = None
    counts = []
    try:
        with path.open("rb") as stream:
            for raw in stream:
                if len(raw) > _MAX_LINE_BYTES:
                    continue
                try:
                    event = json.loads(raw)
                except (UnicodeError, json.JSONDecodeError):
                    continue
                if not isinstance(event, dict):
                    continue
                timestamp = _parse_timestamp(event.get("timestamp"))
                payload = event.get("payload")
                if timestamp is None or not isinstance(payload, dict):
                    continue
                if event.get("type") == "session_meta" and metadata is None:
                    cwd = payload.get("cwd")
                    if not isinstance(cwd, str) or len(cwd) > 4096:
                        continue
                    candidate = Path(cwd).resolve(strict=False)
                    if candidate != project_root and not candidate.is_relative_to(project_root):
                        return None
                    metadata = (timestamp, _category(payload.get("source")))
                elif (event.get("type") == "event_msg"
                      and payload.get("type") == "token_count"):
                    info = payload.get("info")
                    usage = _usage(info.get("total_token_usage") if isinstance(info, dict) else None)
                    if usage is not None:
                        counts.append((timestamp, usage))
    except OSError:
        return None
    return (metadata, sorted(counts, key=lambda item: item[0])) if metadata else None


def _window_usage(category: str, started: datetime, counts, since, until):
    eligible = [(stamp, usage) for stamp, usage in counts if stamp <= until]
    if not eligible:
        return None
    final = eligible[-1][1]
    if category in {"subagent", "reviewer"} and started >= since:
        return final
    before = [usage for stamp, usage in eligible if stamp < since]
    baseline = before[-1] if before else TokenUsage()
    return final.subtract(baseline)


def _active_plan(project_root: Path) -> str:
    try:
        host = json.loads((project_root / "docs/agent/HARNESS_HOST.json")
                          .read_text(encoding="utf-8"))
        value = host.get("activePlan")
        path = Path(value)
        if (not isinstance(value, str) or not value or path.is_absolute()
                or ".." in path.parts or "\\" in value):
            raise ValueError()
        return value
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return "unknown"


def collect_token_usage(session_roots: tuple[Path, ...], *,
                        since: datetime, until: datetime,
                        project_root: Path) -> TokenReport:
    if (not isinstance(since, datetime) or not isinstance(until, datetime)
            or since.utcoffset() is None or until.utcoffset() is None or since >= until):
        raise ValueError("Token report requires an ordered timezone-aware window")
    root = Path(project_root).resolve(strict=True)
    totals, review = TokenUsage(), TokenUsage()
    sessions = {"main": 0, "subagent": 0, "reviewer": 0}
    seen = set()
    for session_root in session_roots:
        candidate = Path(session_root).resolve(strict=True)
        if candidate in seen:
            continue
        seen.add(candidate)
        for path in sorted(candidate.rglob("*.jsonl")):
            parsed = _session(path, root)
            if parsed is None:
                continue
            (started, category), counts = parsed
            usage = _window_usage(category, started, counts, since, until)
            if usage is None:
                continue
            sessions[category] += 1
            if category == "reviewer":
                review += usage
            else:
                totals += usage
    report = TokenReport(since.astimezone(timezone.utc), until.astimezone(timezone.utc),
                         _active_plan(root), sessions["main"], sessions["subagent"],
                         sessions["reviewer"], totals, review)
    report.to_dict()
    return report
