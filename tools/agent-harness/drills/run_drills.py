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
import shutil
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
        scope = service._autonomous_scope(state)
        state = service._save(
            service._decision(state, "authorized_paths", json.dumps(list(scope))),
            "fake_adapter_started",
        )
        adapter = _fake_adapter_commit(repo, state.run_id)
        if adapter.status != "completed" or adapter.needs_human:
            raise AssertionError("fake adapter did not produce a controlled commit")
        state = service.run_gates(state.run_id, adapter.tests_requested)
        decisions = service._readonly_paths(state)
        report = service._directory(state.run_id) / "review-input.json"
        report.write_text(json.dumps({
            "version": 1,
            "verdict": "approved",
            "reviewer": "deterministic-drill",
            "headCommit": state.head_commit,
            "changedPathsSha256": service._review_scope(decisions),
        }, sort_keys=True), encoding="utf-8")
        service.record_review(state.run_id, "deterministic-drill", report.relative_to(repo))
        history = service.finalize(state.run_id)
        final = service.status(state.run_id)
        if final.status != RunStatus.COMPLETED or not history.is_file():
            raise AssertionError("RunService did not publish completed history")
        return json.dumps({"flow": ["init", "fake-adapter", "gate", "review", "finalize", "history"],
                           "status": final.status.value}, sort_keys=True)


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
    current = root
    for part in relative.parent.parts:
        current /= part
        if current.is_symlink():
            raise DrillRefused("smoke input parent is a symlink")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.lstat()
    except FileNotFoundError:
        pass
    else:
        raise DrillRefused("smoke input path already exists")
    descriptor = None
    temporary = target.parent / ("." + target.name + "." + uuid4().hex + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            target.lstat()
        except FileNotFoundError:
            pass
        else:
            raise DrillRefused("smoke input path changed during creation")
        os.replace(temporary, target)
        directory = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


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
            contexts.extend(match.group(0) for match in re.finditer(
                r"(?im)\bcodex(?:\s+exec)?\b[^\n]*(?:\n[ \t]{8,}[^\n]*)*", text
            ))
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


def check_docs(repo: Path, host: dict) -> None:
    root = Path(repo).resolve(strict=True)
    plan_path = _repo_path(root, host["activePlan"])
    manifest_path = _repo_path(root, host["deliveryManifest"])
    ledger_path = _repo_path(root, host["ledger"])
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        required = {"version", "planPath", "implementationCommit", "evidence", "taskSteps",
                    "deliveryStatus", "externalSync"}
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
            ("complete", "complete", "complete"): "fully_synchronized",
        }
        key = (external.get("independentReview"), external.get("github"), external.get("obsidian"))
        if allowed_status.get(key) != manifest["deliveryStatus"]:
            raise ValueError()
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


def cleanup_real_smoke(primary: Path, worktree: Path, branch: Optional[str]) -> tuple[str, ...]:
    errors = []
    if worktree.exists():
        result = _run(["git", "worktree", "remove", "--force", str(worktree)], primary, check=False)
        if result.returncode:
            errors.append("worktree-remove-exit-" + str(result.returncode))
    result = _run(["git", "worktree", "prune"], primary, check=False)
    if result.returncode:
        errors.append("worktree-prune-exit-" + str(result.returncode))
    if branch:
        result = _run(["git", "branch", "-D", branch], primary, check=False)
        if result.returncode:
            errors.append("branch-delete-exit-" + str(result.returncode))
    listing = _run(["git", "worktree", "list", "--porcelain"], primary, check=False)
    if listing.returncode or str(worktree) in listing.stdout:
        errors.append("worktree-registration-present")
    if branch:
        reference = _run(["git", "show-ref", "--verify", "--quiet", "refs/heads/" + branch],
                         primary, check=False)
        if reference.returncode not in (0, 1) or reference.returncode == 0:
            errors.append("branch-reference-present")
    if worktree.exists():
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
        temporary.cleanup()
        try:
            identity_changed = _primary_identity(primary) != before
        except Exception:
            identity_changed = True
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
