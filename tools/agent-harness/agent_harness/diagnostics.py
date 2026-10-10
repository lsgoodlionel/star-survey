"""Bounded failure identity and immutable repair-budget decisions."""

from dataclasses import dataclass, replace
import base64
import binascii
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
from typing import Iterable
from uuid import uuid4

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
_PYTEST_TEMP = re.compile(
    r"(?:/private)?/(?:tmp|var/tmp|var/folders/[^/\s]+/[^/\s]+/T)"
    r"/pytest-of-[^/\s]+/pytest-(?:\d+|current)"
    r"(?:/[^/\s'\"<>:]+(?:\d+|current)(?=/))?"
)
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*")
_AUTHORITY_PORT = re.compile(r"(\[[^\]]+\]|[^:]+):\d{1,5}\Z")
_LOCAL_PORT = re.compile(r"(\b(?:localhost|127\.0\.0\.1|0\.0\.0\.0)|\[::1?\]):\d{1,5}\b", re.I)
_NAMED_PORT = re.compile(r"(\bport\s*[=:]?\s*)\d{1,5}\b", re.I)
_ERROR = re.compile(
    r"\b(?:FAIL(?:ED)?|ERROR|FATAL|[A-Za-z]*Exception|[A-Za-z]*Error)\b"
    r"|\berror\s+[A-Z]+\d+|\b(?:ECONNREFUSED|ENOENT|EADDRINUSE)\b", re.I
)
_WRAPPER = re.compile(r"^\s*(?:npm (?:ERR!|error)|error Command failed|FAILURES\b|ERRORS\b)", re.I)
_EXCEPTION_LINE = re.compile(r"^(?:[A-Za-z_][\w.]*)?(?:Error|Exception|ExceptionGroup)\s*:")
_PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S)
_TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{16,})\b")
_JWT = re.compile(r"(?<![A-Za-z0-9_.-])([A-Za-z0-9_-]+)\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")
_QUOTED_VALUE = r'''"(?:\\[\s\S]|[^"\\])*(?:"|\\?\Z)|'(?:\\[\s\S]|[^'\\])*(?:'|\\?\Z)'''
_HEADER = re.compile(
    r'''(?<![\w-])((?:"(?:Authorization|Cookie|Set-Cookie)"|'(?:Authorization|Cookie|Set-Cookie)'|(?:Authorization|Cookie|Set-Cookie))\s*[:=]\s*)'''
    r"(?:" + _QUOTED_VALUE + r"|[^\r\n]*)(?:\r?\n[ \t]+[^\r\n]*)*", re.I | re.M
)
_ASSIGNMENT_PREFIX = r'''(?<![\w.-])((?:["']?(?:{names})["']?)\s*[:=]\s*)'''
_VALUE_WORD = (r'''(?:,(?![ \t]*["']?[A-Za-z_][A-Za-z0-9_.-]{0,127}["']?[ \t]*[:=])'''
               r'''|[^\s,;&}"'])+''')
_UNQUOTED_VALUE = (_VALUE_WORD
                   + r"(?:[ \t]+(?![A-Za-z_][A-Za-z0-9_.-]*\s*[:=])" + _VALUE_WORD + ")*")
_ASSIGNMENT_VALUE = r"(?:" + _QUOTED_VALUE + "|" + _UNQUOTED_VALUE + ")"
_ASSIGNMENT = re.compile(_ASSIGNMENT_PREFIX.format(
    names=r"(?:[A-Za-z_][A-Za-z0-9_]*_)?(?:PASSWORD|TOKEN)"
) + _ASSIGNMENT_VALUE, re.I | re.M)


def _bounded_text(text: str) -> str:
    if len(text) <= _INPUT_LIMIT:
        return text
    return _truncate_prefix(text[:_INPUT_LIMIT])


def _truncate_prefix(prefix: str) -> str:
    marker = "\n[TRUNCATED]"
    prefix = prefix[:_INPUT_LIMIT - len(marker)]
    # Drop only the incomplete lexical fragment, not the entire first error.
    # In particular an unfinished URI/JWT cannot evade its full-shape redactor.
    end = len(prefix)
    while end and prefix[end - 1] not in " \t\r\n\"'<>":
        end -= 1
    return prefix[:end] + marker


def _secret_names(secret_names: Iterable[str]) -> tuple:
    names = []
    for name in secret_names:
        if (len(names) >= 64 or not isinstance(name, str) or not name
                or len(name) > 128 or any(ord(char) < 32 for char in name)):
            raise ValueError("Secret names must be bounded non-empty labels")
        names.append(name)
    return tuple(names)


