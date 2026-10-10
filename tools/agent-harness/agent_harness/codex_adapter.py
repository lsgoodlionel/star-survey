"""Fixed least-privilege argv and bounded, untrusted Codex JSONL transport."""

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path, PureWindowsPath
import re
import selectors
import signal
import subprocess
import time
from typing import Sequence
from uuid import UUID

from .diagnostics import redact_text, _open_directory
from .git_guard import assert_worktree_isolated
from .state import _read_json

_SCHEMA = _read_json(Path(__file__).with_name("codex_response.schema.json").read_bytes())
_LINE_LIMIT = 65536
_TOTAL_LIMIT = 4 * 1024 * 1024
_EVENT_LIMIT = 4096
_PROMPT_LIMIT = 16384
_FORBIDDEN = re.compile(r"bypass|danger-full|--add-dir|--config|(?:^|\s)-c(?:\s|=|$)|--enable|--disable|--profile", re.I)


class CodexFailure(str, Enum):
    COMMAND_POLICY = "command_policy"
    STORAGE_POLICY = "storage_policy"
    SPAWN_ERROR = "spawn_error"
    CLEANUP_ERROR = "cleanup_error"
    NONZERO_EXIT = "nonzero_exit"
    TIMEOUT = "timeout"
    OUTPUT_LIMIT = "output_limit"
    MALFORMED_JSONL = "malformed_jsonl"
    MISSING_SESSION = "missing_session"
    MISSING_RESPONSE = "missing_response"
    MISSING_COMPLETION = "missing_completion"
    SCHEMA_VIOLATION = "schema_violation"
    TURN_FAILED = "turn_failed"


@dataclass(frozen=True)
class CodexResult:
    status: str = "failed"
    summary: str = "Codex execution failed"
    changed_paths: tuple[str, ...] = ()
    tests_requested: tuple[str, ...] = ()
    needs_human: bool = True
    session_id: str | None = None
    failure: CodexFailure | None = None
    exit_code: int | None = None


