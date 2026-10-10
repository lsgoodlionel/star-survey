#!/usr/bin/env python3
"""Deterministic Harness drills; real Codex is an explicit disposable smoke."""

import argparse
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from fnmatch import fnmatchcase
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Callable, NamedTuple, Optional
from unittest.mock import patch
from uuid import uuid4


if sys.version_info < (3, 11):
    print("agent-harness 需要 Python 3.11 或更高版本；请使用 scripts/agent-harness --python。",
          file=sys.stderr)
    raise SystemExit(2)


REPO = Path(__file__).resolve().parents[3]
HARNESS = REPO / "tools/agent-harness"
sys.path.insert(0, str(HARNESS))

from agent_harness.codex_adapter import CodexFailure, CodexResult, run_codex
from agent_harness.diagnostics import record_failure, redact_text, render_diagnostics
from agent_harness.doctor import run_doctor
from agent_harness.git_guard import capture_snapshot, validate_resume
from agent_harness.run_service import RunService
from agent_harness.state import (
    AttemptState,
    GateEvidence,
    GateStatus,
    RunState,
    RunStatus,
    append_event,
    load_state,
    read_events,
    save_state_atomic,
    transition,
)


class DrillResult(NamedTuple):
    name: str
    status: str
    detail: str


class DrillRefused(ValueError):
    pass


class WorkflowRefused(DrillRefused):
    def __init__(self, line: int, reason: str):
        super().__init__(reason)
        self.line = line
        self.reason = reason


_RUN_ID = "11111111-1111-4111-8111-111111111111"
_DEFAULT_HOST_CONFIG = Path("docs/agent/HARNESS_HOST.json")
_DANGEROUS_CODEX_ARGUMENT = re.compile(
    r"(?:^|\s)(?:--dangerously-[^\s]*bypass[^\s]*|--add-dir(?:=|\s)|"
    r"--config(?:=|\s)|-c(?:=|\s)|--enable(?:=|\s)|--disable(?:=|\s)|"
    r"--profile(?:=|\s))|\bbypass\b|\bdanger-full-access\b",
    re.IGNORECASE,
)


def _run(args, cwd, *, check=True):
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=check)


def _git(repo, *args):
    return _run(["git", *args], repo).stdout.strip()


def _state(root: Path, status=RunStatus.PLANNED, *, gates=None, required=()):
    now = datetime.now(timezone.utc)
    return RunState(
        1,
        _RUN_ID,
        Path("docs/superpowers/plans/drill.md"),
        "a" * 64,
        root.resolve(),
        "feat/drill",
        "b" * 40,
        "b" * 40,
        "drill",
        "Fault drill",
        status,
        AttemptState(0, {}),
        None,
        tuple(required),
        gates or {},
        (),
        (),
        now,
        now,
    )


def _fault_worker(mode: str, state_path: Path) -> int:
    from agent_harness import state as state_module

    current = load_state(state_path)
    candidate = transition(current, RunStatus.ACTIVE, "fault worker")
    real_replace = state_module.os.replace

    def terminate(source, destination):
        if mode == "after":
            real_replace(source, destination)
        os._exit(91 if mode == "before" else 92)

    state_module.os.replace = terminate
    save_state_atomic(state_path, candidate)
    return 99


def _atomic_drill(mode: str) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        path = root / "state.json"
        save_state_atomic(path, _state(root))
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--fault-worker", mode, str(path)],
            text=True,
            capture_output=True,
            check=False,
        )
        expected_code = 91 if mode == "before" else 92
        expected_status = RunStatus.PLANNED if mode == "before" else RunStatus.ACTIVE
        if result.returncode != expected_code or load_state(path).status != expected_status:
            raise AssertionError("atomic publication boundary was not preserved")


def _drill_atomic_before() -> None:
    _atomic_drill("before")


def _drill_atomic_after() -> None:
    _atomic_drill("after")


def _drill_truncated_events() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "events.jsonl"
        append_event(path, "started", {"step": 1})
        append_event(path, "advanced", {"step": 2})
        path.write_bytes(path.read_bytes()[:-7])
        try:
            read_events(path)
        except ValueError:
            return
        raise AssertionError("truncated event chain was accepted")


@contextmanager
def _fixture_worktree():
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name).resolve()
    primary = root / "primary"
    worktree = root / "worktree"
    primary.mkdir()
    try:
        _git(primary, "init", "-b", "main")
        _git(primary, "config", "user.email", "drill@example.invalid")
        _git(primary, "config", "user.name", "Harness Drill")
        _git(primary, "config", "commit.gpgsign", "false")
        files = {
            ".gitignore": "/var/agent-harness/\n",
            "docs/superpowers/plans/drill.md": (
                "# Drill plan\n\n**Status:** approved\n\n"
                "## Milestone m1: Fixture\n\n**Files:**\n"
                "- Modify: `src/example.txt`\n\nAcceptance: the fixture remains bounded.\n"
            ),
            "src/example.txt": "initial\n",
            "docs/agent/GATE_MATRIX.yaml": json.dumps(
                {
                    "version": 1,
                    "gates": [
                        {
                            "id": "unit",
                            "command": [sys.executable, "-c", "print('ok')"],
                            "cwd": ".",
                            "timeout_seconds": 10,
                        }
                    ],
                    "profiles": {
                        "fixture": {
                            "paths": ["src/**", "docs/**"],
                            "gates": ["unit"],
                        }
                    },
                }
            ),
            "docs/agent/PROTECTED_PATHS.yaml": json.dumps(
                {
                    "version": 1,
                    "rules": [
                        {
                            "id": "review",
                            "patterns": ["**"],
                            "operations": ["add", "modify", "delete"],
                            "action": "review_required",
                        }
                    ],
                }
            ),
        }
        for relative, content in files.items():
            path = primary / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        _git(primary, "add", ".")
        _git(primary, "commit", "-m", "drill fixture")
        _git(primary, "worktree", "add", "-b", "feat/drill", str(worktree))
        yield primary, worktree, RunService(worktree)
    finally:
        if worktree.exists():
            _run(["git", "worktree", "remove", "--force", str(worktree)], primary, check=False)
        temporary.cleanup()