def _redact_jwt(match: re.Match) -> str:
    header = match.group(1)
    # Never decode or parse an unbounded header. Oversize/too-deep candidates
    # are conservatively hidden rather than returning a possible credential.
    if len(header) > 8192:
        return "[REDACTED]"
    try:
        decoded = base64.b64decode(header + "=" * (-len(header) % 4),
                                   altchars=b"-_", validate=True)
        header_json = decoded.decode("utf-8")
    except (binascii.Error, UnicodeError):
        return match.group(0)
    try:
        document = json.loads(header_json)
    except json.JSONDecodeError:
        return match.group(0)
    except (RecursionError, ValueError):
        return "[REDACTED]"
    return "[REDACTED]" if isinstance(document, dict) else match.group(0)


def _uri_authorities(text: str):
    """Locate real delimiters first; scheme/authority spans never overlap."""
    cursor = 0
    scheme_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+.-"
    while True:
        delimiter = text.find("://", cursor)
        if delimiter < 0:
            return
        start = delimiter
        while start and text[start - 1] in scheme_chars:
            start -= 1
        authority = delimiter + 3
        end = authority
        while end < len(text) and not text[end].isspace() and text[end] not in "/?#\"'<>\\":
            end += 1
        cursor = max(authority, end)
        # An embedded scheme ends this authority at the slash, but its colon
        # still needs to be visited on the next delimiter scan.
        if end > authority and text[end - 1:end + 2] == "://":
            cursor = end - 1
        if (_SCHEME.fullmatch(text[start:delimiter]) is not None
                and (start == 0 or not (text[start - 1].isalnum() or text[start - 1] == "_"))):
            yield authority, end


def _transform_uris(text: str, *, redact: bool) -> str:
    pieces = []
    cursor = 0
    for start, end in _uri_authorities(text):
        authority = text[start:end]
        if redact:
            at = authority.rfind("@")
            replacement = "[REDACTED]@" + authority[at + 1:] if at >= 0 else authority
        else:
            replacement = _AUTHORITY_PORT.sub(r"\1:<PORT>", authority)
        pieces.extend((text[cursor:start], replacement))
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def redact_text(text: str, secret_names: Iterable[str] = ()) -> str:
    """Redact named/recognizable credentials without reading the environment."""
    names = _secret_names(secret_names)
    text = _ANSI.sub("", _bounded_text(text))
    text = _PEM.sub("[REDACTED]", text)
    text = _transform_uris(text, redact=True)
    text = _HEADER.sub(r"\1[REDACTED]", text)
    text = _TOKEN.sub("[REDACTED]", text)
    text = _JWT.sub(_redact_jwt, text)
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
    text = _PYTEST_TEMP.sub("<TMP>", text)
    text = _TEMP_ROOT.sub("<TMP>", text)
    text = _transform_uris(text, redact=False)
    text = _LOCAL_PORT.sub(r"\1:<PORT>", text)
    text = _NAMED_PORT.sub(r"\1<PORT>", text)
    normalized = "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())
    return normalized[:_INPUT_LIMIT]


def _first_error(text: str) -> str:
    lines = normalize_failure(text).splitlines()
    for index, line in enumerate(lines):
        if line.startswith("Traceback (most recent call last):"):
            block = lines[index + 1:index + 33]
            location = next((item for item in block if item.startswith('File "')), "")
            exception = next((item for item in block if _EXCEPTION_LINE.search(item)), "")
            if exception:
                return "\n".join(item[:_LINE_LIMIT] for item in (location, exception) if item)
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
            return _truncate_prefix(content[:_INPUT_LIMIT].decode("utf-8", errors="replace"))
        return content.decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return ""
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _report_value(value: object, names: tuple, limit: int = 512) -> str:
    text = redact_text(str(value), names)
    marker = " [OMITTED]"
    return text if len(text) <= limit else text[:limit - len(marker)] + marker


def _report_section(title: str, items: Iterable[str], budget: int, count: int) -> list:
    lines = ["", title + ":"]
    remaining = budget
    marker = "[OMITTED: additional entries]"
    for index, item in enumerate(items):
        if index >= count or len(item) + 1 > remaining - len(marker) - 1:
            lines.append(marker)
            break
        lines.append(item)
        remaining -= len(item) + 1
    return lines


