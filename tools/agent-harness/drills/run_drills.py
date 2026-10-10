#!/usr/bin/env python3
"""Deterministic Harness drills; real Codex is an explicit disposable smoke."""

import argparse
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Callable, NamedTuple
from unittest.mock import patch
from uuid import uuid4


REPO = Path(__file__).resolve().parents[3]
HARNESS = REPO / "tools/agent-harness"
sys.path.insert(0, str(HARNESS))

from agent_harness.codex_adapter import CodexFailure, run_codex
from agent_harness.diagnostics import record_failure, render_diagnostics
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
_FORBIDDEN_OPTIONS = {
    "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-bypass-hook-trust",
}


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


def _drill_success() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        now = datetime.now(timezone.utc)
        gate = GateEvidence("unit", GateStatus.PASSED, 0, now, now, Path("evidence.json"), "b" * 40)
        state = _state(root, RunStatus.PLANNED, gates={"unit": gate}, required=("unit",))
        state = transition(state, RunStatus.ACTIVE, "start")
        state = transition(state, RunStatus.VERIFYING, "verify")
        state = transition(state, RunStatus.COMPLETED, "passed")
        if state.status != RunStatus.COMPLETED:
            raise AssertionError("passing state did not complete")


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


_DRILLS: dict[str, Callable[[], str | None]] = {
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


def _looks_like_secret(relative: Path) -> bool:
    value = relative.as_posix()
    parts = set(relative.parts)
    name = relative.name
    return (
        bool(parts & {".aws", ".ssh", "secrets"})
        or name == ".env"
        or name.startswith(".env.")
        or name.startswith(("id_rsa", "id_ed25519"))
        or relative.suffix in {".pem", ".key"}
        or value
        in {
            "application/config/config.php",
            "application/config/security.php",
            "application/config/allowed_hosts.php",
        }
    )


def _known_safe_secret_fixture(relative: Path) -> bool:
    value = "/" + relative.as_posix()
    return (
        "/tests/fixtures/" in value
        or relative.parent == Path("editor")
        and (relative.name == ".env" or relative.name.startswith(".env."))
    )


def ensure_no_production_secret_paths(repo: Path) -> None:
    root = Path(repo).resolve(strict=True)
    for current, directories, files in os.walk(root):
        directories[:] = [
            name
            for name in directories
            if name not in {".git", "node_modules", "vendor", "var", ".worktrees"}
        ]
        for name in files:
            path = Path(current) / name
            relative = path.relative_to(root)
            if _looks_like_secret(relative) and not _known_safe_secret_fixture(relative):
                raise DrillRefused("repository contains a production secret path")


def check_forbidden_options(repo: Path) -> None:
    matrix = json.loads((repo / "docs/agent/GATE_MATRIX.yaml").read_text(encoding="utf-8"))
    arguments = [argument for gate in matrix["gates"] for argument in gate["command"]]
    workflow_text = []
    for workflow in (repo / ".github/workflows").glob("*.yml"):
        workflow_text.append(workflow.read_text(encoding="utf-8"))
    haystack = "\n".join((*arguments, *workflow_text))
    if any(option in haystack for option in _FORBIDDEN_OPTIONS):
        raise DrillRefused("governed command contains a forbidden option")


def check_docs(repo: Path, plan: Path, memory: Path) -> None:
    plan_path = repo / plan
    memory_path = repo / memory
    readme = (repo / "tools/agent-harness/README.md").read_text(encoding="utf-8")
    plan_text = plan_path.read_text(encoding="utf-8")
    memory_text = memory_path.read_text(encoding="utf-8")
    required_sections = ("## 核心套件", "## 项目策略模板", "## 宿主集成")
    if any(section not in readme for section in required_sections):
        raise DrillRefused("Harness README does not separate reusable and host-owned layers")
    if plan.as_posix() not in memory_text or "tools/agent-harness/README.md" not in memory_text:
        raise DrillRefused("project memory is not linked to the active plan and Harness guide")
    if "tools/agent-harness/drills/run_drills.py" not in plan_text:
        raise DrillRefused("active plan does not reference the drill runner")


def _primary_identity(repo: Path) -> tuple[str, bytes]:
    return _git(repo, "rev-parse", "HEAD"), _run(
        ["git", "status", "--porcelain=v1", "-z"], repo
    ).stdout.encode()


def real_smoke_branch_name() -> str:
    return "drill/real-codex-smoke-" + uuid4().hex[:12]


def real_smoke_detail(state: RunState, changed: tuple[str, ...], history: Path) -> str:
    from agent_harness.diagnostics import redact_text

    reason = next(
        (
            decision["summary"]
            for decision in reversed(state.decisions)
            if decision["type"] == "transition"
        ),
        "terminal state recorded without a transition summary",
    )
    return json.dumps(
        {
            "status": state.status.value,
            "stopReason": redact_text(reason),
            "changedPaths": changed,
            "passedGates": sum(
                1 for gate in state.gates.values() if gate.status == GateStatus.PASSED
            ),
            "history": history.as_posix(),
            "cleanup": "temporary worktree and branch removed",
        },
        sort_keys=True,
    )


def run_real_codex_smoke(repo: Path) -> DrillResult:
    """Run exactly one Codex cycle in a temporary linked worktree."""
    primary = Path(repo).resolve(strict=True)
    before = _primary_identity(primary)
    temporary = tempfile.TemporaryDirectory()
    worktree = Path(temporary.name).resolve() / "codex-smoke"
    run_id = None
    branch = real_smoke_branch_name()
    branch_created = False
    try:
        _git(primary, "worktree", "add", "--detach", str(worktree), "HEAD")
        _git(worktree, "switch", "-c", branch)
        branch_created = True
        _git(worktree, "config", "user.email", "drill@example.invalid")
        _git(worktree, "config", "user.name", "Harness Drill")
        ensure_no_production_secret_paths(worktree)
        fixture = worktree / "tools/agent-harness/tests/fixtures/real_codex_smoke.txt"
        fixture.parent.mkdir(parents=True, exist_ok=True)
        fixture.write_text("baseline\n", encoding="utf-8")
        plan = worktree / "docs/superpowers/plans/real-codex-smoke.md"
        plan.write_text(
            "# Disposable real Codex smoke\n\n**Status:** approved\n\n"
            "## Milestone smoke: Add expected fixture line\n\n"
            "**Files:**\n- Modify: `tools/agent-harness/tests/fixtures/real_codex_smoke.txt`\n\n"
            "Acceptance: append exactly one line containing `real-codex-smoke-ok`; commit only the fixture "
            "with the supplied Agent-Run-Id trailer.\n",
            encoding="utf-8",
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
        if result.status not in (RunStatus.COMPLETED, RunStatus.PAUSED) or not history.is_file():
            raise AssertionError("real Codex did not produce a terminal history")
        history_text = history.read_text(encoding="utf-8")
        if any(secret in history_text for secret in ("Authorization:", "Bearer ", "fake-secret")):
            raise AssertionError("real Codex history is not sanitized")
        detail = real_smoke_detail(result, changed, history.relative_to(worktree))
        return DrillResult("real-codex-smoke", result.status.value, detail)
    except Exception as error:
        from agent_harness.diagnostics import redact_text

        detail = redact_text(type(error).__name__ + ": " + str(error))
        if run_id:
            history = worktree / "docs/agent/run-history" / (run_id + ".md")
            if history.is_file():
                detail = "sanitized pause evidence: " + history.relative_to(worktree).as_posix()
        return DrillResult("real-codex-smoke", "failed", detail)
    finally:
        if worktree.exists():
            _run(["git", "worktree", "remove", "--force", str(worktree)], primary, check=False)
        if branch_created:
            _run(["git", "branch", "-D", branch], primary, check=False)
        _run(["git", "worktree", "prune"], primary, check=False)
        temporary.cleanup()
        if _primary_identity(primary) != before:
            raise AssertionError("primary worktree changed during real Codex smoke")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run deterministic Harness fault drills")
    parser.add_argument("--allow-real-codex", action="store_true")
    parser.add_argument("--check-forbidden-options", action="store_true")
    parser.add_argument("--check-docs", action="store_true")
    parser.add_argument("--plan", type=Path, default=Path("docs/superpowers/plans/2026-10-10-autonomous-engineering-control-plane.md"))
    parser.add_argument("--memory", type=Path, default=Path("docs/agent/PROJECT_MEMORY.md"))
    parser.add_argument("--fault-worker", choices=("before", "after"), help=argparse.SUPPRESS)
    parser.add_argument("state_path", nargs="?", type=Path, help=argparse.SUPPRESS)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if args.fault_worker:
        if args.state_path is None:
            return 2
        return _fault_worker(args.fault_worker, args.state_path)
    try:
        if args.check_forbidden_options:
            check_forbidden_options(REPO)
            print("PASS forbidden-option-policy")
            return 0
        if args.check_docs:
            check_docs(REPO, args.plan, args.memory)
            print("PASS plan-memory-drift")
            return 0
        if args.allow_real_codex:
            result = run_real_codex_smoke(REPO)
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
