"""Plan additive quality gates and persist immutable, HEAD-bound attempts."""

from dataclasses import replace
from datetime import datetime, timezone
from fnmatch import fnmatchcase
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import signal
import subprocess
import tempfile
from typing import Iterable, Tuple

from .config import GateDefinition, GateMatrix
from .state import GateEvidence, GateStatus, RunState, RunStatus


def _relative(value: str) -> str:
    if (not isinstance(value, str) or not value.strip() or "\x00" in value
            or "\\" in value or PurePosixPath(value).is_absolute()
            or PureWindowsPath(value).drive or ".." in PurePosixPath(value).parts):
        raise ValueError("Expected a repository-relative path without traversal")
    return PurePosixPath(value).as_posix()


def resolve_required_gates(
    matrix: GateMatrix, changed_paths: Iterable[str], *,
    additional_gate_ids: Iterable[str] = (),
) -> Tuple[GateDefinition, ...]:
    """Union every matching profile and additions in matrix definition order."""
    required = set()
    for value in changed_paths:
        path = _relative(value)
        profiles = [name for name, patterns in matrix.paths.items()
                    if any(fnmatchcase(path, _relative(pattern)) for pattern in patterns)]
        if path == "." or not profiles:
            raise ValueError("Changed path has no gate profile")
        for name in profiles:
            required.update(matrix.profiles[name])
    required.update(additional_gate_ids)
    if required - {gate.id for gate in matrix.gates}:
        raise ValueError("Unknown gate identifier")
    return tuple(gate for gate in matrix.gates if gate.id in required)


def _directory(root: Path, relative: str) -> Path:
    path = root
    for part in PurePosixPath(_relative(relative)).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("Symlink directory is not permitted")
    if not path.resolve().is_relative_to(root):
        raise ValueError("Directory escapes repository")
    return path


def _environment() -> dict:
    names = ("PATH", "HOME", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "TZ",
             "JAVA_HOME", "SYSTEMROOT", "WINDIR")
    environment = {name: os.environ[name] for name in names if name in os.environ}
    environment.setdefault("PATH", os.defpath)
    return environment


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _validate_head(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise ValueError("HEAD must be a full lowercase commit SHA")


def run_gate(repo: Path, gate: GateDefinition, evidence_dir: Path,
             head_commit: str) -> GateEvidence:
    """Execute a configured argv; metadata is the final record of an attempt."""
    if (not isinstance(gate.command, (tuple, list)) or not gate.command
            or any(not isinstance(arg, str) or not arg.strip() or "\x00" in arg
                   for arg in gate.command)
            or type(gate.timeout_seconds) is not int or gate.timeout_seconds <= 0):
        raise ValueError("Gate requires fixed argv and a positive timeout")
    # Validate the HEAD and evidence contract before any command or disk writes.
    _validate_head(head_commit)
    GateEvidence(gate.id, GateStatus.PENDING, None, None, None, None, head_commit)
    root = Path(repo).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Repository must be a directory")
    cwd = _directory(root, gate.cwd)
    evidence_dir = Path(evidence_dir)
    relative = evidence_dir.relative_to(root) if evidence_dir.is_absolute() else evidence_dir
    relative = Path(_relative(relative.as_posix()))
    if not relative.is_relative_to(Path("var/agent-harness/runs")):
        raise ValueError("Evidence must be stored in the raw run directory")
    directory = _directory(root, relative.as_posix())
    directory.mkdir(parents=True, exist_ok=True)
    attempt = Path(tempfile.mkdtemp(prefix="gate-", dir=directory))
    metadata_path = attempt / "metadata.json"
    started_at = datetime.now(timezone.utc)
    status, exit_code, failure_kind = GateStatus.FAILED, None, None
    with (attempt / "stdout.log").open("x", encoding="utf-8") as stdout, \
            (attempt / "stderr.log").open("x", encoding="utf-8") as stderr:
        if not cwd.is_dir():
            failure_kind = "missing_cwd"
        else:
            process = None
            try:
                process = subprocess.Popen(
                    tuple(gate.command), cwd=cwd, env=_environment(), shell=False,
                    stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, text=True,
                    start_new_session=(os.name == "posix"),
                )
                try:
                    exit_code = process.wait(timeout=gate.timeout_seconds)
                    status = GateStatus.PASSED if exit_code == 0 else GateStatus.FAILED
                    if exit_code != 0:
                        failure_kind = "nonzero_exit"
                except subprocess.TimeoutExpired:
                    status, failure_kind = GateStatus.TIMED_OUT, "timeout"
            except FileNotFoundError:
                failure_kind = "missing_executable"
            except OSError:
                failure_kind = "spawn_error"
            finally:
                if process is not None:
                    # Descendants inherit logs even after the leader exits.
                    # Stop our group and reap the leader before closing logs.
                    try:
                        if os.name == "posix":
                            os.killpg(process.pid, signal.SIGKILL)
                        elif process.poll() is None:
                            process.kill()
                    except ProcessLookupError:
                        pass
                    finally:
                        process.wait()
    ended_at = datetime.now(timezone.utc)
    evidence = GateEvidence(gate.id, status, exit_code, started_at, ended_at,
                            metadata_path.relative_to(root), head_commit)
    metadata = {
        "gateId": gate.id, "status": status.value, "exitCode": exit_code,
        "headCommit": head_commit, "startedAt": _timestamp(started_at),
        "endedAt": _timestamp(ended_at), "command": list(gate.command),
        "cwd": _relative(gate.cwd), "timeoutSeconds": gate.timeout_seconds,
        "stdoutPath": "stdout.log", "stderrPath": "stderr.log",
        "failureKind": failure_kind,
    }
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=attempt,
                                         prefix=".metadata.", suffix=".tmp",
                                         delete=False) as stream:
            temporary_path = Path(stream.name)
            json.dump(metadata, stream, ensure_ascii=True, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, metadata_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return evidence


def invalidate_stale_evidence(state: RunState, current_head: str) -> RunState:
    """All current gates are code-sensitive until the matrix declares otherwise."""
    _validate_head(current_head)
    head_changed = state.head_commit != current_head
    gates = {
        name: replace(gate, status=GateStatus.PENDING)
        if head_changed or (gate.head_commit != current_head and gate.status != GateStatus.PENDING)
        else gate for name, gate in state.gates.items()
    }
    if not head_changed and gates == state.gates:
        return state
    now = datetime.now(timezone.utc)
    decision = {
        "type": "evidence_invalidated",
        "summary": "HEAD-bound gate evidence invalidated; prior artifacts retained as history",
        "createdAt": _timestamp(now),
    }
    return replace(
        state, head_commit=current_head, gates=gates, updated_at=now,
        status=RunStatus.VERIFYING if state.status == RunStatus.COMPLETED else state.status,
        decisions=state.decisions + (decision,),
    )