def _render_report(state: RunState, evidence: Iterable[GateEvidence], root: Path,
                   names: tuple) -> str:
    stop = next((item["summary"] for item in reversed(state.decisions)
                 if item["type"] == "transition"), "unspecified")
    lines = [
        "# Diagnostics", f"Run: {state.run_id}", f"Status: {state.status.value}",
        f"Plan: {_report_value(state.plan_path.as_posix(), names, 2048)}",
        f"Plan SHA-256: {state.plan_sha256}",
        f"Milestone: {_report_value(state.milestone_id, names)} "
        f"{_report_value(state.milestone_title, names, 1024)}",
        f"Worktree: {_report_value(state.worktree_path, names, 2048)}",
        f"Branch: {_report_value(state.branch, names)}",
        f"HEAD: {state.head_commit}", f"Fingerprint: {state.last_failure_fingerprint or 'unavailable'}",
        f"Attempts: {state.attempts.total}/5 total; "
        f"{state.attempts.by_fingerprint.get(state.last_failure_fingerprint, 0)}/3 same failure",
        f"Stop reason: {_report_value(stop, names, 1024)}",
    ]
    lines.extend(_report_section(
        "Changed paths", ("- " + _report_value(path.as_posix(), names)
                          for path in state.changed_paths), 8192, 100))

    def gate_entries():
        for index, gate in enumerate(evidence):
            if index >= 32:
                yield "[OMITTED: additional gate evidence]"
                break
            if gate.status not in (GateStatus.FAILED, GateStatus.TIMED_OUT):
                continue
            header = (f"- {_report_value(gate.gate_id, names, 128)}: {gate.status.value}; "
                      f"exit={gate.exit_code}; HEAD={gate.head_commit}; "
                      f"evidence={_report_value(gate.evidence_path or 'unavailable', names)}")
            context = ""
            if gate.evidence_path is not None:
                path = gate.evidence_path
                paths = ((path.parent / "stderr.log", path.parent / "stdout.log")
                         if path.name == "metadata.json" else (path,))
                for log in paths:
                    context = _first_error(redact_text(_read_log(root, log), names))
                    if context:
                        break
            yield header + "\n" + "\n".join(
                "    " + line for line in (context or "Minimal log unavailable").splitlines())

    lines.extend(_report_section("Failed gates", gate_entries(), 24576, 32))
    recent = reversed(state.decisions)
    lines.extend(_report_section(
        "Recent decisions", (f"- {item['createdAt']} {_report_value(item['type'], names, 128)}: "
                             + _report_value(item["summary"], names, 1024)
                             for item in recent), 8192, 10))
    resume = "cd " + shlex.quote(str(state.worktree_path)) + " && scripts/agent-harness resume"
    lines.extend(("", "Next: review the stop reason and authorize further repair.",
                  "Resume:", "```sh", redact_text(resume, names), "```", ""))
    # Independent section budgets reserve the mandatory skeleton and command.
    return "\n".join(lines)


def _open_directory(path: Path) -> int:
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def render_diagnostics(state: RunState, evidence: Iterable[GateEvidence], output: Path,
                       *, secret_names: Iterable[str] = ()) -> None:
    """Pin the validated output parent for creation, publication and cleanup."""
    names = _secret_names(secret_names)
    root = state.worktree_path.resolve(strict=True)
    output = Path(output)
    if not output.is_absolute():
        output = root / output
    elif output.is_relative_to(state.worktree_path):
        output = root / output.relative_to(state.worktree_path)
    if ".." in output.parts or output.name in (
            "state.json", "events.jsonl", "metadata.json", "stdout.log", "stderr.log"):
        raise ValueError("Diagnostic output must not overwrite state or raw evidence")
    try:
        parent = _open_directory(output.parent)
    except OSError as error:
        raise ValueError("Diagnostic output parent is unavailable or a symlink") from error
    temporary_name = None
    try:
        try:
            leaf = os.stat(output.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(leaf.st_mode):
                raise ValueError("Diagnostic output must be a regular file")
        report = _render_report(state, evidence, root, names)
        candidate = ".diagnostics." + uuid4().hex + ".tmp"
        descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
        temporary_name = candidate
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(report)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, output.name, src_dir_fd=parent, dst_dir_fd=parent)
    finally:
        try:
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name, dir_fd=parent)
                except FileNotFoundError:
                    pass
        finally:
            os.close(parent)