def _drill_plan_digest() -> str:
    with _fixture_worktree() as (_, repo, service):
        state = service.init(Path("docs/superpowers/plans/drill.md"), "m1")
        plan = repo / state.plan_path
        plan.write_text(plan.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
        result = service.run_autonomous(state.run_id, 1)
        reasons = [
            decision["summary"]
            for decision in result.decisions
            if decision["type"] == "transition"
        ]
        if result.status != RunStatus.PAUSED or not any("计划摘要" in reason for reason in reasons):
            raise AssertionError("changed plan digest did not produce a paused terminal state")
        return "paused: plan digest drift"


def _drill_rewritten_history() -> None:
    with _fixture_worktree() as (_, repo, service):
        state = service.init(Path("docs/superpowers/plans/drill.md"), "m1")
        target = repo / "src/example.txt"
        target.write_text("child\n", encoding="utf-8")
        _git(repo, "add", "src/example.txt")
        _git(repo, "commit", "-m", "child\n\nAgent-Run-Id: " + state.run_id)
        child = _git(repo, "rev-parse", "HEAD")
        recorded = replace(state, head_commit=child, changed_paths=(Path("src/example.txt"),))
        target.write_text("rewritten\n", encoding="utf-8")
        _git(repo, "add", "src/example.txt")
        _git(repo, "commit", "--amend", "-m", "rewritten\n\nAgent-Run-Id: " + state.run_id)
        decision = validate_resume(capture_snapshot(repo), recorded)
        if decision.allowed or decision.reason != "history_drift":
            raise AssertionError("rewritten history was not rejected")


def _drill_missing_runtimes() -> None:
    with tempfile.TemporaryDirectory() as directory, patch(
        "agent_harness.doctor.shutil.which", return_value=None
    ), patch("agent_harness.doctor.shutil.disk_usage", return_value=shutil.disk_usage(directory)):
        report = run_doctor(Path(directory)).to_dict()
    warnings = {item["id"] for item in report["warning"]}
    if not {"docker", "node", "java"} <= warnings:
        raise AssertionError("missing optional runtimes were not typed as warnings")


def _drill_fake_secret() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        evidence_dir = root / "var/agent-harness/runs" / _RUN_ID / "evidence/unit"
        evidence_dir.mkdir(parents=True)
        relative = evidence_dir.relative_to(root) / "metadata.json"
        (evidence_dir / "metadata.json").write_text("{}\n", encoding="utf-8")
        (evidence_dir / "stdout.log").write_text("starting\n", encoding="utf-8")
        (evidence_dir / "stderr.log").write_text(
            "ERROR: request failed Authorization: Bearer fake-secret\n", encoding="utf-8"
        )
        now = datetime.now(timezone.utc)
        gate = GateEvidence("unit", GateStatus.FAILED, 1, now, now, relative, "b" * 40)
        state = _state(root, RunStatus.PAUSED, gates={"unit": gate}, required=("unit",))
        output = root / "var/agent-harness/runs" / _RUN_ID / "diagnostics.md"
        render_diagnostics(state, (gate,), output)
        report = output.read_text(encoding="utf-8")
        if "fake-secret" in report or "[REDACTED]" not in report:
            raise AssertionError("diagnostic report exposed a secret")


def _failed_state(root: Path) -> RunState:
    now = datetime.now(timezone.utc)
    gate = GateEvidence("unit", GateStatus.FAILED, 1, now, now, None, "b" * 40)
    return _state(root, RunStatus.VERIFYING, gates={"unit": gate}, required=("unit",))


def _drill_repeated_gate_failure() -> None:
    with tempfile.TemporaryDirectory() as directory:
        state = _failed_state(Path(directory))
        fingerprint = hashlib.sha256(b"same gate failure").hexdigest()
        decisions = []
        for index in range(3):
            decision = record_failure(
                state, fingerprint, hashlib.sha256(f"diff-{index}".encode()).hexdigest()
            )
            decisions.append(decision.code)
            state = decision.state
        if decisions != ["repair_allowed", "repair_allowed", "same_failure_limit"]:
            raise AssertionError("same-failure threshold changed")
        if state.status != RunStatus.PAUSED or state.attempts.total != 3:
            raise AssertionError("repeated failure did not pause exactly on three")


def _drill_no_progress() -> None:
    with tempfile.TemporaryDirectory() as directory:
        state = _failed_state(Path(directory))
        fingerprint = hashlib.sha256(b"failure").hexdigest()
        diff = hashlib.sha256(b"unchanged").hexdigest()
        first = record_failure(state, fingerprint, diff)
        second = record_failure(first.state, fingerprint, diff)
        if second.code != "no_progress" or second.state.status != RunStatus.PAUSED:
            raise AssertionError("two unchanged cycles did not pause")


def _fake_adapter_commit(repo: Path, run_id: str) -> CodexResult:
    target = repo / "src/example.txt"
    target.write_text("initial\ncontrolled-change\n", encoding="utf-8")
    _git(repo, "add", "src/example.txt")
    _git(repo, "commit", "-m", "test: controlled drill change\n\nAgent-Run-Id: " + run_id)
    return CodexResult("completed", "controlled fixture committed", ("src/example.txt",),
                       ("unit",), False, "22222222-2222-4222-8222-222222222222")


def _drill_success() -> str:
    with _fixture_worktree() as (_, repo, service):
        state = service.init(Path("docs/superpowers/plans/drill.md"), "m1")
        real_gates = service.run_gates

        def fake_codex(command, timeout, event_log):
            return _fake_adapter_commit(repo, state.run_id)

        def reviewed_gates(run_id, extra_gate_ids):
            gated = real_gates(run_id, extra_gate_ids)
            decisions = service._readonly_paths(gated)
            scope = service._review_scope(decisions)
            report = service._directory(run_id) / "review-input.json"
            report.write_text(json.dumps({
                "version": 1,
                "verdict": "approved",
                "reviewer": "deterministic-drill",
                "headCommit": gated.head_commit,
                "changedPathsSha256": scope,
            }, sort_keys=True), encoding="utf-8")
            service.record_review(run_id, "deterministic-drill", report.relative_to(repo))
            return gated

        command = ("fake-codex", "exec", "--sandbox", "workspace-write", "--approve-for-me",
                   "--strict-config", "--json", "--output-schema", "schema", "--cd", str(repo), "prompt")
        with patch("agent_harness.run_service.build_codex_command", return_value=command), \
                patch("agent_harness.run_service.run_codex", side_effect=fake_codex), \
                patch.object(service, "run_gates", side_effect=reviewed_gates):
            final = service.run_autonomous(state.run_id, 1)
        history = repo / "docs/agent/run-history" / (state.run_id + ".md")
        if final.status != RunStatus.COMPLETED or not history.is_file():
            raise AssertionError("RunService did not publish completed history")
        events = read_events(service._directory(state.run_id) / "events.jsonl")
        review_binding = json.loads(next(
            decision["summary"] for decision in reversed(final.decisions)
            if decision["type"] == "review_evidence"
        ))
        expected_scope = service._review_scope(service._readonly_paths(final))
        gate = final.gates["unit"]
        return json.dumps({
            "events": [event["type"] for event in events],
            "status": final.status.value,
            "headCommit": final.head_commit,
            "gateHead": gate.head_commit,
            "reviewHead": review_binding["headCommit"],
            "reviewScope": review_binding["changedPathsSha256"],
            "expectedReviewScope": expected_scope,
            "historyPublished": history.is_file(),
        }, sort_keys=True)


def _drill_codex_timeout() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        output = root / "codex.jsonl"
        fake = ("placeholder",) * 10 + (str(root),)
        command = (sys.executable, "-c", "import time; time.sleep(5)")
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        with patch("agent_harness.codex_adapter._environment", return_value={"PATH": "/usr/bin:/bin"}), patch(
            "agent_harness.codex_adapter._command", return_value=(command, root)
        ), patch("agent_harness.codex_adapter._storage", return_value=(output, descriptor)):
            result = run_codex(fake, 1, output)
        if result.failure != CodexFailure.TIMEOUT:
            raise AssertionError("Codex timeout was not bounded and typed")


_DRILLS: dict[str, Callable[[], Optional[str]]] = {
    "success": _drill_success,
    "atomic-before-replace": _drill_atomic_before,
    "atomic-after-replace": _drill_atomic_after,
    "truncated-events": _drill_truncated_events,
    "plan-digest-drift": _drill_plan_digest,
    "rewritten-history": _drill_rewritten_history,
    "missing-runtimes": _drill_missing_runtimes,
    "fake-secret-diagnostics": _drill_fake_secret,
    "repeated-gate-failure": _drill_repeated_gate_failure,
    "no-progress": _drill_no_progress,
    "codex-timeout": _drill_codex_timeout,
}


def run_named_drill(name: str) -> DrillResult:
    if name not in _DRILLS:
        raise ValueError("unknown drill")
    try:
        detail = _DRILLS[name]()
        return DrillResult(name, "passed", detail or "expected boundary observed")
    except Exception as error:
        detail = type(error).__name__ + ": " + str(error)
        from agent_harness.diagnostics import redact_text

        return DrillResult(name, "failed", redact_text(detail))


def run_fake_drills() -> tuple[DrillResult, ...]:
    return tuple(run_named_drill(name) for name in _DRILLS)


def _repo_path(root: Path, value: str, *, must_exist=True) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or "\\" in value or relative.as_posix() != value:
        raise DrillRefused("host configuration contains an unsafe repository path")
    path = root / relative
    try:
        resolved = path.resolve(strict=must_exist)
    except (OSError, RuntimeError):
        raise DrillRefused("host configuration path is unavailable") from None
    if not resolved.is_relative_to(root):
        raise DrillRefused("host configuration path escapes the repository")
    return path


def load_host_config(repo: Path, relative: Path = _DEFAULT_HOST_CONFIG) -> dict:
    root = Path(repo).resolve(strict=True)
    path = _repo_path(root, Path(relative).as_posix())
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        required = {"version", "activePlan", "deliveryManifest", "ledger", "secretPolicy",
                    "fixture", "documentation"}
        if set(document) != required or document["version"] != 1:
            raise ValueError()
        policy = document["secretPolicy"]
        fixture = document["fixture"]
        docs = document["documentation"]
        if (set(policy) != {"patterns", "allowlist"}
                or not all(isinstance(value, str) and value for value in (*policy["patterns"], *policy["allowlist"]))
                or set(fixture) != {"path", "planPath", "baseline", "expected"}
                or not isinstance(docs.get("statusMarkerPaths"), list)):
            raise ValueError()
        for value in (document["activePlan"], document["deliveryManifest"], document["ledger"],
                      fixture["path"], fixture["planPath"], *docs["statusMarkerPaths"]):
            _repo_path(root, value, must_exist=False)
        plan_root = Path("docs/superpowers/plans")
        if (not Path(document["activePlan"]).is_relative_to(plan_root)
                or not Path(fixture["planPath"]).is_relative_to(plan_root)):
            raise ValueError()
    except (KeyError, TypeError, ValueError, OSError, UnicodeError, json.JSONDecodeError):
        raise DrillRefused("invalid Harness host configuration") from None
    return document


def _matches(path: str, patterns) -> bool:
    return any(fnmatchcase(path, pattern) for pattern in patterns)


def _git_inventory(root: Path) -> tuple[Path, ...]:
    values = set()
    commands = (
        ("ls-files", "-z", "--cached"),
        ("ls-files", "-z", "--others", "--exclude-standard"),
        ("ls-files", "-z", "--others", "--ignored", "--exclude-standard"),
    )
    for command in commands:
        result = _run(["git", *command], root, check=False)
        if result.returncode:
            raise DrillRefused("unable to build the repository path inventory")
        values.update(value for value in result.stdout.split("\0") if value)
    return tuple(Path(value) for value in sorted(values))


def ensure_no_production_secret_paths(repo: Path, policy: dict) -> None:
    root = Path(repo).resolve(strict=True)
    patterns = tuple(policy["patterns"])
    allowlist = tuple(policy["allowlist"])
    for relative in _git_inventory(root):
        value = relative.as_posix()
        path = root / relative
        try:
            metadata = path.lstat()
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            raise DrillRefused("repository inventory contains an unreadable path") from None
        if not resolved.is_relative_to(root):
            raise DrillRefused("repository path resolves outside the worktree")
        if path.is_symlink() and resolved.is_dir():
            raise DrillRefused("repository contains a directory symlink")
        if metadata.st_mode == 0:
            raise DrillRefused("repository inventory path has invalid metadata")
        if _matches(value, patterns) and not _matches(value, allowlist):
            raise DrillRefused("repository contains a production secret path")


def atomic_create(repo: Path, path: Path, content: str) -> None:
    root = Path(repo).resolve(strict=True)
    target = Path(path)
    if not target.is_absolute():
        target = root / target
    try:
        relative = target.relative_to(root)
    except ValueError:
        raise DrillRefused("smoke input path escapes the worktree") from None
    if not relative.parts or relative.name in ("", ".", "..") or not hasattr(os, "O_NOFOLLOW"):
        raise DrillRefused("smoke input path is unsupported")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptors = []
    identities = []
    temporary = "." + relative.name + "." + uuid4().hex + ".tmp"
    descriptor = None
    try:
        descriptors.append(os.open(root, directory_flags))
        identities.append(os.fstat(descriptors[-1]))
        for part in relative.parent.parts:
            try:
                os.mkdir(part, 0o755, dir_fd=descriptors[-1])
            except FileExistsError:
                pass
            child = os.open(part, directory_flags, dir_fd=descriptors[-1])
            metadata = os.stat(part, dir_fd=descriptors[-1], follow_symlinks=False)
            opened = os.fstat(child)
            if (not stat.S_ISDIR(metadata.st_mode)
                    or (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino)):
                os.close(child)
                raise DrillRefused("smoke input parent identity changed")
            descriptors.append(child)
            identities.append(opened)

        def verify_chain():
            for index, part in enumerate(relative.parent.parts):
                metadata = os.stat(part, dir_fd=descriptors[index], follow_symlinks=False)
                expected = identities[index + 1]
                if (not stat.S_ISDIR(metadata.st_mode)
                        or (metadata.st_dev, metadata.st_ino) != (expected.st_dev, expected.st_ino)):
                    raise DrillRefused("smoke input parent identity changed")

        parent = descriptors[-1]
        try:
            os.stat(relative.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise DrillRefused("smoke input path already exists")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600, dir_fd=parent)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.stat(relative.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise DrillRefused("smoke input path changed during creation")
        verify_chain()
        os.replace(temporary, relative.name, src_dir_fd=parent, dst_dir_fd=parent)
        verify_chain()
        os.fsync(parent)
    except (OSError, RuntimeError) as error:
        if isinstance(error, DrillRefused):
            raise
        raise DrillRefused("smoke input could not be created safely") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if descriptors:
            try:
                os.unlink(temporary, dir_fd=descriptors[-1])
            except FileNotFoundError:
                pass
            except OSError:
                pass
        for directory in reversed(descriptors):
            os.close(directory)


def _contains_decoded_run_key(source: str) -> bool:
    for match in re.finditer(r'("(?:\\.|[^"\\])*")\s*:', source):
        try:
            if json.loads(match[1]) == "run":
                return True
        except json.JSONDecodeError:
            continue
    return False


def _decode_inline_run_scalar(value: str, line: int) -> str:
    value = value.strip()
    if not value:
        return value
    if value.startswith("'"):
        if re.fullmatch(r"'(?:[^']|'')*'", value) is None:
            raise WorkflowRefused(line, "workflow run value uses unsupported YAML quoting")
        return value[1:-1].replace("''", "'")
    if value.startswith('"'):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            raise WorkflowRefused(line, "workflow run value uses unsupported YAML quoting") from None
        if not isinstance(decoded, str):
            raise WorkflowRefused(line, "workflow run value is not a string")
        return decoded
    return value


def _workflow_run_blocks(text: str) -> tuple[tuple[int, str], ...]:
    lines = text.splitlines()
    blocks = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue
        match = re.match(r'''^(\s*)-?\s*(?:run|"run"|'run')\s*:\s*(.*)$''',
                         lines[index])
        if not match:
            if re.match(r"^\s*-\s*\?\s*$", lines[index]):
                raise WorkflowRefused(index + 1, "workflow explicit run key is unsupported")
            if re.match(r'''^\s*-?\s*(?:[&!][^\s]+\s+|\?\s+)(?:run|"run"|'run')\b''',
                        lines[index]):
                raise WorkflowRefused(index + 1, "workflow run key uses unsupported YAML syntax")
            if _contains_decoded_run_key(lines[index]):
                raise WorkflowRefused(index + 1, "workflow run key uses unsupported YAML syntax")
            if re.search(r'''(?:^|\s)![^\s]+\s+(?:run|"run"|'run')\s*:''',
                         lines[index]):
                raise WorkflowRefused(index + 1, "workflow run key uses unsupported YAML syntax")
            if "{" in stripped and re.search(
                    r'''(?:^|[{,])\s*(?:run|"run"|'run')\s*:''', stripped):
                raise WorkflowRefused(index + 1,
                                      "workflow run step uses unsupported YAML flow syntax")
            index += 1
            continue
        indent, value = len(match[1]), match[2]
        start_line = index + 1
        if value in ("|", "|-", ">", ">-"):
            values = []
            index += 1
            while index < len(lines):
                following = lines[index]
                if following.strip() and len(following) - len(following.lstrip()) <= indent:
                    break
                values.append(following.strip())
                index += 1
            block = "\n".join(values).strip()
            if value.startswith(">") and _looks_codex_like(block):
                raise WorkflowRefused(start_line,
                                      "Codex-like folded run scalar is unsupported")
            blocks.append((start_line, block))
            continue
        if value.startswith(("|", ">", "!", "&", "*")):
            raise WorkflowRefused(start_line, "workflow run value uses unsupported YAML syntax")
        blocks.append((start_line, _decode_inline_run_scalar(value, start_line)))
        index += 1
    return tuple(blocks)


def _could_resolve_to_codex(value: str) -> bool:
    name = Path(value).name.lower()
    if name == "codex":
        return True
    if fnmatchcase("codex", name):
        return True
    brace = re.search(r"\{([^{}]+)\}", name)
    if brace is not None:
        return any(
            _could_resolve_to_codex(name[:brace.start()] + alternative + name[brace.end():])
            for alternative in brace[1].split(",")
        )
    without_expansions = re.sub(r"\$\{[^}]*\}|\$\([^)]*\)|`[^`]*`", "", name)
    if Path(without_expansions).name == "codex":
        return True
    return "$" in name and name.startswith("co") and name.endswith("dex")


def _looks_codex_like(source: str) -> bool:
    lowered = re.sub(r'["\']', "", source.lower())
    return ("codex" in lowered or re.search(
        r"co(?:(?:[\"']{2}|\$\{[^}]*\})+dex|(?:\[[^]]+\]|\{[^}]+\}|[?*])ex)",
        lowered,
    ) is not None or re.search(
        r"(?:^|[\s/])[^\s;|]*[?*\[\]{}][^\s;|]*\s+exec\b.*--sandbox",
        lowered,
    ) is not None)


def _shell_segments(source: str) -> tuple[tuple[str, ...], ...]:
    segments = []
    logical_source = source.replace("\\\n", " ")
    for line in logical_source.splitlines() or [logical_source]:
        if not _looks_codex_like(line):
            continue
        lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        lexer.commenters = "#"
        current = []
        try:
            tokens = tuple(lexer)
        except ValueError:
            raise DrillRefused("workflow command uses unsupported shell quoting") from None
        for token in tokens:
            if token and all(character in ";&|<>" for character in token):
                if current:
                    segments.append(tuple(current))
                    current = []
                continue
            current.append(token)
        if current:
            segments.append(tuple(current))
    return tuple(segments)


def _validate_direct_codex(arguments: tuple[str, ...], source: str) -> None:
    executable = arguments[0]
    if executable != "codex":
        raise DrillRefused("Codex workflow command uses an unsupported executable wrapper")
    if re.search(r"[\"'`$\\;&|<>]|[\r\n]", source):
        raise DrillRefused("Codex workflow command uses unsupported shell syntax")
    if len(arguments) < 2 or arguments[1] != "exec":
        raise DrillRefused("Codex workflow command is not an allowed exec invocation")
    if _DANGEROUS_CODEX_ARGUMENT.search(" ".join(arguments)):
        raise DrillRefused("Codex command contains a forbidden permission override")
    positionals = []
    sandbox = None
    index = 2
    while index < len(arguments):
        value = arguments[index]
        if value == "--json":
            index += 1
            continue
        if value in ("--sandbox", "--output-schema"):
            if index + 1 >= len(arguments) or arguments[index + 1].startswith("-"):
                raise DrillRefused("Codex workflow option is missing its value")
            if value == "--sandbox":
                sandbox = arguments[index + 1]
            index += 2
            continue
        if value.startswith("--sandbox="):
            sandbox = value.partition("=")[2]
            index += 1
            continue
        if value.startswith("-"):
            raise DrillRefused("Codex workflow command contains an unsupported option")
        positionals.append(value)
        index += 1
    if sandbox != "workspace-write":
        raise DrillRefused("Codex workflow sandbox is not workspace-write")
    if len(positionals) != 1:
        raise DrillRefused("Codex workflow command must contain one normalized prompt argument")


def _validate_command_segment(arguments: tuple[str, ...], source: str) -> bool:
    position = 0
    while position < len(arguments) and re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*=.*", arguments[position]):
        position += 1
    if position >= len(arguments):
        return False
    executable = arguments[position]
    name = Path(executable).name.lower()
    remaining = arguments[position + 1:]
    if name in {"sh", "bash", "zsh"}:
        if (len(remaining) >= 2 and remaining[0].startswith("-")
                and "c" in remaining[0][1:]):
            return _validate_codex_shell(remaining[1])
        if _looks_codex_like(" ".join(remaining)):
            raise DrillRefused("Codex workflow command uses unsupported shell interpreter syntax")
        return False
    if name == "eval" and remaining:
        nested = remaining[1:] if remaining[0] == "--" else remaining
        return _validate_codex_shell(" ".join(nested))
    if name == "env":
        nested = 0
        while nested < len(remaining) and (
                remaining[nested].startswith("-")
                or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", remaining[nested])):
            nested += 1
        if nested < len(remaining):
            return _validate_command_segment(tuple(remaining[nested:]),
                                             " ".join(remaining[nested:]))
        return False
    if name == "command":
        nested = 0
        while nested < len(remaining) and remaining[nested].startswith("-"):
            nested += 1
        if nested < len(remaining):
            return _validate_command_segment(tuple(remaining[nested:]),
                                             " ".join(remaining[nested:]))
        return False
    if name in {"exec", "time", "timeout", "xargs", "nice", "nohup"}:
        if _looks_codex_like(" ".join(remaining)):
            raise DrillRefused("Codex workflow command uses an unsupported executable wrapper")
        return False
    if not _could_resolve_to_codex(executable):
        inert = {"echo", "printf", "grep", "egrep", "fgrep", "python", "python3",
                 "node", "rg", "sed", "awk", "cat"}
        if name not in inert and _looks_codex_like(" ".join(arguments)):
            raise DrillRefused("Codex-like command appears in an unsupported executable position")
        return False
    _validate_direct_codex(tuple(arguments[position:]), source)
    return True


def _validate_codex_shell(source: str) -> bool:
    if re.search(r"(?:\$|`|\\).*\bexec\b.*--sandbox", source, re.DOTALL):
        raise DrillRefused("Codex-like exec command uses dynamic shell syntax")
    return any(_validate_command_segment(arguments, source)
               for arguments in _shell_segments(source))


def check_forbidden_options(repo: Path) -> None:
    matrix = json.loads((repo / "docs/agent/GATE_MATRIX.yaml").read_text(encoding="utf-8"))
    contexts = []
    for gate in matrix["gates"]:
        command = gate["command"]
        if command and (Path(command[0]).name == "codex" or "run-codex" in command):
            contexts.append(" ".join(command))
    workflows = repo / ".github/workflows"
    for pattern in ("*.yml", "*.yaml"):
        for workflow in workflows.glob(pattern):
            text = workflow.read_text(encoding="utf-8")
            relative = workflow.relative_to(repo).as_posix()
            try:
                blocks = _workflow_run_blocks(text)
            except WorkflowRefused as error:
                raise DrillRefused(f"{relative}:{error.line}: {error.reason}") from None
            for line, source in blocks:
                try:
                    if _validate_codex_shell(source):
                        contexts.append(source)
                except DrillRefused as error:
                    raise DrillRefused(f"{relative}:{line}: {error}") from None
    if any(_DANGEROUS_CODEX_ARGUMENT.search(context) for context in contexts):
        raise DrillRefused("Codex command contains a forbidden permission override")


def _plan_checkboxes(text: str) -> dict:
    headings = list(re.finditer(r"(?m)^### Task\s+(\d+)\s*:", text))
    result = {}
    for index, heading in enumerate(headings):
        following = re.search(r"(?m)^#{1,3}\s+", text[heading.end():])
        end = heading.end() + following.start() if following else len(text)
        boxes = re.findall(r"(?m)^- \[([ xX])\]", text[heading.end():end])
        result[heading[1]] = {
            "completed": [position for position, value in enumerate(boxes, 1) if value.lower() == "x"],
            "pending": [position for position, value in enumerate(boxes, 1) if value == " "],
        }
    final = re.search(r"(?m)^## Final Acceptance\s*$", text)
    if final:
        following = re.search(r"(?m)^#{1,2}\s+", text[final.end():])
        end = final.end() + following.start() if following else len(text)
        boxes = re.findall(r"(?m)^- \[([ xX])\]", text[final.end():end])
        result["finalAcceptance"] = {
            "completed": [position for position, value in enumerate(boxes, 1) if value.lower() == "x"],
            "pending": [position for position, value in enumerate(boxes, 1) if value == " "],
        }
    return result


def _canonical_review_verdicts(content: str) -> tuple[str, str]:
    keys = re.findall(r"\b(SPEC_COMPLIANCE|CODE_QUALITY)\s*=", content)
    if keys.count("SPEC_COMPLIANCE") != 1 or keys.count("CODE_QUALITY") != 1:
        raise ValueError()
    match = re.search(
        r"(?:\A|\n)SPEC_COMPLIANCE=([A-Z][A-Z_]*)\n"
        r"CODE_QUALITY=([A-Z][A-Z_]*)\n?\Z",
        content,
    )
    if match is None:
        raise ValueError()
    start = match.start()
    if content[start:start + 1] == "\n":
        start += 1
    if start and not content[:start].endswith("\n\n"):
        raise ValueError()
    prefix = content[:start]
    if (re.search(r"(?is)<(?:script|pre|style|textarea|xmp|iframe|noframes|plaintext|title)\b",
                  prefix)
            or "<?" in prefix or "<![CDATA[" in prefix
            or re.search(r"(?m)^ {0,3}<![A-Z]", prefix)):
        raise ValueError()
    fence = None
    in_comment = False
    for line in prefix.splitlines():
        comment_position = 0
        while comment_position < len(line):
            if in_comment:
                closing = line.find("-->", comment_position)
                if closing < 0:
                    break
                in_comment = False
                comment_position = closing + 3
            else:
                opening = line.find("<!--", comment_position)
                if opening < 0:
                    break
                in_comment = True
                comment_position = opening + 4
        fence_match = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence_match is not None and not in_comment:
            marker = fence_match[1]
            if fence is None:
                fence = (marker[0], len(marker))
            elif marker[0] == fence[0] and len(marker) >= fence[1]:
                fence = None
    if fence is not None or in_comment:
        raise ValueError()
    return match[1], match[2]


def _validate_report_verdicts(content: str, *, spec_verdict: str,
                              code_quality_verdict: str) -> None:
    verdicts = _canonical_review_verdicts(content)
    expected = (spec_verdict, code_quality_verdict)
    if verdicts != expected or verdicts != ("APPROVED", "APPROVED"):
        raise ValueError()


def _validate_review_evidence(root: Path, manifest: dict, complete: bool) -> None:
    evidence = manifest["reviewEvidence"]
    required = ["security", "dx_ci", "whole_branch"]
    if (set(evidence) != {"requiredReviewers", "reports"}
            or evidence["requiredReviewers"] != required
            or not isinstance(evidence["reports"], list)):
        raise ValueError()
    reports = evidence["reports"]
    if not complete:
        if reports:
            raise ValueError()
        return
    if len(reports) != len(required):
        raise ValueError()
    commit = manifest["implementationCommit"]
    seen = set()
    seen_paths = set()
    seen_digests = set()
    for report in reports:
        keys = {"reviewer", "path", "reviewedCommit", "reviewedRange", "sha256",
                "specVerdict", "codeQualityVerdict"}
        if set(report) != keys or report["reviewer"] not in required or report["reviewer"] in seen:
            raise ValueError()
        seen.add(report["reviewer"])
        if (report["reviewedCommit"] != commit
                or report["specVerdict"] != "APPROVED"
                or report["codeQualityVerdict"] != "APPROVED"):
            raise ValueError()
        match = re.fullmatch(r"([0-9a-f]{40})\.\.([0-9a-f]{40})", report["reviewedRange"])
        if match is None or match[2] != commit:
            raise ValueError()
        ancestor = _run(["git", "merge-base", "--is-ancestor", match[1], commit], root,
                        check=False)
        if ancestor.returncode:
            raise ValueError()
        path = _repo_path(root, report["path"])
        absolute_path = path.absolute()
        resolved_path = path.resolve(strict=True)
        if (absolute_path != resolved_path or report["path"] in seen_paths
                or report["sha256"] in seen_digests):
            raise ValueError()
        seen_paths.add(report["path"])
        seen_digests.add(report["sha256"])
        tracked = _run(["git", "ls-files", "--error-unmatch", "--", report["path"]], root,
                       check=False)
        content = path.read_text(encoding="utf-8")
        if (tracked.returncode or hashlib.sha256(path.read_bytes()).hexdigest() != report["sha256"]
                or report["reviewedRange"] not in content):
            raise ValueError()
        _validate_report_verdicts(
            content,
            spec_verdict=report["specVerdict"],
            code_quality_verdict=report["codeQualityVerdict"],
        )
    if seen != set(required):
        raise ValueError()


def check_docs(repo: Path, host: dict) -> None:
    root = Path(repo).resolve(strict=True)
    plan_path = _repo_path(root, host["activePlan"])
    manifest_path = _repo_path(root, host["deliveryManifest"])
    ledger_path = _repo_path(root, host["ledger"])
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        required = {"version", "planPath", "implementationCommit", "evidence", "taskSteps",
                    "deliveryStatus", "externalSync", "reviewEvidence"}
        if set(manifest) != required or manifest["version"] != 1:
            raise ValueError()
        if manifest["planPath"] != host["activePlan"]:
            raise ValueError()
        commit = manifest["implementationCommit"]
        evidence = manifest["evidence"]
        if (re.fullmatch(r"[0-9a-f]{40}", commit) is None
                or evidence.get("headCommit") != commit or evidence.get("status") != "passed"
                or not evidence.get("commands")):
            raise ValueError()
        commit_check = _run(["git", "cat-file", "-e", commit + "^{commit}"], root, check=False)
        if commit_check.returncode:
            raise ValueError()
        if _plan_checkboxes(plan_path.read_text(encoding="utf-8")) != manifest["taskSteps"]:
            raise ValueError()
        external = manifest["externalSync"]
        allowed_status = {
            ("pending", "pending", "pending"): "local_validated_sync_pending",
            ("complete", "pending", "pending"): "locally_reviewed_sync_pending",
            ("complete", "complete", "pending"): "github_synced_obsidian_pending",
            ("complete", "complete", "complete"): "fully_synchronized",
        }
        key = (external.get("independentReview"), external.get("github"), external.get("obsidian"))
        if allowed_status.get(key) != manifest["deliveryStatus"]:
            raise ValueError()
        _validate_review_evidence(root, manifest, external.get("independentReview") == "complete")
        ledger = ledger_path.read_text(encoding="utf-8")
        for task, steps in manifest["taskSteps"].items():
            if not task.isdigit():
                continue
            expected = "complete" if not steps["pending"] else "in_progress"
            if re.search(r"(?m)^Task " + re.escape(task) + r": " + expected + r"\b", ledger) is None:
                raise ValueError()
        marker = "<!-- harness-delivery-status: " + manifest["deliveryStatus"] + " -->"
        for relative in host["documentation"]["statusMarkerPaths"]:
            if marker not in _repo_path(root, relative).read_text(encoding="utf-8"):
                raise ValueError()
    except (KeyError, TypeError, ValueError, OSError, UnicodeError, json.JSONDecodeError):
        raise DrillRefused("delivery manifest, plan, ledger, or host documentation drifted") from None


def _primary_identity(repo: Path) -> tuple[str, bytes]:
    return _git(repo, "rev-parse", "HEAD"), _run(
        ["git", "status", "--porcelain=v1", "-z"], repo
    ).stdout.encode()


def real_smoke_branch_name() -> str:
    return "drill/real-codex-smoke-" + uuid4().hex[:12]


def real_smoke_detail(state: RunState, changed: tuple[str, ...], history: Path,
                      cleanup_verified=False) -> str:
    reason = next(
        (
            decision["summary"]
            for decision in reversed(state.decisions)
            if decision["type"] == "transition"
        ),
        "terminal state recorded without a transition summary",
    )
    detail = {
            "status": state.status.value,
            "stopReason": redact_text(reason),
            "changedPaths": changed,
            "passedGates": sum(
                1 for gate in state.gates.values() if gate.status == GateStatus.PASSED
            ),
            "history": history.as_posix(),
        }
    if cleanup_verified:
        detail["cleanup"] = "verified"
    return json.dumps(detail, sort_keys=True)


def validate_real_smoke_terminal(repo: Path, state: RunState, changed: tuple[str, ...],
                                 fixture: Path, history: Path, expected: str) -> None:
    root = Path(repo).resolve(strict=True)
    history_text = history.read_text(encoding="utf-8") if history.is_file() else ""
    if not history_text or redact_text(history_text) != history_text:
        raise AssertionError("terminal diagnostic history is missing or unsanitized")
    if state.status == RunStatus.PAUSED:
        return
    if state.status != RunStatus.COMPLETED:
        raise AssertionError("real Codex did not produce an accepted terminal state")
    relative = fixture.resolve(strict=True).relative_to(root).as_posix()
    if changed != (relative,) or fixture.read_text(encoding="utf-8") != expected:
        raise AssertionError("completed smoke fixture does not match the exact expected semantics")
    if not state.required_gates or _git(root, "rev-parse", "HEAD") != state.head_commit:
        raise AssertionError("completed smoke lacks required gates or current HEAD binding")
    for identifier in state.required_gates:
        gate = state.gates.get(identifier)
        if (gate is None or gate.status != GateStatus.PASSED or gate.exit_code != 0
                or gate.head_commit != state.head_commit or gate.evidence_path is None):
            raise AssertionError("completed smoke lacks passing HEAD-bound Gate evidence")
        evidence = root / gate.evidence_path
        if not evidence.is_file() or not evidence.resolve().is_relative_to(root):
            raise AssertionError("completed smoke Gate evidence is unavailable")


def cleanup_real_smoke(primary: Path, worktree: Path, branch: Optional[str], *, execute=_run) -> tuple[str, ...]:
    errors = []
    try:
        present = worktree.exists()
    except OSError:
        present = True
        errors.append("worktree-path-precheck-oserror")
    if present:
        try:
            result = execute(["git", "worktree", "remove", "--force", str(worktree)], primary, check=False)
            if result.returncode:
                errors.append("worktree-remove-exit-" + str(result.returncode))
        except OSError:
            errors.append("worktree-remove-oserror")
    try:
        result = execute(["git", "worktree", "prune"], primary, check=False)
        if result.returncode:
            errors.append("worktree-prune-exit-" + str(result.returncode))
    except OSError:
        errors.append("worktree-prune-oserror")
    if branch:
        try:
            result = execute(["git", "branch", "-D", branch], primary, check=False)
            if result.returncode:
                errors.append("branch-delete-exit-" + str(result.returncode))
        except OSError:
            errors.append("branch-delete-oserror")
    try:
        listing = execute(["git", "worktree", "list", "--porcelain"], primary, check=False)
        if listing.returncode or str(worktree) in listing.stdout:
            errors.append("worktree-registration-present")
    except OSError:
        errors.append("worktree-registration-check-oserror")
    if branch:
        try:
            reference = execute(["git", "show-ref", "--verify", "--quiet", "refs/heads/" + branch],
                                primary, check=False)
            if reference.returncode not in (0, 1) or reference.returncode == 0:
                errors.append("branch-reference-present")
        except OSError:
            errors.append("branch-reference-check-oserror")
    try:
        present = worktree.exists()
    except OSError:
        present = True
        errors.append("worktree-path-postcheck-oserror")
    if present:
        errors.append("worktree-path-present")
    return tuple(errors)


def run_real_codex_smoke(repo: Path, host_config: Path = _DEFAULT_HOST_CONFIG) -> DrillResult:
    """Run exactly one Codex cycle in a temporary linked worktree."""
    primary = Path(repo).resolve(strict=True)
    before = _primary_identity(primary)
    temporary = tempfile.TemporaryDirectory()
    worktree = Path(temporary.name).resolve() / "codex-smoke"
    run_id = None
    branch = real_smoke_branch_name()
    branch_created = False
    terminal = None
    changed = ()
    history_relative = None
    failure = None
    cleanup_errors = ()
    try:
        _git(primary, "worktree", "add", "--detach", str(worktree), "HEAD")
        _git(worktree, "switch", "-c", branch)
        branch_created = True
        _git(worktree, "config", "user.email", "drill@example.invalid")
        _git(worktree, "config", "user.name", "Harness Drill")
        host = load_host_config(worktree, host_config)
        ensure_no_production_secret_paths(worktree, host["secretPolicy"])
        fixture_spec = host["fixture"]
        fixture = _repo_path(worktree, fixture_spec["path"], must_exist=False)
        plan = _repo_path(worktree, fixture_spec["planPath"], must_exist=False)
        atomic_create(worktree, fixture, fixture_spec["baseline"])
        atomic_create(worktree, plan,
            "# Disposable real Codex smoke\n\n**Status:** approved\n\n"
            "## Milestone smoke: Add expected fixture line\n\n"
            "**Files:**\n- Modify: `" + fixture.relative_to(worktree).as_posix() + "`\n\n"
            "Acceptance: append exactly one line containing `real-codex-smoke-ok`; commit only the fixture "
            "with the supplied Agent-Run-Id trailer.\n"
        )
        _git(worktree, "add", str(fixture.relative_to(worktree)), str(plan.relative_to(worktree)))
        _git(worktree, "commit", "-m", "test: seed disposable Codex smoke")
        service = RunService(worktree)
        state = service.init(plan.relative_to(worktree), "smoke")
        run_id = state.run_id
        result = service.run_autonomous(run_id, 1)
        changed = tuple(
            path
            for path in _git(worktree, "diff", "--name-only", state.base_commit, "HEAD").splitlines()
            if path
        )
        permitted = (fixture.relative_to(worktree).as_posix(),)
        if changed and changed != permitted:
            raise AssertionError("real Codex changed paths outside the fixture")
        history = worktree / "docs/agent/run-history" / (run_id + ".md")
        validate_real_smoke_terminal(worktree, result, changed, fixture, history,
                                     fixture_spec["expected"])
        terminal = result
        history_relative = history.relative_to(worktree)
    except Exception as error:
        failure = redact_text(type(error).__name__ + ": " + str(error))
        if run_id:
            history = worktree / "docs/agent/run-history" / (run_id + ".md")
            if history.is_file():
                failure = "sanitized terminal evidence: " + history.relative_to(worktree).as_posix()
    finally:
        cleanup_errors = cleanup_real_smoke(primary, worktree, branch if branch_created else None)
        try:
            temporary.cleanup()
        except OSError:
            cleanup_errors += ("temporary-cleanup-oserror",)
        try:
            identity_changed = _primary_identity(primary) != before
        except Exception:
            identity_changed = True
            cleanup_errors += ("primary-identity-check-oserror",)
    if cleanup_errors or identity_changed:
        issues = cleanup_errors + (("primary-identity-changed",) if identity_changed else ())
        detail = ",".join(issues)
        return DrillResult("real-codex-smoke", "failed", redact_text(detail))
    if failure is not None or terminal is None or history_relative is None:
        return DrillResult("real-codex-smoke", "failed", failure or "smoke did not produce a terminal result")
    return DrillResult("real-codex-smoke", terminal.status.value,
                       real_smoke_detail(terminal, changed, history_relative, cleanup_verified=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run deterministic Harness fault drills")
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--host-config", type=Path, default=_DEFAULT_HOST_CONFIG)
    parser.add_argument("--allow-real-codex", action="store_true")
    parser.add_argument("--check-forbidden-options", action="store_true")
    parser.add_argument("--check-docs", action="store_true")
    parser.add_argument("--fail-on-skip", action="store_true")
    parser.add_argument("--fault-worker", choices=("before", "after"), help=argparse.SUPPRESS)
    parser.add_argument("state_path", nargs="?", type=Path, help=argparse.SUPPRESS)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    repo = args.repo.resolve(strict=True)
    if args.fault_worker:
        if args.state_path is None:
            return 2
        return _fault_worker(args.fault_worker, args.state_path)
    try:
        if args.check_forbidden_options:
            check_forbidden_options(repo)
            print("PASS forbidden-option-policy")
            return 0
        if args.check_docs:
            check_docs(repo, load_host_config(repo, args.host_config))
            print("PASS plan-memory-drift")
            return 0
        if args.allow_real_codex:
            result = run_real_codex_smoke(repo, args.host_config)
            print(result.status.upper(), result.name, result.detail)
            return 0 if result.status in {"passed", "completed", "paused"} else 1
        results = run_fake_drills()
        for result in results:
            print(result.status.upper(), result.name, result.detail)
        return 0 if all(result.status == "passed" for result in results) else 1
    except (DrillRefused, OSError, ValueError) as error:
        from agent_harness.diagnostics import redact_text

        print("REFUSED", redact_text(str(error)))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