def _session(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("Invalid session identity")
    return value


def _safe(value, limit):
    if (not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > limit
            or "\x00" in value or _FORBIDDEN.search(value) or redact_text(value) != value):
        raise ValueError("Unsafe command input")
    return value


def _path(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Expected canonical absolute path")
    descriptor = _open_directory(path if path.is_dir() else path.parent)
    os.close(descriptor)
    if path.is_symlink() or path.resolve(strict=True) != path:
        raise ValueError("Symlink path rejected")
    _safe(str(path), 4096)
    return path


def build_codex_command(repo: Path, schema: Path, prompt: str,
                        resume_session: str | None = None) -> tuple[str, ...]:
    root, schema = _path(repo), _path(schema)
    assert_worktree_isolated(root)
    if not schema.is_relative_to(root) or schema.stat().st_size > _LINE_LIMIT or _read_json(schema.read_bytes()) != _SCHEMA:
        raise ValueError("Expected the controlled response schema")
    prompt = _safe(prompt, _PROMPT_LIMIT)
    if prompt.lstrip().startswith("-"):
        raise ValueError("Prompt must not be parsed as an option")
    fixed = ("codex", "exec", "--sandbox", "workspace-write", "--approve-for-me",
             "--strict-config", "--json", "--output-schema", str(schema), "--cd", str(root))
    # Parent exec options precede resume: resume does not expose sandbox/cd.
    return fixed + (("resume", _session(resume_session)) if resume_session is not None else ()) + (prompt,)


def _command(command):
    if not isinstance(command, (tuple, list)) or len(command) not in (12, 14):
        raise ValueError("Command rejected")
    command = tuple(command)
    session = command[12] if len(command) == 14 else None
    expected = build_codex_command(Path(command[10]), Path(command[8]), command[-1], session)
    if command != expected:
        raise ValueError("Command rejected")
    return command, Path(command[10])


def _storage(root, event_log):
    path = Path(event_log)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Raw log path rejected")
    relative = path.relative_to(root)
    if len(relative.parts) < 5 or relative.parts[:3] != ("var", "agent-harness", "runs"):
        raise ValueError("Raw log must belong to a run")
    _session(relative.parts[3])
    parent = _open_directory(path.parent)
    try:
        arguments = [Path(*relative.parts[:4]).as_posix() + "/",
                     relative.as_posix(), relative.as_posix() + ".stderr.log"]
        result = subprocess.run(["git", "--no-optional-locks", "check-ignore", "--no-index", "-z", "--stdin"],
                                cwd=root, input="\x00".join(arguments) + "\x00", text=True, capture_output=True, check=False)
        tracked = subprocess.run(["git", "--no-optional-locks", "ls-files", "-z", "--", *arguments],
                                 cwd=root, capture_output=True, check=False)
        if result.returncode or set(result.stdout.rstrip("\x00").split("\x00")) != set(arguments) or tracked.returncode or tracked.stdout:
            raise ValueError("Raw outputs must be ignored and untracked")
        for name in (path.name, path.name + ".stderr.log"):
            if os.path.lexists(path.parent / name):
                raise ValueError("Raw output already exists")
        return path, parent
    except BaseException:
        os.close(parent)
        raise


def _validate_response(value, schema):
    kinds = {"object": type(value) is dict, "array": type(value) is list,
             "string": type(value) is str, "boolean": type(value) is bool}
    if not kinds.get(schema["type"], False):
        raise ValueError("Response type mismatch")
    if isinstance(value, dict):
        if set(value) != set(schema["required"]):
            raise ValueError("Response field mismatch")
        for key, item in value.items():
            _validate_response(item, schema["properties"][key])
    elif isinstance(value, list):
        if len(value) > schema["maxItems"] or any(item in value[:i] for i, item in enumerate(value)):
            raise ValueError("Response array limit")
        for item in value:
            _validate_response(item, schema["items"])
    elif isinstance(value, str):
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", _LINE_LIMIT):
            raise ValueError("Response string limit")
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError("Response enum mismatch")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            raise ValueError("Response pattern mismatch")
        if schema.get("format") == "uuid":
            _session(value)


def _response(document, session):
    _validate_response(document, _SCHEMA)
    for key in ("changedPaths", "testsRequested"):
        if any(redact_text(v) != v for v in document[key]):
            raise ValueError("Unsafe response metadata")
    for value in document["changedPaths"]:
        path = Path(value)
        if (path.is_absolute() or PureWindowsPath(value).drive or ".." in path.parts or "\\" in value
                or "\x00" in value or path.as_posix() != value or value == "."):
            raise ValueError("Invalid changed path")
    if _session(document["sessionId"]) != session:
        raise ValueError("Session mismatch")
    return CodexResult(document["status"], redact_text(document["summary"]),
                       tuple(document["changedPaths"]), tuple(document["testsRequested"]),
                       document["needsHuman"], session, exit_code=0)


def _environment():
    names = ("PATH", "HOME", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "TZ", "SYSTEMROOT", "WINDIR")
    return {name: os.environ[name] for name in names if name in os.environ}


def run_codex(command: Sequence[str], timeout_seconds: int, event_log: Path) -> CodexResult:
    """Never return arbitrary exception/log text; kill and reap owned processes."""
    try:
        command, root = _command(command)
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600:
            raise ValueError()
    except (OSError, ValueError, TypeError, IndexError):
        return CodexResult(failure=CodexFailure.COMMAND_POLICY)
    try:
        path, parent = _storage(root, event_log)
    except (OSError, ValueError, TypeError):
        return CodexResult(failure=CodexFailure.STORAGE_POLICY)
    process = None
    failure, exit_code = None, None
    session, response, completed = None, None, False
    total, count, buffer = 0, 0, b""
    deadline = time.monotonic() + timeout_seconds
    try:
        with os.fdopen(os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                               0o600, dir_fd=parent), "wb") as stdout, \
                os.fdopen(os.open(path.name + ".stderr.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                  0o600, dir_fd=parent), "wb") as stderr, selectors.DefaultSelector() as selector:
            process = subprocess.Popen(command, cwd=root, env=_environment(), shell=False,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       start_new_session=(os.name == "posix"))
            for stream, target in ((process.stdout, stdout), (process.stderr, stderr)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, target)
            while selector.get_map() and failure is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failure = CodexFailure.TIMEOUT
                    break
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fd, 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    allowed = min(len(chunk), _TOTAL_LIMIT - total)
                    key.data.write(chunk[:allowed])
                    total += len(chunk)
                    if total > _TOTAL_LIMIT:
                        failure = CodexFailure.OUTPUT_LIMIT
                        break
                    if key.fileobj is process.stderr:
                        continue
                    buffer += chunk
                    while b"\n" in buffer and failure is None:
                        line, buffer = buffer.split(b"\n", 1)
                        count += 1
                        if len(line) > _LINE_LIMIT or count > _EVENT_LIMIT:
                            failure = CodexFailure.OUTPUT_LIMIT
                            break
                        try:
                            event = _read_json(line)
                            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                                raise ValueError()
                            kind = event["type"]
                            if kind == "thread.started":
                                identity = _session(event.get("thread_id"))
                                if session is not None or completed:
                                    raise ValueError()
                                session = identity
                            elif kind == "item.completed" and event.get("item", {}).get("type") == "agent_message":
                                if completed:
                                    raise ValueError()
                                # Only the last agent message is the final response.
                                response = event["item"]["text"]
                            elif kind == "turn.completed":
                                if completed:
                                    raise ValueError()
                                completed = True
                            elif kind in ("turn.failed", "error"):
                                failure = CodexFailure.TURN_FAILED
                        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
                            failure = CodexFailure.MALFORMED_JSONL
                    if len(buffer) > _LINE_LIMIT:
                        failure = CodexFailure.OUTPUT_LIMIT
            if failure is None:
                try:
                    exit_code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    failure = CodexFailure.TIMEOUT
            if failure is None and exit_code != 0:
                failure = CodexFailure.NONZERO_EXIT
            if failure is None and buffer:
                failure = CodexFailure.MALFORMED_JSONL
    except OSError:
        failure = CodexFailure.SPAWN_ERROR
    finally:
        if process is not None:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                elif process.poll() is None:
                    process.kill()
            except ProcessLookupError:
                pass
            except PermissionError:
                # Some host sandboxes deny group signalling; reap our child.
                failure = failure or CodexFailure.CLEANUP_ERROR
                if process.poll() is None:
                    process.kill()
            process.wait()
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        os.close(parent)
    if failure is None:
        failure = (CodexFailure.MISSING_SESSION if session is None else
                   CodexFailure.MISSING_RESPONSE if response is None else
                   CodexFailure.MISSING_COMPLETION if not completed else None)
    if failure is None:
        try:
            if len(command) == 14 and session != command[12]:
                raise ValueError("Resumed session mismatch")
            return _response(_read_json(response), session)
        except (ValueError, TypeError, KeyError, RecursionError):
            failure = CodexFailure.SCHEMA_VIOLATION
    return CodexResult(failure=failure, exit_code=exit_code)
