"""Exercise gate selection, real subprocesses and HEAD-bound evidence."""

from dataclasses import replace
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

HARNESS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS))

from agent_harness.config import GateDefinition, GateMatrix, load_gate_matrix  # noqa: E402
from agent_harness.gate_runner import (  # noqa: E402
    invalidate_stale_evidence, resolve_required_gates, run_gate,
)
from agent_harness.state import (  # noqa: E402
    GateStatus, RunStatus, load_state, save_state_atomic, transition,
)


class GateResolutionTests(unittest.TestCase):
    def setUp(self):
        self.matrix = GateMatrix(1, tuple(
            GateDefinition(name, ("check",), ".", 10)
            for name in ("docs", "shared", "frontend", "backend", "extra")
        ), {"ui": ("frontend", "shared"), "api": ("shared", "backend"),
            "docs": ("docs",)},
            {"ui": ("ui/**",), "api": ("api/**",), "docs": ("docs/**",)})

    def ids(self, paths, **kwargs):
        return tuple(gate.id for gate in resolve_required_gates(self.matrix, paths, **kwargs))

    def test_one_path_selects_its_profile_in_matrix_order(self):
        self.assertEqual(self.ids(["ui/src/index.ts"]), ("shared", "frontend"))

    def test_multiple_areas_form_stable_deduplicated_union(self):
        paths = ["api/main.py", "ui/index.ts", "docs/guide.md", "api/main.py"]
        expected = ("docs", "shared", "frontend", "backend")
        self.assertEqual(self.ids(iter(paths)), expected)
        self.assertEqual(self.ids(reversed(paths)), expected)

    def test_overlapping_profiles_all_contribute_gates(self):
        matrix = replace(self.matrix, paths={"ui": ("ui/**",), "api": ("ui/src/**",),
                                            "docs": ("docs/**",)})
        self.assertEqual(tuple(gate.id for gate in resolve_required_gates(
            matrix, ["ui/src/index.ts"])), ("shared", "frontend", "backend"))

    def test_unknown_paths_fail_closed_even_alongside_known_paths(self):
        for path in ("new-service/main.py", "README.md", "UI/index.ts", "docs-extra/a.md"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.ids(["ui/index.ts", path])

    def test_unsafe_or_ambiguous_paths_fail_closed(self):
        for path in ("", ".", "/ui/a.ts", "../ui/a.ts", "docs/../ui/a.ts",
                     "C:/ui/a.ts", "ui\\a.ts", "ui/\x00.ts"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.ids([path])

    def test_documentation_only_selects_documentation_checks(self):
        self.assertEqual(self.ids(["docs/guide.md"]), ("docs",))

    def test_empty_changes_require_no_inferred_gates(self):
        self.assertEqual(self.ids([]), ())

    def test_explicit_gates_can_only_add_to_inferred_gates(self):
        self.assertEqual(self.ids(["ui/index.ts"], additional_gate_ids=("extra", "extra")),
                         ("shared", "frontend", "extra"))
        self.assertEqual(self.ids(["ui/index.ts"], additional_gate_ids=()),
                         ("shared", "frontend"))
        self.assertEqual(self.ids([], additional_gate_ids=("backend",)), ("backend",))

    def test_unknown_additional_gate_id_fails_closed(self):
        with self.assertRaises(ValueError):
            self.ids(["ui/index.ts"], additional_gate_ids=("invented",))

    def test_loaded_repository_matrix_combines_real_profiles(self):
        matrix = load_gate_matrix(HARNESS.parents[1] / "docs/agent/GATE_MATRIX.yaml")
        gates = resolve_required_gates(matrix, [
            "platform/apps/admin-web/src/App.tsx", "tools/agent-harness/agent_harness/state.py",
        ])
        self.assertEqual(tuple(gate.id for gate in gates), (
            "admin-lint", "admin-typecheck", "admin-test", "admin-build",
            "admin-production-bundle", "admin-e2e", "p1-e2e", "access-policy-mysql",
            "access-policy-pgsql", "scoring-parity-mysql", "scoring-parity-pgsql",
            "diff-check", "harness-tests",
        ))


class GateExecutionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name).resolve()
        (self.repo / "app").mkdir()
        self.evidence_dir = self.repo / "var/agent-harness/runs/test/evidence"
        self.head = "a" * 40

    def gate(self, script, *, cwd=".", timeout=5, arguments=()):
        return GateDefinition("unit", (sys.executable, "-c", script, *arguments), cwd, timeout)

    def execute(self, gate):
        return run_gate(self.repo, gate, self.evidence_dir, self.head)

    def artifacts(self, evidence):
        metadata_path = self.repo / evidence.evidence_path
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        return metadata, metadata_path.parent

    def test_success_records_metadata_separately_from_stdout_and_stderr(self):
        gate = self.gate("import sys; print('OUT_MARKER'); print('ERR_MARKER', file=sys.stderr)")
        evidence = self.execute(gate)
        self.assertEqual(evidence.status, GateStatus.PASSED)
        self.assertEqual(evidence.exit_code, 0)
        self.assertEqual(evidence.head_commit, self.head)
        self.assertIsNotNone(evidence.started_at.utcoffset())
        self.assertGreaterEqual(evidence.ended_at, evidence.started_at)
        metadata, directory = self.artifacts(evidence)
        self.assertEqual(metadata["gateId"], "unit")
        self.assertEqual(metadata["status"], "passed")
        self.assertEqual(metadata["headCommit"], self.head)
        self.assertEqual(metadata["exitCode"], 0)
        self.assertEqual(metadata["command"], list(gate.command))
        self.assertEqual(metadata["cwd"], ".")
        self.assertEqual(metadata["timeoutSeconds"], 5)
        self.assertEqual(metadata["startedAt"], evidence.started_at.isoformat().replace("+00:00", "Z"))
        self.assertEqual(metadata["endedAt"], evidence.ended_at.isoformat().replace("+00:00", "Z"))
        self.assertEqual((directory / "stdout.log").read_text(), "OUT_MARKER\n")
        self.assertEqual((directory / "stderr.log").read_text(), "ERR_MARKER\n")
        self.assertNotIn("OUT_MARKER", json.dumps({key: value for key, value in metadata.items()
                                                  if key != "command"}))
        self.assertNotIn("ERR_MARKER", json.dumps({key: value for key, value in metadata.items()
                                                  if key != "command"}))
        self.assertEqual(metadata["stdoutPath"], "stdout.log")
        self.assertEqual(metadata["stderrPath"], "stderr.log")

    def test_nonzero_exit_is_failed_even_if_output_claims_success(self):
        evidence = self.execute(self.gate("import sys; print('all tests passed'); sys.exit(7)"))
        self.assertEqual(evidence.status, GateStatus.FAILED)
        self.assertEqual(evidence.exit_code, 7)
        metadata, directory = self.artifacts(evidence)
        self.assertEqual(metadata["status"], "failed")
        self.assertEqual((directory / "stdout.log").read_text(), "all tests passed\n")

    def test_timeout_keeps_partial_text_output_and_has_no_passing_exit(self):
        evidence = self.execute(self.gate(
            "import sys,time; print('partial', flush=True); "
            "print('partial-error', file=sys.stderr, flush=True); time.sleep(30)", timeout=1))
        self.assertEqual(evidence.status, GateStatus.TIMED_OUT)
        self.assertIsNone(evidence.exit_code)
        metadata, directory = self.artifacts(evidence)
        self.assertEqual(metadata["failureKind"], "timeout")
        self.assertEqual((directory / "stdout.log").read_text(), "partial\n")
        self.assertEqual((directory / "stderr.log").read_text(), "partial-error\n")

    def wait_for(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail("Real process fixture did not become ready")
            time.sleep(0.01)

    def cleanup_process(self, process):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)

    def child_is_running(self, pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        # Linux may retain an orphan zombie until init reaps it; it cannot write.
        try:
            status = Path("/proc/{}/stat".format(pid)).read_text()
        except FileNotFoundError:
            return True
        return status.rsplit(") ", 1)[1].split()[0] != "Z"

    def descendant_gate(self, exit_code, *, timeout=False):
        child = (
            "import os,sys,time; from pathlib import Path; "
            "Path('child.pid.tmp').write_text(str(os.getpid())); "
            "Path('child.pid.tmp').replace('child.pid'); "
            "\nwhile not Path('release').exists(): time.sleep(0.01)"
            "\nprint('late-output', flush=True)"
            "\nprint('late-error', file=sys.stderr, flush=True)"
            "\ntime.sleep(30)"
        )
        parent = (
            "import subprocess,sys,time; from pathlib import Path; "
            "subprocess.Popen([sys.executable, '-c', " + repr(child) + "]); "
            "\nwhile not Path('child.pid').exists(): time.sleep(0.01)"
            "\n" + ("time.sleep(30)" if timeout else "sys.exit(" + str(exit_code) + ")")
        )
        return self.gate(parent, timeout=1 if timeout else 5)

    def assert_descendant_stopped(self, evidence):
        _, directory = self.artifacts(evidence)
        stdout = (directory / "stdout.log").read_bytes()
        stderr = (directory / "stderr.log").read_bytes()
        pid = int((self.repo / "child.pid").read_text())
        (self.repo / "release").touch()
        deadline = time.monotonic() + 1
        while self.child_is_running(pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual((directory / "stdout.log").read_bytes(), stdout)
        self.assertEqual((directory / "stderr.log").read_bytes(), stderr)
        self.assertFalse(self.child_is_running(pid), "Ordinary descendant still running")

    def execute_tracked(self, gate):
        real_spawn = subprocess.Popen

        def spawn(*args, **kwargs):
            process = real_spawn(*args, **kwargs)
            self.addCleanup(self.cleanup_process, process)
            return process

        with patch("agent_harness.gate_runner.subprocess.Popen", side_effect=spawn):
            return self.execute(gate)

    @unittest.skipUnless(os.name == "posix", "Owned POSIX process-group regression")
    def test_normal_exit_stops_ordinary_descendant_before_sealing_logs(self):
        evidence = self.execute_tracked(self.descendant_gate(0))
        self.assertEqual(evidence.status, GateStatus.PASSED)
        self.assert_descendant_stopped(evidence)

    @unittest.skipUnless(os.name == "posix", "Owned POSIX process-group regression")
    def test_nonzero_exit_stops_ordinary_descendant_before_sealing_logs(self):
        evidence = self.execute_tracked(self.descendant_gate(7))
        self.assertEqual(evidence.status, GateStatus.FAILED)
        self.assertEqual(evidence.exit_code, 7)
        self.assert_descendant_stopped(evidence)

    @unittest.skipUnless(os.name == "posix", "Owned POSIX process-group regression")
    def test_timeout_stops_ordinary_descendant_before_sealing_logs(self):
        evidence = self.execute_tracked(self.descendant_gate(0, timeout=True))
        self.assertEqual(evidence.status, GateStatus.TIMED_OUT)
        self.assert_descendant_stopped(evidence)

    @unittest.skipUnless(os.name == "posix", "Owned POSIX process-group regression")
    def test_keyboard_interrupt_reaps_real_process_before_closing_logs(self):
        ready = self.repo / "ready"
        real_spawn, real_killpg = subprocess.Popen, os.killpg
        processes, log_handles = [], []

        def spawn(*args, **kwargs):
            process = real_spawn(*args, **kwargs)
            original_wait = process.wait
            interrupted = False

            def wait(timeout=None):
                nonlocal interrupted
                if not interrupted:
                    interrupted = True
                    self.wait_for(ready.exists)
                    raise KeyboardInterrupt("Injected first wait interruption")
                return original_wait(timeout=timeout)

            process.wait = wait
            self.addCleanup(self.cleanup_process, process)
            processes.append(process)
            log_handles.extend((kwargs["stdout"], kwargs["stderr"]))
            return process

        def killpg(pid, sig):
            self.assertTrue(all(not stream.closed for stream in log_handles))
            return real_killpg(pid, sig)

        with patch("agent_harness.gate_runner.subprocess.Popen", side_effect=spawn), \
                patch("agent_harness.gate_runner.os.killpg", side_effect=killpg):
            with self.assertRaises(KeyboardInterrupt):
                self.execute(self.gate("from pathlib import Path; import time; "
                                       "Path('ready').touch(); time.sleep(30)"))
        self.assertIsNotNone(processes[0].returncode, "Interrupted direct process not reaped")
        with self.assertRaises(ProcessLookupError):
            os.kill(processes[0].pid, 0)
        self.assertTrue(all(stream.closed for stream in log_handles))
        self.assertEqual(list(self.evidence_dir.glob("*/metadata.json")), [])

    def test_partial_metadata_serialization_failure_never_publishes_final(self):
        def fail_after_partial_write(metadata, stream, **kwargs):
            stream.write('{"gateId":')
            stream.flush()
            raise OSError("Injected partial JSON write")

        with patch("agent_harness.gate_runner.json.dump", side_effect=fail_after_partial_write):
            with self.assertRaises(OSError):
                self.execute(self.gate("print('retained-log')"))
        attempt, = self.evidence_dir.iterdir()
        self.assertFalse((attempt / "metadata.json").exists())
        self.assertEqual({path.name for path in attempt.iterdir()}, {"stdout.log", "stderr.log"})
        self.assertEqual((attempt / "stdout.log").read_text(), "retained-log\n")

    def test_metadata_fsync_failure_removes_unpublished_files(self):
        with patch("agent_harness.gate_runner.os.fsync", side_effect=OSError("Injected fsync")):
            with self.assertRaises(OSError):
                self.execute(self.gate("print('retained-log')"))
        attempt, = self.evidence_dir.iterdir()
        self.assertFalse((attempt / "metadata.json").exists())
        self.assertEqual({path.name for path in attempt.iterdir()}, {"stdout.log", "stderr.log"})

    def test_metadata_replace_failure_removes_unpublished_files(self):
        with patch("agent_harness.gate_runner.os.replace", side_effect=OSError("Injected replace")):
            with self.assertRaises(OSError):
                self.execute(self.gate("print('retained-log')"))
        attempt, = self.evidence_dir.iterdir()
        self.assertFalse((attempt / "metadata.json").exists())
        self.assertEqual({path.name for path in attempt.iterdir()}, {"stdout.log", "stderr.log"})

    def test_metadata_publication_uses_closed_synced_same_directory_temporary(self):
        real_dump, real_fsync, real_replace = json.dump, os.fsync, os.replace
        streams, synced_inodes, publications = [], [], []

        def dump(metadata, stream, **kwargs):
            streams.append(stream)
            return real_dump(metadata, stream, **kwargs)

        def fsync(fd):
            synced_inodes.append(os.fstat(fd).st_ino)
            return real_fsync(fd)

        def publish(source, destination):
            source, destination = Path(source), Path(destination)
            self.assertEqual(source.parent, destination.parent)
            self.assertNotEqual(source.name, destination.name)
            self.assertFalse(destination.exists())
            self.assertTrue(streams[0].closed)
            self.assertIn(source.stat().st_ino, synced_inodes)
            self.assertEqual(json.loads(source.read_text())["status"], "passed")
            publications.append(destination)
            return real_replace(source, destination)

        with patch("agent_harness.gate_runner.json.dump", side_effect=dump), \
                patch("agent_harness.gate_runner.os.fsync", side_effect=fsync), \
                patch("agent_harness.gate_runner.os.replace", side_effect=publish):
            evidence = self.execute(self.gate("print('complete')"))
        self.assertEqual(publications, [self.repo / evidence.evidence_path])
        _, directory = self.artifacts(evidence)
        self.assertEqual({path.name for path in directory.iterdir()},
                         {"stdout.log", "stderr.log", "metadata.json"})

    def test_missing_executable_creates_failed_evidence(self):
        evidence = self.execute(GateDefinition("unit", ("absent-gate-tool-96b36b",), ".", 5))
        self.assertEqual(evidence.status, GateStatus.FAILED)
        self.assertIsNone(evidence.exit_code)
        metadata, directory = self.artifacts(evidence)
        self.assertEqual(metadata["failureKind"], "missing_executable")
        self.assertTrue((directory / "stdout.log").is_file())
        self.assertTrue((directory / "stderr.log").is_file())

    def test_cwd_is_configured_relative_to_repo_not_callers_cwd(self):
        evidence = self.execute(self.gate("from pathlib import Path; print(Path.cwd())", cwd="app"))
        _, directory = self.artifacts(evidence)
        self.assertEqual((directory / "stdout.log").read_text().strip(), str(self.repo / "app"))

    def test_missing_cwd_creates_failed_evidence_without_running_command(self):
        evidence = self.execute(self.gate("from pathlib import Path; Path('executed').touch()",
                                          cwd="missing"))
        metadata, _ = self.artifacts(evidence)
        self.assertEqual(evidence.status, GateStatus.FAILED)
        self.assertEqual(metadata["failureKind"], "missing_cwd")
        self.assertFalse((self.repo / "executed").exists())

    def test_argument_array_keeps_shell_metacharacters_literal(self):
        arguments = ("; touch injected", "$(touch injected)", "a b", "`touch injected`")
        with patch("agent_harness.gate_runner.subprocess.Popen", wraps=subprocess.Popen) as spawn:
            evidence = self.execute(self.gate("import json,sys; print(json.dumps(sys.argv[1:]))",
                                              arguments=arguments))
        self.assertEqual(evidence.status, GateStatus.PASSED)
        _, directory = self.artifacts(evidence)
        self.assertEqual(json.loads((directory / "stdout.log").read_text()), list(arguments))
        self.assertFalse((self.repo / "injected").exists())
        self.assertFalse(spawn.call_args.kwargs.get("shell", False))
        self.assertIsInstance(spawn.call_args.args[0], (tuple, list))

    def test_environment_inherits_only_named_runtime_variables(self):
        with patch.dict(os.environ, {"GATE_FAKE_TOKEN": "synthetic-test-value",
                                     "PYTHONPATH": "/untrusted/import", "LANG": "C"}):
            evidence = self.execute(self.gate("import json,os; print(json.dumps(dict(os.environ)))"))
        _, directory = self.artifacts(evidence)
        environment = json.loads((directory / "stdout.log").read_text())
        self.assertNotIn("GATE_FAKE_TOKEN", environment)
        self.assertNotIn("PYTHONPATH", environment)
        self.assertEqual(environment["LANG"], "C")
        self.assertIn("PATH", environment)

    def test_repeated_attempts_preserve_prior_evidence(self):
        first = self.execute(self.gate("print('first')"))
        second = self.execute(self.gate("print('second')"))
        self.assertNotEqual(first.evidence_path, second.evidence_path)
        _, first_dir = self.artifacts(first)
        _, second_dir = self.artifacts(second)
        self.assertEqual((first_dir / "stdout.log").read_text(), "first\n")
        self.assertEqual((second_dir / "stdout.log").read_text(), "second\n")

    def test_invalid_gate_inputs_are_rejected_before_execution(self):
        gate = self.gate("from pathlib import Path; Path('executed').touch()")
        for invalid in (replace(gate, command="echo untrusted"), replace(gate, command=()),
                        replace(gate, timeout_seconds=0), replace(gate, timeout_seconds=True),
                        replace(gate, cwd="../outside"), replace(gate, cwd=str(self.repo)),
                        replace(gate, command=(sys.executable, "\x00"))):
            with self.subTest(gate=invalid), self.assertRaises(ValueError):
                self.execute(invalid)
        self.assertFalse((self.repo / "executed").exists())

    def test_cwd_symlinks_are_rejected_inside_and_outside_repo(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        for name, target in (("alias", self.repo / "app"), ("escape", Path(temporary.name))):
            (self.repo / name).symlink_to(target, target_is_directory=True)
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.execute(self.gate("print('must not run')", cwd=name))

    def test_evidence_is_confined_to_raw_run_directory(self):
        for directory in (self.repo / "docs/evidence", self.repo.parent / "escaped-evidence",
                          self.repo / "var/agent-harness/runs/../outside"):
            with self.subTest(directory=directory), self.assertRaises(ValueError):
                run_gate(self.repo, self.gate("print('must not run')"), directory, self.head)

    def test_evidence_symlink_is_rejected_without_writing_through_it(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.evidence_dir.parent.mkdir(parents=True)
        self.evidence_dir.symlink_to(temporary.name, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.execute(self.gate("print('must not run')"))
        self.assertEqual(list(Path(temporary.name).iterdir()), [])

    def test_gate_id_cannot_escape_evidence_directory(self):
        evidence = self.execute(replace(self.gate("print('safe')"), id="../outside"))
        self.assertTrue(evidence.evidence_path.is_relative_to(Path("var/agent-harness/runs")))
        self.assertEqual(self.artifacts(evidence)[0]["gateId"], "../outside")

    def test_invalid_head_is_rejected_before_execution(self):
        for head in ("not-a-commit", "a" * 40 + "\n", "a" * 39, "A" * 40):
            with self.subTest(head=head), self.assertRaises(ValueError):
                run_gate(self.repo, self.gate("from pathlib import Path; Path('executed').touch()"),
                         self.evidence_dir, head)
        self.assertFalse((self.repo / "executed").exists())


class EvidenceInvalidationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "state.json"
        self.path.write_bytes((HARNESS / "tests/fixtures/valid_state.json").read_bytes())
        self.original = replace(load_state(self.path), worktree_path=self.root,
                                status=RunStatus.VERIFYING)

    def test_every_gate_is_pending_after_any_head_change_with_history_retained(self):
        old = self.original.gates["unit"]
        gates = {"unit": old, "docs": replace(old, gate_id="docs"),
                 "extra": replace(old, gate_id="extra", status=GateStatus.FAILED, exit_code=1)}
        original = replace(self.original, required_gates=("unit", "docs"), gates=gates)
        result = invalidate_stale_evidence(original, "a" * 40)
        self.assertEqual(result.head_commit, "a" * 40)
        self.assertEqual(result.required_gates, ("unit", "docs"))
        self.assertGreater(result.updated_at, original.updated_at)
        for name, gate in result.gates.items():
            self.assertEqual(gate.status, GateStatus.PENDING)
            self.assertEqual(gate.head_commit, "c" * 40)
            self.assertEqual(gate.evidence_path, gates[name].evidence_path)
            self.assertEqual(gate.exit_code, gates[name].exit_code)
            self.assertEqual(gate.started_at, gates[name].started_at)
            self.assertEqual(gate.ended_at, gates[name].ended_at)
        self.assertEqual(original.gates["unit"].status, GateStatus.PASSED)
        save_state_atomic(self.path, result)
        self.assertEqual(load_state(self.path), result)
        with self.assertRaises(ValueError):
            transition(result, RunStatus.COMPLETED, "Old evidence cannot pass")

    def test_same_head_keeps_current_evidence_valid(self):
        self.assertEqual(invalidate_stale_evidence(self.original, "c" * 40), self.original)

    def test_stale_gate_is_invalidated_even_when_state_head_already_matches(self):
        original = replace(self.original, head_commit="a" * 40)
        self.assertEqual(invalidate_stale_evidence(original, "a" * 40).gates["unit"].status,
                         GateStatus.PENDING)

    def test_head_reversal_does_not_resurrect_old_passes(self):
        newer = invalidate_stale_evidence(self.original, "a" * 40)
        reverted = invalidate_stale_evidence(newer, "c" * 40)
        self.assertEqual(reverted.gates["unit"].status, GateStatus.PENDING)

    def test_completed_state_returns_to_verifying_on_head_change(self):
        original = replace(self.original, status=RunStatus.COMPLETED)
        result = invalidate_stale_evidence(original, "a" * 40)
        self.assertEqual(result.status, RunStatus.VERIFYING)
        self.assertEqual(result.gates["unit"].status, GateStatus.PENDING)
        self.assertEqual(original.status, RunStatus.COMPLETED)

    def test_invalid_current_head_is_rejected(self):
        for head in ("bad-head", "a" * 40 + "\n", "a" * 39, "A" * 40):
            with self.subTest(head=head), self.assertRaises(ValueError):
                invalidate_stale_evidence(self.original, head)


if __name__ == "__main__":
    unittest.main()
