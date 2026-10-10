"""Compose the accepted state, Git, gate and diagnostics contracts."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Sequence
from uuid import UUID, uuid4

from .config import load_gate_matrix, load_protected_paths, validate_gate_id
from .diagnostics import redact_text, render_diagnostics
from .doctor import run_doctor
from .gate_runner import invalidate_stale_evidence, resolve_required_gates, run_gate
from .git_guard import assert_worktree_isolated, capture_snapshot, changed_paths, classify_paths, validate_resume
from .state import (AttemptState, GateStatus, RunState, RunStatus,
                    append_event, load_state, read_events, save_state_atomic,
                    sha256_file, transition)


class ServiceError(ValueError):
    def __init__(self, message: str, exit_code: int = 2):
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class NextAction:
    operation: str
    status: str
    run_id: str


def _now():
    return datetime.now(timezone.utc)


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ServiceError("证据包含重复字段", 3)
        result[key] = value
    return result


class RunService:
    def __init__(self, repo: Path):
        self.repo = Path(repo).resolve(strict=True)

    def _path(self, relative: Path) -> Path:
        relative = Path(relative)
        if relative.is_absolute() or ".." in relative.parts or "\\" in str(relative):
            raise ServiceError("路径必须位于仓库内", 3)
        path = self.repo
        for part in relative.parts:
            path /= part
            if path.is_symlink():
                raise ServiceError("拒绝符号链接路径", 3)
        if not path.resolve().is_relative_to(self.repo):
            raise ServiceError("路径越过仓库边界", 3)
        return path

    def _directory(self, run_id):
        try:
            if str(UUID(run_id)) != run_id:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ServiceError("运行 ID 无效") from None
        return self._path(Path("var/agent-harness/runs") / run_id)

    def _load(self, run_id, *, allow_incomplete=False):
        directory = self._directory(run_id)
        try:
            state = load_state(self._path(directory.relative_to(self.repo) / "state.json"))
            read_events(self._path(directory.relative_to(self.repo) / "events.jsonl"))
        except (OSError, ValueError):
            raise ServiceError("运行状态或事件链不可用") from None
        if state.run_id != run_id or state.worktree_path.resolve() != self.repo:
            raise ServiceError("运行与当前 worktree 不符", 3)
        for identifier in (*state.required_gates, *state.gates):
            validate_gate_id(identifier)
        if state.status == RunStatus.COMPLETED and not allow_incomplete and (self._trusted_history(state) is None or not self._terminal_event_present(state)):
            raise ServiceError("完成历史尚未发布；请重新 finalize 恢复", 5)
        return state

    def status(self, run_id: str) -> RunState:
        return self._load(run_id)

    def select_run(self) -> str:
        root = self._path(Path("var/agent-harness/runs"))
        states = [self._load(p.name, allow_incomplete=True) for p in root.iterdir() if p.is_dir()] if root.exists() else []
        active = [s for s in states if s.status != RunStatus.COMPLETED]
        candidates = active or states
        if len(candidates) != 1:
            raise ServiceError("请用 --run-id 指定唯一运行")
        return candidates[0].run_id

    def _save(self, state, event_type, payload=None):
        directory = self._directory(state.run_id)
        self._assert_raw_storage(state.run_id)
        save_state_atomic(self._path(directory.relative_to(self.repo) / "state.json"), state)
        append_event(self._path(directory.relative_to(self.repo) / "events.jsonl"), event_type,
                     payload or {"status": state.status.value, "headCommit": state.head_commit})
        return state

    def _decision(self, state, kind, summary):
        if not kind.strip() or not summary.strip() or len(kind) > 128 or len(summary) > 65536:
            raise ServiceError("决定类型或摘要无效")
        now = _now()
        return replace(state, updated_at=now, decisions=state.decisions + ({
            "type": kind, "summary": redact_text(summary),
            "createdAt": now.isoformat().replace("+00:00", "Z")},))

    def _configs(self):
        return (load_gate_matrix(self._path(Path("docs/agent/GATE_MATRIX.yaml"))),
                load_protected_paths(self._path(Path("docs/agent/PROTECTED_PATHS.yaml"))))

    def _binding(self):
        return {name: sha256_file(self._path(Path("docs/agent") / name))
                for name in ("GATE_MATRIX.yaml", "PROTECTED_PATHS.yaml")}

    def _assert_raw_storage(self, run_id):
        directory = self._directory(run_id)
        relative = directory.relative_to(self.repo)
        products = ("", "state.json", "events.jsonl", "evidence/", "evidence/probe/stdout.log",
                    "evidence/probe/stderr.log", "evidence/probe/metadata.json",
                    "diagnostics.md", "terminal-history.md", "terminal-state.json", "terminal-intent.json")
        arguments = []
        for name in products:
            path = self._path(relative / name)
            argument = path.relative_to(self.repo).as_posix() + ("/" if not name else "")
            arguments.append(argument)
        result = subprocess.run(["git", "--no-optional-locks", "check-ignore", "--no-index", "-z", "--stdin"],
                                input="\x00".join(arguments) + "\x00", text=True,
                                cwd=self.repo, capture_output=True, check=False)
        if result.returncode or set(result.stdout.rstrip("\x00").split("\x00")) != set(arguments):
            raise ServiceError("实际原始运行目录及产物必须全部被 Git 忽略", 3)
        if self._git("ls-files", "--", relative.as_posix()).strip():
            raise ServiceError("原始运行产物不得被 Git 跟踪", 3)

    def _plan(self, relative, milestone):
        path = self._path(relative)
        try:
            if path.stat().st_size > 1024 * 1024:
                raise ServiceError("计划过大")
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise ServiceError("无法读取计划") from None
        # Approval text inside fenced examples cannot approve a plan.
        text = re.sub(r"(?ms)^\s*(```|~~~).*?^\s*\1[^\n]*$", "", text)
        approved = re.search(r"(?mi)^\s*(?:\*\*)?(?:Status|状态)(?:\*\*)?[:：](?:\*\*)?\s*"
                             r"(?:approved\s*$|已(?:于\s*\d{4}-\d{2}-\d{2}\s*)?获用户书面确认\s*$|已确认\s*$)", text)
        if approved is None:
            raise ServiceError("计划尚未确认", 3)
        headings = list(re.finditer(r"(?m)^(#{1,6})\s+(?:Milestone\s+(\S+?)|Task\s+(\d+))\s*[:：]\s*(.+)$", text))
        matches = [h for h in headings if (h[2] or "task-" + h[3]) == milestone]
        if len(matches) != 1:
            raise ServiceError("Milestone 不存在或不唯一")
        heading = matches[0]
        next_heading = re.search(r"(?m)^#{1," + str(len(heading[1])) + r"}\s", text[heading.end():])
        end = heading.end() + next_heading.start() if next_heading else len(text)
        body = text[heading.end():end]
        if not re.search(r"(?i)Acceptance|Expected:|验收", body):
            raise ServiceError("Milestone 缺少可判定验收标准", 3)
        return heading[4].strip(), sha256_file(path)

    def init(self, plan: Path, milestone: str) -> RunState:
        plan = Path(plan)
        if plan.is_absolute():
            try:
                plan = plan.relative_to(self.repo)
            except ValueError:
                raise ServiceError("计划必须位于当前仓库", 3) from None
        if not plan.is_relative_to(Path("docs/superpowers/plans")):
            raise ServiceError("计划必须来自权威计划目录")
        title, digest = self._plan(plan, milestone)
        try:
            assert_worktree_isolated(self.repo)
            snapshot = capture_snapshot(self.repo)
        except ValueError:
            raise ServiceError("需要有效的独立 Git worktree 与任务分支", 3) from None
        if snapshot.branch in ("main", "master") or snapshot.dirty_paths:
            raise ServiceError("需要干净的独立任务分支", 3)
        run_id = str(uuid4())
        self._assert_raw_storage(run_id)
        matrix, policy = self._configs()
        if any(p.action in ("deny", "approval_required") for p in classify_paths(self.repo, (plan.as_posix(),), policy)):
            raise ServiceError("计划路径需要人工处理", 3)
        definitions = resolve_required_gates(matrix, (plan.as_posix(),))
        now = _now()
        state = RunState(1, run_id, plan, digest, self.repo, snapshot.branch,
                         snapshot.head_commit, snapshot.head_commit, milestone, title,
                         RunStatus.PLANNED, AttemptState(0, {}), None,
                         tuple(g.id for g in definitions), {}, (), (), now, now)
        state = self._decision(state, "policy_binding", json.dumps(self._binding(), sort_keys=True))
        directory = self._directory(state.run_id)
        directory.mkdir(parents=True)
        return self._save(state, "initialized")

    def next_action(self, run_id: str) -> NextAction:
        state = self._load(run_id)
        if state.status in (RunStatus.PLANNED, RunStatus.ACTIVE, RunStatus.REPAIRING, RunStatus.VERIFYING):
            state, _ = self._sync(state)
            self._readonly_paths(state)
        operation = {RunStatus.PLANNED: "develop", RunStatus.ACTIVE: "develop",
                     RunStatus.REPAIRING: "repair", RunStatus.COMPLETED: "done",
                     RunStatus.PAUSED: "resume", RunStatus.BLOCKED: "human_review"}.get(state.status)
        if operation is None:
            operation = "gate"
            try:
                synced = state
                matrix, _ = self._configs()
                paths = tuple(p for p in changed_paths(self.repo, state.base_commit) if p != self._trusted_history(state))
                definitions = resolve_required_gates(matrix, (state.plan_path.as_posix(),
                    *paths), additional_gate_ids=state.required_gates)
                self._verify_snapshot(synced)
                for definition in definitions:
                    self._verify_evidence(synced, definition)
                self._verify_review(synced, self._readonly_paths(synced))
                operation = "finalize"
            except ServiceError as error:
                if error.exit_code == 3:
                    operation = "record_review"
                elif error.exit_code != 4:
                    raise
            except (OSError, ValueError):
                pass
        return NextAction(operation, state.status.value, run_id)

    def record_decision(self, run_id: str, decision_type: str, summary: str) -> RunState:
        state = self._load(run_id)
        self._operable(state)
        if decision_type in ("policy_binding", "history_artifact", "verification_snapshot", "transition", "evidence_invalidated", "resumed", "review_evidence", "authorized_paths", "observed_paths"):
            raise ServiceError("此决定类型由 Harness 管理")
        return self._save(self._decision(state, decision_type, summary), "decision_recorded")

    def _authorized_paths(self, state):
        records = [d for d in state.decisions if d["type"] == "authorized_paths"]
        paths = tuple(json.loads(records[-1]["summary"])) if records else ()
        _, policy = self._configs()
        # History is never ordinary code authorization, including aliases.
        classified = classify_paths(self.repo, paths, policy)
        return tuple(Path(p) for p, decision in zip(paths, classified)
                     if not Path(decision.path).is_relative_to(Path("docs/agent/run-history")))

    def _readonly_paths(self, state):
        _, policy = self._configs()
        paths = tuple(p for p in changed_paths(self.repo, state.base_commit) if p != self._trusted_history(state))
        decisions = classify_paths(self.repo, paths, policy)
        if any(p.action in ("deny", "approval_required", "generated") for p in decisions):
            raise ServiceError("路径策略拒绝自动执行", 5)
        return decisions

    def _review_scope(self, decisions):
        return hashlib.sha256(json.dumps(sorted({p.path for p in decisions}),
                                         separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()

    def record_review(self, run_id, reviewer, report):
        state = self._load(run_id)
        self._operable(state)
        state, _ = self._sync(state)
        decisions = self._readonly_paths(state)
        if (not isinstance(reviewer, str) or len(reviewer) > 128
                or re.fullmatch(r"[a-zA-Z][a-zA-Z0-9-]*", reviewer) is None
                or redact_text(reviewer) != reviewer):
            raise ServiceError("审查人标识无效", 3)
        source = self._path(Path(report))
        directory = self._directory(run_id)
        if not source.is_relative_to(directory) or source.stat().st_size > 65536:
            raise ServiceError("审查报告必须位于本运行的忽略目录", 3)
        self._assert_raw_storage(run_id)
        text = source.read_text(encoding="utf-8")
        document = json.loads(text, object_pairs_hook=_unique_fields)
        expected = {"version": 1, "verdict": "approved", "reviewer": reviewer,
                    "headCommit": state.head_commit, "changedPathsSha256": self._review_scope(decisions)}
        if document != expected or type(document.get("version")) is not int or redact_text(text) != text:
            raise ServiceError("审查报告未批准当前 HEAD 与规范改动范围", 3)
        digest = hashlib.sha256(text.encode()).hexdigest()
        target = directory / ("review-" + digest + ".json")
        self._atomic_text(target, text)
        binding = {**expected, "reviewerSha256": hashlib.sha256(reviewer.encode()).hexdigest(),
                   "reportSha256": digest, "reportPath": target.relative_to(self.repo).as_posix()}
        if self._git("rev-parse", "HEAD").strip() != state.head_commit or self._review_scope(self._readonly_paths(state)) != expected["changedPathsSha256"]:
            raise ServiceError("审查录入期间 Git 发生漂移", 5)
        return self._save(self._decision(state, "review_evidence", json.dumps(binding, sort_keys=True)), "review_recorded")

    def _verify_review(self, state, decisions):
        if not any(p.action == "review_required" for p in decisions):
            return
        records = [d for d in state.decisions if d["type"] == "review_evidence"]
        try:
            binding = json.loads(records[-1]["summary"], object_pairs_hook=_unique_fields)
            path = self._path(Path(binding["reportPath"]))
            if not path.is_relative_to(self._directory(state.run_id)) or path.stat().st_size > 65536:
                raise ValueError()
            report = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_fields)
            expected = {"version": 1, "verdict": "approved", "reviewer": binding["reviewer"],
                        "headCommit": state.head_commit, "changedPathsSha256": self._review_scope(decisions)}
            if (report != expected or any(binding[k] != v for k, v in expected.items())
                    or binding["reviewerSha256"] != hashlib.sha256(binding["reviewer"].encode()).hexdigest()
                    or binding["reportSha256"] != sha256_file(path)):
                raise ValueError()
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            raise ServiceError("缺少当前 HEAD 与范围绑定的有效独立审查证据", 3) from None

    def _operable(self, state):
        if state.status in (RunStatus.PAUSED, RunStatus.BLOCKED):
            raise ServiceError("运行已暂停或阻塞", 5)
        if state.status == RunStatus.COMPLETED:
            raise ServiceError("运行已完成", 3)

    def _check_binding(self, state):
        _, digest = self._plan(state.plan_path, state.milestone_id)
        if digest != state.plan_sha256:
            raise ServiceError("计划摘要发生漂移", 5)
        bindings = [d for d in state.decisions if d["type"] == "policy_binding"]
        if len(bindings) != 1 or json.loads(bindings[0]["summary"]) != self._binding():
            raise ServiceError("策略配置发生漂移", 5)

    def _history_relative(self, state):
        return Path("docs/agent/run-history") / (state.run_id + ".md")

    def _trusted_history(self, state):
        records = [d for d in state.decisions if d["type"] == "history_artifact"]
        if not records:
            return None
        relative = self._history_relative(state)
        path = self._path(relative)
        if path.is_file() and sha256_file(path) == records[-1]["summary"]:
            return relative.as_posix()
        return None

    def _snapshot(self, state):
        snapshot = capture_snapshot(self.repo)
        trusted = self._trusted_history(state)
        filtered = replace(snapshot, dirty_paths=tuple(p for p in snapshot.dirty_paths if p != trusted),
                           untracked_paths=tuple(p for p in snapshot.untracked_paths if p != trusted))
        if snapshot.branch != state.branch or snapshot.repo != state.worktree_path:
            raise ServiceError("Git 身份发生漂移", 5)
        assert_worktree_isolated(self.repo)
        return snapshot, filtered

    def _sync(self, state, allow_dirty=False):
        self._check_binding(state)
        snapshot, filtered = self._snapshot(state)
        self._check_ancestry(state)
        if not allow_dirty and filtered.dirty_paths:
            raise ServiceError("出现无法归属的工作区改动", 5)
        if snapshot.head_commit != state.head_commit:
            if not self._resume_git_allowed(snapshot, filtered, state):
                raise ServiceError("Git HEAD 漂移无法归属", 5)
            state = invalidate_stale_evidence(state, snapshot.head_commit)
        return state, filtered

    def _git(self, *args):
        result = subprocess.run(["git", "--no-optional-locks", "--no-replace-objects",
                                 "-c", "core.fsmonitor=false", *args], cwd=self.repo,
                                text=True, capture_output=True, check=False)
        if result.returncode:
            raise ServiceError("无法验证 Git 提交归属", 5)
        return result.stdout

    def _check_ancestry(self, state):
        if self._git("rev-parse", "--is-shallow-repository").strip() != "false":
            raise ServiceError("浅克隆无法验证提交归属", 5)
        self._git("merge-base", "--is-ancestor", state.base_commit, state.head_commit)

    def _resume_git_allowed(self, raw, filtered, state):
        scoped = replace(state, changed_paths=self._authorized_paths(state))
        decision = validate_resume(raw, scoped)
        if decision.allowed:
            return True
        if (decision.reason not in ("dirty_drift", "unexplained_head") or filtered.dirty_paths
                or self._trusted_history(state) is None):
            return False
        # Preserve ancestry/trailer/scope checks while admitting only this
        # run's digest-verified history, whether dirty or checkpointed.
        self._check_ancestry(state)
        if raw.head_commit != state.head_commit:
            self._git("merge-base", "--is-ancestor", state.head_commit, raw.head_commit)
            previous = state.head_commit
            history = self._trusted_history(state)
            permitted = {p.as_posix() for p in scoped.changed_paths} | {history}
            history_digests = {d["summary"] for d in state.decisions if d["type"] == "history_artifact"}
            for row in self._git("rev-list", "--reverse", "--parents",
                                 state.head_commit + ".." + raw.head_commit).splitlines():
                parts = row.split()
                if len(parts) != 2 or parts[1] != previous:
                    return False
                commit = parts[0]
                trailer = self._git("show", "-s", "--format=%(trailers:key=Agent-Run-Id,only,unfold,separator=%x00)", commit, "--").removesuffix("\n")
                key, separator, value = trailer.partition(":")
                if not separator or key.lower() != "agent-run-id" or value.strip() != state.run_id:
                    return False
                touched = set(changed_paths(self.repo, previous, commit))
                if not touched <= permitted:
                    return False
                if history in touched:
                    # Authenticate the bytes in each checkpoint, not just HEAD.
                    content = subprocess.run(["git", "--no-replace-objects", "show", commit + ":" + history],
                                             cwd=self.repo, capture_output=True, check=False)
                    if content.returncode or hashlib.sha256(content.stdout).hexdigest() not in history_digests:
                        return False
                previous = commit
            if previous != raw.head_commit:
                return False
        return capture_snapshot(self.repo) == raw

    def _stop(self, state, reason, code):
        if state.status not in (RunStatus.COMPLETED, RunStatus.PAUSED, RunStatus.BLOCKED):
            self._terminal(state, transition(state, RunStatus.PAUSED, reason))
        raise ServiceError(reason, code)

    def _atomic_text(self, path, text):
        path = self._path(path.relative_to(self.repo))
        if path.is_relative_to(self.repo / "var/agent-harness/runs"):
            self._assert_raw_storage(path.relative_to(self.repo / "var/agent-harness/runs").parts[0])
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=".history.", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def _terminal(self, previous, terminal):
        directory = self._directory(terminal.run_id)
        self._assert_raw_storage(terminal.run_id)
        preview = self._path(directory.relative_to(self.repo) / "diagnostics.md")
        render_diagnostics(terminal, terminal.gates.values(), preview)
        report = preview.read_text(encoding="utf-8")
        if terminal.status == RunStatus.COMPLETED:
            report = report.split("\nNext:", 1)[0] + "\n\nNext: completed; no automatic Milestone advancement.\n"
        report += f"\nBase commit: {terminal.base_commit}\n\nGate evidence:\n"
        for identifier in terminal.required_gates:
            gate = terminal.gates.get(identifier)
            report += f"- {identifier}: {gate.status.value if gate else 'pending'}; exit={gate.exit_code if gate else None}; evidence={gate.evidence_path if gate else None}\n"
            if gate is not None:
                report += f"  startedAt={gate.started_at.isoformat().replace('+00:00', 'Z') if gate.started_at else None}; endedAt={gate.ended_at.isoformat().replace('+00:00', 'Z') if gate.ended_at else None}\n"
                if gate.evidence_path is not None:
                    try:
                        metadata_path = self._path(gate.evidence_path)
                        if not metadata_path.is_relative_to(directory / "evidence") or metadata_path.stat().st_size > 65536:
                            raise ValueError()
                        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                        report += "  command=" + json.dumps(metadata["command"], ensure_ascii=False) + "; cwd=" + metadata["cwd"] + "\n"
                    except (OSError, ValueError, KeyError, TypeError):
                        report += "  command metadata unavailable\n"
        report = redact_text(report)
        terminal = self._decision(terminal, "history_artifact", hashlib.sha256(report.encode()).hexdigest())
        history = self._path(self._history_relative(terminal))
        pending = self._path(directory.relative_to(self.repo) / "terminal-history.md")
        self._atomic_text(pending, report)
        state_path = self._path(directory.relative_to(self.repo) / "state.json")
        candidate_path = self._path(directory.relative_to(self.repo) / "terminal-state.json")
        save_state_atomic(candidate_path, terminal)
        intent = {"previousStateSha256": sha256_file(state_path),
                  "terminalStateSha256": sha256_file(candidate_path),
                  "historySha256": terminal.decisions[-1]["summary"],
                  "status": terminal.status.value}
        self._atomic_text(directory / "terminal-intent.json", json.dumps(intent, sort_keys=True))
        # Publish completion only after its atomic state has passed validation.
        save_state_atomic(self._path(directory.relative_to(self.repo) / "state.json"), terminal)
        try:
            self._atomic_text(history, report)
        except (OSError, ValueError):
            save_state_atomic(self._path(directory.relative_to(self.repo) / "state.json"), previous)
            raise
        append_event(self._path(directory.relative_to(self.repo) / "events.jsonl"), terminal.status.value,
                     {"headCommit": terminal.head_commit, "historySha256": terminal.decisions[-1]["summary"],
                      "status": terminal.status.value})
        return history

    def _terminal_event_present(self, state):
        records = [d["summary"] for d in state.decisions if d["type"] == "history_artifact"]
        if not records:
            return False
        events = read_events(self._directory(state.run_id) / "events.jsonl")
        return any(e["type"] in (state.status.value, "reconciled")
                   and e["payload"].get("status") == state.status.value
                   and e["payload"].get("headCommit") == state.head_commit
                   and e["payload"].get("historySha256") == records[-1] for e in events)

    def _recover_pending_terminal(self, state, status):
        directory = self._directory(state.run_id)
        intent_path = self._path(directory.relative_to(self.repo) / "terminal-intent.json")
        if not intent_path.exists() or state.status in (RunStatus.PAUSED, RunStatus.COMPLETED):
            return state
        try:
            intent = json.loads(intent_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_fields)
            current = sha256_file(directory / "state.json")
            if current != intent["previousStateSha256"]:
                return state
            candidate_path = self._path(directory.relative_to(self.repo) / "terminal-state.json")
            if sha256_file(candidate_path) != intent["terminalStateSha256"]:
                raise ValueError()
            candidate = load_state(candidate_path)
            if (candidate.run_id != state.run_id or candidate.worktree_path != self.repo
                    or candidate.status != status or intent["status"] != status.value
                    or candidate.decisions[-1]["summary"] != intent["historySha256"]):
                raise ValueError()
            if status == RunStatus.COMPLETED:
                verified, _ = self._sync(state)
                if verified.head_commit != candidate.head_commit:
                    raise ValueError()
                self._verify_snapshot(verified)
                _, definitions = self._paths_and_gates(verified, ())
                for definition in definitions:
                    self._verify_evidence(verified, definition)
                self._verify_review(verified, self._readonly_paths(verified))
            self._assert_raw_storage(state.run_id)
            save_state_atomic(directory / "state.json", candidate)
            return candidate
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            raise ServiceError("终态恢复意图缺失、过期或不匹配", 5) from None

    def _recover_history(self, state):
        history = self._path(self._history_relative(state))
        records = [d["summary"] for d in state.decisions if d["type"] == "history_artifact"]
        if not records:
            raise ServiceError("没有可恢复的历史证据", 5)
        pending = self._path(self._directory(state.run_id).relative_to(self.repo) / "terminal-history.md")
        if not pending.is_file() or sha256_file(pending) != records[-1]:
            raise ServiceError("待发布历史缺失或摘要不匹配", 5)
        if history.exists() and sha256_file(history) not in records:
            raise ServiceError("历史文件出现外来改动", 5)
        if not self._trusted_history(state):
            self._atomic_text(history, pending.read_text(encoding="utf-8"))
        if not self._terminal_event_present(state):
            self._assert_raw_storage(state.run_id)
            append_event(self._path(self._directory(state.run_id).relative_to(self.repo) / "events.jsonl"),
                         "reconciled", {"historySha256": records[-1], "headCommit": state.head_commit,
                                        "status": state.status.value})
        return history

    def pause(self, run_id: str, reason: str) -> Path:
        state = self._load(run_id, allow_incomplete=True)
        state = self._recover_pending_terminal(state, RunStatus.PAUSED)
        if state.status == RunStatus.PAUSED:
            return self._recover_history(state)
        self._operable(state)
        try:
            synced, _ = self._sync(state, allow_dirty=True)
        except (ValueError, OSError):
            synced = state
        paths = tuple(p for p in changed_paths(self.repo, state.base_commit) if p != self._trusted_history(state))
        known = {p.as_posix() for p in self._authorized_paths(state)}
        synced = self._decision(replace(synced, changed_paths=tuple(Path(p) for p in paths)), "observed_paths",
                                json.dumps({"known": sorted(set(paths) & known), "unknown": sorted(set(paths) - known)}))
        return self._terminal(state, transition(synced, RunStatus.PAUSED, redact_text(reason)))

    def resume(self, run_id: str) -> RunState:
        state = self._load(run_id)
        if state.status != RunStatus.PAUSED:
            raise ServiceError("仅暂停运行可以恢复", 5)
        if run_doctor(self.repo).error:
            raise ServiceError("doctor 检查未通过，运行保持暂停", 5)
        try:
            self._recover_history(state)
            synced, filtered = self._sync(state)
            snapshot = capture_snapshot(self.repo)
            if not self._resume_git_allowed(snapshot, filtered, state):
                raise ServiceError("Git 恢复检查未通过", 5)
        except (OSError, ValueError):
            raise ServiceError("计划、策略或 Git 漂移，运行保持暂停", 5) from None
        # The shared state machine keeps terminal transitions closed; resume
        # is an explicit service operation after environment and identity checks.
        resumed = self._decision(replace(synced, status=RunStatus.ACTIVE), "resumed", "doctor and Git verified")
        if synced.head_commit != state.head_commit:
            resumed = self._save(resumed, "reconciled", {"previousHead": state.head_commit,
                                 "headCommit": synced.head_commit, "gatesInvalidated": True})
        return self._save(resumed, "resumed")

    def _paths_and_gates(self, state, extra):
        matrix, policy = self._configs()
        paths = changed_paths(self.repo, state.base_commit)
        trusted = self._trusted_history(state)
        paths = tuple(p for p in paths if p != trusted)
        decisions = classify_paths(self.repo, paths, policy)
        if any(p.action in ("deny", "approval_required", "generated") for p in decisions):
            self._stop(replace(state, changed_paths=tuple(Path(p) for p in paths)),
                       "改动触及受保护路径，需要人工处理", 3)
        definitions = resolve_required_gates(matrix, (state.plan_path.as_posix(), *paths),
                                              additional_gate_ids=(*state.required_gates, *extra))
        return paths, definitions

    def _workspace_stamp(self, state):
        raw, snapshot = self._snapshot(state)
        trusted = self._trusted_history(state)
        pathspec = ["."] + ([":(top,literal,exclude)" + trusted] if trusted else [])
        result = subprocess.run(["git", "--no-optional-locks", "--no-replace-objects",
                                 "diff", "--no-ext-diff", "--no-textconv", "--binary", "HEAD", "--", *pathspec],
                                cwd=self.repo, capture_output=True, check=False)
        if result.returncode:
            raise ServiceError("无法检查工作区内容", 5)
        digest = hashlib.sha256(result.stdout)
        for path in snapshot.untracked_paths:
            digest.update(path.encode("utf-8", errors="surrogateescape"))
            digest.update(sha256_file(self._path(Path(path))).encode())
        return {"head": raw.head_commit, "clean": not snapshot.dirty_paths,
                "digest": digest.hexdigest()}

    def _verify_snapshot(self, state):
        records = [d for d in state.decisions if d["type"] == "verification_snapshot"]
        if not records or json.loads(records[-1]["summary"]) != self._workspace_stamp(state):
            raise ServiceError("质量门对应的代码快照已变化，请重新运行", 4)
        if not json.loads(records[-1]["summary"])["clean"]:
            raise ServiceError("完成前须在当前 HEAD 的干净工作区重跑质量门", 4)

    def run_gates(self, run_id: str, extra_gate_ids: Sequence[str] = ()) -> RunState:
        state = self._load(run_id)
        self._operable(state)
        try:
            synced, _ = self._sync(state, allow_dirty=True)
        except (ValueError, OSError):
            self._stop(state, "计划、策略或 Git 漂移", 5)
        paths, definitions = self._paths_and_gates(synced, extra_gate_ids)
        state = replace(synced, changed_paths=tuple(Path(p) for p in paths),
                        required_gates=tuple(g.id for g in definitions))
        state = self._decision(state, "authorized_paths", json.dumps(list(paths)))
        if state.status == RunStatus.PLANNED:
            state = transition(state, RunStatus.ACTIVE, "gate requested")
        if state.status != RunStatus.VERIFYING:
            state = transition(state, RunStatus.VERIFYING, "verification started")
        state = self._save(state, "verification_started")
        initial = capture_snapshot(self.repo)
        stamp = self._workspace_stamp(state)
        for definition in definitions:
            self._assert_raw_storage(run_id)
            evidence = run_gate(self.repo, definition, self._directory(run_id) / "evidence", state.head_commit)
            state = self._save(replace(state, gates={**state.gates, definition.id: evidence}, updated_at=_now()), "gate_finished", {"gateId": definition.id, "status": evidence.status.value})
        if capture_snapshot(self.repo) != initial or self._workspace_stamp(state) != stamp:
            self._stop(state, "Gate 执行期间 Git 状态发生变化", 5)
        state = self._save(self._decision(state, "verification_snapshot", json.dumps(stamp, sort_keys=True)), "verification_finished")
        if any(state.gates[g.id].status != GateStatus.PASSED for g in definitions):
            self._save(transition(state, RunStatus.REPAIRING, "gate failure"), "gate_failed")
            raise ServiceError("质量门未通过", 4)
        return state

    def _verify_evidence(self, state, definition):
        gate = state.gates.get(definition.id)
        if (gate is None or gate.status != GateStatus.PASSED or gate.exit_code != 0
                or gate.head_commit != state.head_commit or gate.evidence_path is None):
            raise ServiceError("必需 Gate 缺少当前 HEAD 的成功证据", 4)
        path = self._path(gate.evidence_path)
        if not path.is_relative_to(self._directory(state.run_id) / "evidence"):
            raise ServiceError("Gate 证据不属于当前运行", 4)
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
            for name in ("stdout.log", "stderr.log"):
                if not self._path(path.parent.relative_to(self.repo) / name).is_file():
                    raise ValueError()
            if (metadata["gateId"] != definition.id or metadata["status"] != "passed"
                    or metadata["exitCode"] != 0 or metadata["headCommit"] != state.head_commit
                    or metadata["command"] != list(definition.command) or metadata["cwd"] != definition.cwd
                    or metadata["timeoutSeconds"] != definition.timeout_seconds):
                raise ValueError()
        except (OSError, ValueError, KeyError, TypeError):
            raise ServiceError("Gate 证据缺失或不匹配", 4) from None

    def finalize(self, run_id: str) -> Path:
        state = self._load(run_id, allow_incomplete=True)
        state = self._recover_pending_terminal(state, RunStatus.COMPLETED)
        if state.status == RunStatus.COMPLETED:
            self._recover_history(state)
        if state.status not in (RunStatus.VERIFYING, RunStatus.COMPLETED):
            self._operable(state)
            raise ServiceError("须先运行质量门", 4)
        try:
            synced, _ = self._sync(state)
        except (ValueError, OSError):
            self._stop(state, "计划、策略或 Git 漂移", 5)
        paths, definitions = self._paths_and_gates(synced, ())
        initial = self._workspace_stamp(synced)
        self._verify_snapshot(synced)
        for definition in definitions:
            self._verify_evidence(synced, definition)
        self._verify_review(synced, self._readonly_paths(synced))
        if self._workspace_stamp(synced) != initial:
            self._stop(state, "收尾期间 Git 状态发生变化", 5)
        state = replace(synced, changed_paths=tuple(Path(p) for p in paths),
                        required_gates=tuple(g.id for g in definitions))
        if state.status == RunStatus.COMPLETED:
            return self._path(self._history_relative(state))
        completed = transition(state, RunStatus.COMPLETED, "current HEAD gates passed") if state.status != RunStatus.COMPLETED else state
        return self._terminal(state, completed)
