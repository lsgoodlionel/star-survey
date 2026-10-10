"""Bounded failure identity and immutable repair-budget decisions."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import tempfile
from typing import Iterable

from .state import AttemptState, GateEvidence, GateStatus, RunState, RunStatus, transition


_INPUT_LIMIT = 65536
_LINE_LIMIT = 512
_DIGEST = re.compile(r"[0-9a-f]{64}")
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))")
_TIMESTAMP = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?|\b\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z)?"
)
_TEMP_ROOT = re.compile(
    r"(?:/private)?/(?:tmp|var/tmp)/[^\s/'\"<>:]+"
    r"|(?:/private)?/var/folders/[^/\s]+/[^/\s]+/T/[^\s/'\"<>:]+"
)
_URL_PORT = re.compile(r"(\b[a-z][a-z0-9+.-]*://(?:\[[^\]\s]+\]|[^\s/:]+)):\d{1,5}\b", re.I)
_LOCAL_PORT = re.compile(r"(\b(?:localhost|127\.0\.0\.1|0\.0\.0\.0)|\[::1?\]):\d{1,5}\b", re.I)
_NAMED_PORT = re.compile(r"(\bport\s*[=:]?\s*)\d{1,5}\b", re.I)
_ERROR = re.compile(
    r"\b(?:FAIL(?:ED)?|ERROR|FATAL|[A-Za-z]*Exception|[A-Za-z]*Error)\b"
    r"|\berror\s+[A-Z]+\d+|\b(?:ECONNREFUSED|ENOENT|EADDRINUSE)\b", re.I
)
_WRAPPER = re.compile(r"^\s*(?:npm (?:ERR!|error)|error Command failed|FAILURES\b|ERRORS\b)", re.I)
_PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S)
_USERINFO = re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^\s/@]+@", re.I)
_TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{16,})\b")
_JWT = re.compile(r"\b(?:eyJ|ewo)[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")
_QUOTED_VALUE = r'''"(?:\\[\s\S]|[^"\\])*(?:"|\\?\Z)|'(?:\\[\s\S]|[^'\\])*(?:'|\\?\Z)'''
_HEADER = re.compile(
    r'''(?<![\w-])((?:"(?:Authorization|Cookie|Set-Cookie)"|'(?:Authorization|Cookie|Set-Cookie)'|(?:Authorization|Cookie|Set-Cookie))\s*[:=]\s*)'''
    r"(?:" + _QUOTED_VALUE + r"|[^\r\n]*)(?:\r?\n[ \t]+[^\r\n]*)*", re.I | re.M
)
_ASSIGNMENT_PREFIX = r'''(?<![\w.-])((?:["']?(?:{names})["']?)\s*[:=]\s*)'''
_UNQUOTED_VALUE = (r"[^\s,;&}\"']+"
                   r"(?:[ \t]+(?![A-Za-z_][A-Za-z0-9_.-]*\s*[:=])[^\s,;&}\"']+)*")
_ASSIGNMENT_VALUE = r"(?:" + _QUOTED_VALUE + "|" + _UNQUOTED_VALUE + ")"
_ASSIGNMENT = re.compile(_ASSIGNMENT_PREFIX.format(
    names=r"(?:[A-Za-z_][A-Za-z0-9_]*_)?(?:PASSWORD|TOKEN)"
) + _ASSIGNMENT_VALUE, re.I | re.M)


def _bounded_text(text: str) -> str:
    if len(text) <= _INPUT_LIMIT:
        return text
    # A cut line might begin in a secret value. Omit it before any pattern runs.
    prefix = text[:_INPUT_LIMIT]
    end = prefix.rfind("\n")
    return (prefix[:end + 1] if end >= 0 else "") + "[TRUNCATED]"


def _secret_names(secret_names: Iterable[str]) -> tuple:
    names = []
    for name in secret_names:
        if (len(names) >= 64 or not isinstance(name, str) or not name
                or len(name) > 128 or any(ord(char) < 32 for char in name)):
            raise ValueError("Secret names must be bounded non-empty labels")
        names.append(name)
    return tuple(names)


def redact_text(text: str, secret_names: Iterable[str] = ()) -> str:
    """Redact named/recognizable credentials without reading the environment."""
    names = _secret_names(secret_names)
    text = _ANSI.sub("", _bounded_text(text))
    text = _PEM.sub("[REDACTED]", text)
    text = _USERINFO.sub(r"\1[REDACTED]@", text)
    text = _HEADER.sub(r"\1[REDACTED]", text)
    text = _TOKEN.sub("[REDACTED]", text)
    text = _JWT.sub("[REDACTED]", text)
    text = _ASSIGNMENT.sub(r"\1[REDACTED]", text)
    if names:
        assignment = re.compile(_ASSIGNMENT_PREFIX.format(names="|".join(map(re.escape, names)))
                                + _ASSIGNMENT_VALUE, re.I | re.M)
        text = assignment.sub(r"\1[REDACTED]", text)
    return text[:_INPUT_LIMIT]


def normalize_failure(text: str) -> str:
    """Remove volatile execution details without erasing source locations."""
    text = redact_text(text)
    text = _TIMESTAMP.sub("<TIME>", text)
    text = _TEMP_ROOT.sub("<TMP>", text)
    text = _URL_PORT.sub(r"\1:<PORT>", text)
    text = _LOCAL_PORT.sub(r"\1:<PORT>", text)
    text = _NAMED_PORT.sub(r"\1<PORT>", text)
    normalized = "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())
    return normalized[:_INPUT_LIMIT]


def _first_error(text: str) -> str:
    lines = normalize_failure(text).splitlines()
    for index, line in enumerate(lines):
        if _ERROR.search(line) and not _WRAPPER.search(line):
            context = [line[:_LINE_LIMIT]]
            traceback = (index + 1 < len(lines)
                         and lines[index + 1].startswith("Traceback"))
            location_seen = False
            # Scan a bounded traceback for its stable location and actual error.
            for following in lines[index + 1:index + 33]:
                if re.search(r"^(?:FAIL(?:ED)?|ERROR|FATAL)[:\s]", following, re.I):
                    break
                if traceback and following.startswith('File "') and not location_seen:
                    context.append(following[:_LINE_LIMIT])
                    location_seen = True
                elif _ERROR.search(following) or following.startswith(("E ", "assert ", "at ")):
                    context.append(following[:_LINE_LIMIT])
                elif not traceback or following.startswith(("---", "Ran ")):
                    break
                if len(context) >= 4:
                    break
            return "\n".join(context)
    return ""


def failure_fingerprint(exit_code: int, stdout: str, stderr: str) -> str:
    """Hash the first actionable error and exit code, not build chatter."""
    if type(exit_code) is not int:
        raise ValueError("Exit code must be an integer")
    context = _first_error(stderr) or _first_error(stdout)
    if not context:
        context = normalize_failure(stderr or stdout).split("\n", 1)[0][:_LINE_LIMIT]
    return hashlib.sha256(json.dumps([exit_code, context], ensure_ascii=True).encode()).hexdigest()


@dataclass(frozen=True)
class RetryDecision:
    state: RunState
    code: str

    @property
    def should_pause(self) -> bool:
        return self.code != "repair_allowed"


def record_failure(state: RunState, fingerprint: str, diff_digest: str) -> RetryDecision:
    """Persist progress identity in the existing decision-summary wire contract."""
    if state.status not in (RunStatus.VERIFYING, RunStatus.REPAIRING):
        raise ValueError("Failures can only be recorded during verification or repair")
    for value in (fingerprint, diff_digest):
        if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
            raise ValueError("Failure and diff identities must be SHA-256 digests")
    outcomes = sorted((key, gate.gate_id, gate.status.value, gate.exit_code)
                      for key, gate in state.gates.items())
    gate_digest = hashlib.sha256(json.dumps(outcomes).encode()).hexdigest()
    cycle = {"fingerprint": fingerprint, "diffDigest": diff_digest, "gateDigest": gate_digest}
    previous = next((item for item in reversed(state.decisions)
                     if item["type"] == "failure_cycle"), None)
    same_cycle = previous is not None and previous["summary"] == json.dumps(cycle, sort_keys=True)
    counts = dict(state.attempts.by_fingerprint)
    counts[fingerprint] = counts.get(fingerprint, 0) + 1
    total = state.attempts.total + 1
    code = ("same_failure_limit" if counts[fingerprint] >= 3 else
            "total_attempt_limit" if total >= 5 else
            "no_progress" if same_cycle else "repair_allowed")
    now = datetime.now(timezone.utc)
    decision = {"type": "failure_cycle", "summary": json.dumps(cycle, sort_keys=True),
                "createdAt": now.isoformat().replace("+00:00", "Z")}
    updated = replace(state, attempts=AttemptState(total, counts),
                      last_failure_fingerprint=fingerprint, updated_at=now,
                      decisions=state.decisions + (decision,))
    if code != "repair_allowed":
        updated = transition(updated, RunStatus.PAUSED, code)
    elif updated.status == RunStatus.VERIFYING:
        updated = transition(updated, RunStatus.REPAIRING, code)
    return RetryDecision(updated, code)


def _read_log(root: Path, relative: Path) -> str:
    """Open regular raw evidence using directory descriptors; reject symlinks."""
    if (relative.is_absolute() or ".." in relative.parts or "\\" in relative.as_posix()
            or not relative.is_relative_to(Path("var/agent-harness/runs"))):
        return ""
    descriptor = None
    try:
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in relative.parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        leaf = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                       dir_fd=descriptor)
        with os.fdopen(leaf, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return ""
            content = stream.read(_INPUT_LIMIT + 1)
        if len(content) > _INPUT_LIMIT:
            prefix = content[:_INPUT_LIMIT]
            content = prefix.rsplit(b"\n", 1)[0] if b"\n" in prefix else b""
            content += b"\n[TRUNCATED]"
        return content.decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return ""
    finally:
        if descriptor is not None:
            os.close(descriptor)


def render_diagnostics(state: RunState, evidence: Iterable[GateEvidence], output: Path,
                       *, secret_names: Iterable[str] = ()) -> None:
    """Write a minimal redacted report; optional names apply to every surface."""
    names = _secret_names(secret_names)
    root = state.worktree_path.resolve(strict=True)
    output = Path(output)
    if not output.is_absolute():
        output = root / output
    elif output.is_relative_to(state.worktree_path):
        output = root / output.relative_to(state.worktree_path)
    if output.is_symlink() or output.parent.resolve() != output.parent:
        raise ValueError("Diagnostic output must not follow symlinks")
    if output.name in ("state.json", "events.jsonl", "metadata.json", "stdout.log", "stderr.log"):
        raise ValueError("Diagnostic output must not overwrite state or raw evidence")
    stop = next((item["summary"] for item in reversed(state.decisions)
                 if item["type"] == "transition"), "unspecified")
    lines = [
        "# Diagnostics", f"Run: {state.run_id}", f"Status: {state.status.value}",
        f"Plan: {state.plan_path.as_posix()}", f"Plan SHA-256: {state.plan_sha256}",
        f"Milestone: {state.milestone_id} {state.milestone_title}",
        f"Worktree: {state.worktree_path}", f"Branch: {state.branch}",
        f"HEAD: {state.head_commit}", f"Fingerprint: {state.last_failure_fingerprint or 'unavailable'}",
        f"Attempts: {state.attempts.total}/5 total; "
        f"{state.attempts.by_fingerprint.get(state.last_failure_fingerprint, 0)}/3 same failure",
        f"Stop reason: {stop}", "", "Changed paths:",
    ]
    lines.extend(f"- {path.as_posix()}" for path in state.changed_paths[:100])
    lines.extend(("", "Failed gates:"))
    for index, gate in enumerate(evidence):
        if index >= 32:
            lines.append("Additional gate evidence omitted")
            break
        if gate.status not in (GateStatus.FAILED, GateStatus.TIMED_OUT):
            continue
        lines.append(f"- {gate.gate_id}: {gate.status.value}; exit={gate.exit_code}; "
                     f"HEAD={gate.head_commit}; evidence={gate.evidence_path or 'unavailable'}")
        context = ""
        if gate.evidence_path is not None:
            path = gate.evidence_path
            paths = ((path.parent / "stderr.log", path.parent / "stdout.log")
                     if path.name == "metadata.json" else (path,))
            for log in paths:
                context = _first_error(redact_text(_read_log(root, log), names))
                if context:
                    break
        lines.extend("    " + line for line in (context or "Minimal log unavailable").splitlines())
    lines.extend(("", "Recent decisions:"))
    for item in state.decisions[-10:]:
        # Cycle records contain only digests; human repair summaries stay useful.
        lines.append(f"- {item['createdAt']} {item['type']}: "
                     + redact_text(item["summary"], names)[:2048])
    resume = "cd " + shlex.quote(str(state.worktree_path)) + " && scripts/agent-harness resume"
    lines.extend(("", "Next: review the stop reason and authorize further repair.",
                  "Resume:", "```sh", resume, "```", ""))
    report = "\n".join(redact_text(line, names) for line in lines)
    # Redact the complete document again to catch multiline credentials in fields.
    report = redact_text(report, names)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=".diagnostics.", suffix=".tmp",
                                         delete=False) as stream:
            temporary_path = Path(stream.name)
            stream.write(report)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, output)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
