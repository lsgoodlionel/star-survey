"""Controlled argv and bounded streams; subprocesses below are Python fakes."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_run_service import WorktreeCase


class AdapterCase(WorktreeCase):
    def setUp(self):
        super().setUp()
        from agent_harness import codex_adapter
        self.adapter = codex_adapter
        self.init_run()
        self.directory = self.state_path().parent
        self.schema = self.directory / "response.schema.json"
        self.write(self.schema, (Path(codex_adapter.__file__).with_name("codex_response.schema.json")).read_text())
        self.command = codex_adapter.build_codex_command(self.repo, self.schema, "Repair fixture")
        self.children = []
        self.session = "11111111-1111-4111-8111-111111111111"
        self.response = {"status": "completed", "summary": "ok", "changedPaths": ["src/example.txt"],
                         "testsRequested": ["unit"], "needsHuman": False, "sessionId": self.session}

    def stream(self, response=None):
        return [json.dumps({"type": "thread.started", "thread_id": self.session}),
                json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(response or self.response)}}),
                json.dumps({"type": "turn.completed", "usage": {}})]

    def execute(self, lines, code=0, delay=0, command=None, log=None, timeout=2):
        payload = "\n".join(lines) + "\n"
        expression = repr(lines[0] + "\n") + "*" + str(len(lines)) if lines and all(line == lines[0] for line in lines) else repr(payload)
        script = "import sys,time; sys.stdout.write(" + expression + "); sys.stdout.flush(); time.sleep(" + repr(delay) + "); sys.exit(" + repr(code) + ")"
        spawn = subprocess.Popen
        def fake(argv, **kwargs):
            if argv[0] == "git":
                return spawn(argv, **kwargs)
            self.assertEqual(argv[0:2], (self.command[0], "exec"))
            self.assertFalse(kwargs.get("shell", False))
            child = spawn([sys.executable, "-c", script], **kwargs)
            self.children.append(child)
            return child
        with patch("agent_harness.codex_adapter.subprocess.Popen", side_effect=fake):
            return self.adapter.run_codex(command or self.command, timeout, log or self.directory / "codex.jsonl")

    def test_fixed_command_and_safe_resume(self):
        self.assertTrue(Path(self.command[0]).is_absolute())
        self.assertEqual(Path(self.command[0]).resolve(), Path(shutil.which("codex")).resolve())
        self.assertEqual(self.command[1:], ("exec", "--sandbox", "workspace-write", "--approve-for-me",
                                       "--strict-config", "--json", "--output-schema", str(self.schema), "--cd", str(self.repo), "Repair fixture"))
        resumed = self.adapter.build_codex_command(self.repo, self.schema, "repair", self.session)
        self.assertEqual(resumed[0:2], (self.command[0], "exec"))
        self.assertIn("resume", resumed)
        self.assertIn(self.session, resumed)
        for required in ("--sandbox", "workspace-write", "--approve-for-me", "--strict-config", "--json", "--output-schema", "--cd"):
            self.assertIn(required, resumed)

    def test_builder_rejects_secrets_and_permission_overrides(self):
        for value in ("TOKEN=fake-secret", "--dangerously-bypass-approvals-and-sandbox", "--dangerously-bypass-hook-trust",
                      "danger-full-access", "--add-dir /tmp", "--config x=y", "-c x=y", "--worktree", "--oss", "-"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.adapter.build_codex_command(self.repo, self.schema, value)
        with self.assertRaises(ValueError):
            self.adapter.build_codex_command(self.repo, self.schema, "ok", "--last")

    def test_executor_rejects_same_length_tampering_and_secret_prompt(self):
        for index, replacement in ((3, "danger-full-access"), (4, "--dangerously-bypass-hook-trust"),
                                   (5, "--config=x=y"), (6, "--add-dir=/tmp"), (11, "TOKEN=fake-secret")):
            command = list(self.command)
            command[index] = replacement
            result = self.execute(self.stream(), command=command)
            self.assertEqual(result.failure.value, "command_policy")

    def test_schema_field_bounds_and_duplicate_json_keys_are_enforced(self):
        for index, overrides in enumerate(({"summary": "x" * 4097}, {"status": "invented"},
                                           {"changedPaths": ["x"] * 129}, {"changedPaths": ["src/a", "src/a"]},
                                           {"testsRequested": ["shell command"]})):
            result = self.execute(self.stream(dict(self.response, **overrides)), log=self.directory / ("schema-" + str(index) + ".jsonl"))
            self.assertEqual(result.failure.value, "schema_violation")
        result = self.execute(['{"type":"progress","type":"turn.completed"}'], log=self.directory / "duplicate.jsonl")
        self.assertEqual(result.failure.value, "malformed_jsonl")

    def test_event_count_and_failed_turn_are_bounded_typed_failures(self):
        result = self.execute(['{"type":"progress"}'] * 4097)
        self.assertEqual(result.failure.value, "output_limit")
        result = self.execute(['{"type":"turn.failed","error":{"message":"TOKEN=fake-secret"}}'], log=self.directory / "failed.jsonl")
        self.assertEqual(result.failure.value, "turn_failed")
        self.assertNotIn("fake-secret", repr(result))

    def test_resume_cannot_silently_switch_session(self):
        command = self.adapter.build_codex_command(self.repo, self.schema, "repair", "22222222-2222-4222-8222-222222222222")
        result = self.execute(self.stream(), command=command)
        self.assertEqual(result.failure.value, "schema_violation")

    def test_executor_rechecks_policy_before_spawn(self):
        for extra in ("--dangerously-bypass-approvals-and-sandbox", "--dangerously-bypass-hook-trust", "--add-dir", "--config", "-c", "--enable", "--profile", "danger-full-access"):
            with self.subTest(extra=extra), patch("agent_harness.codex_adapter.subprocess.Popen") as spawn:
                result = self.adapter.run_codex((*self.command[:-1], extra, self.command[-1]), 2, self.directory / "bad.jsonl")
                self.assertEqual(result.failure.value, "command_policy")
                spawn.assert_not_called()

    def test_path_poisoning_and_writable_executable_candidates_are_rejected(self):
        poisoned = self.repo / "bin/codex"
        self.write(poisoned, "#!/bin/sh\nexit 0\n")
        poisoned.chmod(0o755)
        with patch("agent_harness.codex_adapter.shutil.which", return_value=str(poisoned)):
            protected = self.adapter.build_codex_command(self.repo, self.schema, "repair")
        self.assertEqual(protected[0], self.command[0])
        command = list(self.command)
        command[0] = "codex"
        self.assertEqual(self.adapter.run_codex(command, 2, self.directory / "relative.jsonl").failure.value,
                         "command_policy")
        command[0] = str(poisoned)
        self.assertEqual(self.adapter.run_codex(command, 2, self.directory / "writable.jsonl").failure.value,
                         "command_policy")

    def test_executor_revalidates_bound_executable_identity(self):
        with patch("agent_harness.codex_adapter.os.stat", side_effect=OSError("changed")), \
                patch("agent_harness.codex_adapter._environment", return_value={"PATH": "/usr/bin:/bin"}), \
                patch("agent_harness.codex_adapter.subprocess.Popen") as spawn:
            result = self.adapter.run_codex(self.command, 2, self.directory / "identity.jsonl")
        self.assertEqual(result.failure.value, "command_policy")
        spawn.assert_not_called()

    def test_environment_rejects_sensitive_values_for_every_allowed_key(self):
        allowed = ("PATH", "HOME", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "TZ", "SYSTEMROOT", "WINDIR")
        for index, name in enumerate(allowed):
            with self.subTest(name=name), patch.dict("os.environ", {name: "TOKEN=fake-secret"}, clear=True):
                result = self.adapter.run_codex(self.command, 2, self.directory / ("env-" + str(index) + ".jsonl"))
                self.assertEqual(result.failure.value, "environment_policy")

    def test_environment_normalizes_paths_and_rejects_relative_or_missing_entries(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            with patch.dict("os.environ", {"PATH": first + os.pathsep + second, "HOME": first,
                                            "TMPDIR": second, "LANG": "C.UTF-8", "TZ": "UTC"}, clear=True):
                environment = self.adapter._environment()
            self.assertEqual(environment["PATH"], self.adapter._environment_path(os.defpath, multiple=True))
            self.assertEqual(environment["HOME"], str(Path(first).resolve()))
        for index, values in enumerate(({"PATH": "relative"}, {"PATH": "/does/not/exist"},
                                        {"HOME": "relative"}, {"TMPDIR": "/does/not/exist"},
                                        {"LANG": "bad value"}, {"LC_ALL": "bad value"},
                                        {"TZ": "../escape"}, {"SYSTEMROOT": "relative"}, {"WINDIR": "relative"})):
            with self.subTest(values=values), patch.dict("os.environ", values, clear=True):
                result = self.adapter.run_codex(self.command, 2, self.directory / ("bad-env-" + str(index) + ".jsonl"))
                self.assertEqual(result.failure.value, "environment_policy")

    def test_valid_stream_returns_only_sanitized_typed_metadata(self):
        response = dict(self.response, summary="TOKEN=fake-secret")
        lines = self.stream(response)
        result = self.execute(lines)
        self.assertIsNone(result.failure)
        self.assertEqual(result.session_id, self.session)
        self.assertEqual(result.changed_paths, ("src/example.txt",))
        self.assertNotIn("fake-secret", repr(result))
        self.assertEqual((self.directory / "codex.jsonl").read_text(), "\n".join(lines) + "\n")

    def test_intermediate_agent_message_does_not_replace_final_response(self):
        lines = self.stream()
        lines.insert(1, json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Working on the fixture"}}))
        result = self.execute(lines)
        self.assertIsNone(result.failure)
        self.assertEqual(result.status, "completed")

    def test_invalid_streams_return_typed_failures_without_raw_text(self):
        invalid = [(["TOKEN=fake-secret"], 0, "malformed_jsonl"),
                   (["x" * 65537], 0, "output_limit"),
                   (self.stream()[1:], 0, "missing_session"),
                   ([self.stream()[0], self.stream()[2]], 0, "missing_response"),
                   (self.stream(dict(self.response, needsHuman="false")), 0, "schema_violation"),
                   (self.stream(dict(self.response, extra="TOKEN=fake-secret")), 0, "schema_violation"),
                   (self.stream(dict(self.response, changedPaths=["../escape"])), 0, "schema_violation"),
                   (self.stream(dict(self.response, sessionId="22222222-2222-4222-8222-222222222222")), 0, "schema_violation"),
                   (self.stream()[:-1], 0, "missing_completion"),
                   (self.stream(), 7, "nonzero_exit")]
        for index, (lines, code, expected) in enumerate(invalid):
            with self.subTest(expected=expected):
                result = self.execute(lines, code, log=self.directory / (str(index) + ".jsonl"))
                self.assertEqual(result.failure.value, expected)
                self.assertNotIn("fake-secret", repr(result))

    def test_timeout_and_total_output_are_bounded(self):
        result = self.execute(self.stream(), delay=2, timeout=1)
        self.assertEqual(result.failure.value, "timeout")
        result = self.execute([json.dumps({"type": "progress", "data": "x" * 60000})] * 80,
                              log=self.directory / "large.jsonl")
        self.assertEqual(result.failure.value, "output_limit")
        self.assertLessEqual((self.directory / "large.jsonl").stat().st_size, 4 * 1024 * 1024)

    def test_denied_group_signal_still_reaps_owned_child(self):
        with patch("agent_harness.codex_adapter.os.killpg", side_effect=PermissionError("fixture")):
            result = self.execute(self.stream(), delay=2, timeout=1)
        self.assertEqual(result.failure.value, "timeout")
        self.assertIsNotNone(self.children[0].poll())

    def test_unconfirmed_process_group_cleanup_cannot_report_success(self):
        with patch("agent_harness.codex_adapter.os.killpg", side_effect=PermissionError("fixture")):
            result = self.execute(self.stream())
        self.assertEqual(getattr(result.failure, "value", None), "cleanup_error")

    def test_generic_cleanup_oserrors_never_escape(self):
        cases = (("killpg", patch("agent_harness.codex_adapter.os.killpg", side_effect=OSError("killpg"))),
                 ("wait", patch("agent_harness.codex_adapter._wait_process", side_effect=OSError("wait"))),
                 ("close", patch("agent_harness.codex_adapter._close_resource", side_effect=OSError("close"))))
        for index, (name, failure) in enumerate(cases):
            with self.subTest(name=name), failure:
                result = self.execute(self.stream(), log=self.directory / ("cleanup-" + str(index) + ".jsonl"))
                self.assertEqual(result.failure.value, "cleanup_error")

    def test_fallback_kill_oserror_is_typed_and_direct_child_is_reaped_when_possible(self):
        with patch("agent_harness.codex_adapter.os.killpg", side_effect=OSError("group")), \
                patch("agent_harness.codex_adapter._kill_process", side_effect=OSError("child")):
            result = self.execute(self.stream(), delay=2, timeout=1)
        self.assertEqual(result.failure.value, "cleanup_error")

    def test_raw_log_rejects_outside_unignored_tracked_and_symlink_paths(self):
        for log in (self.repo / "raw.jsonl", self.repo / "var/agent-harness/runs/../escape.jsonl"):
            result = self.execute(self.stream(), log=log)
            self.assertEqual(result.failure.value, "storage_policy")
            self.assertFalse(log.exists())
        target = self.directory / "link"
        target.symlink_to(self.root, target_is_directory=True)
        self.assertEqual(self.execute(self.stream(), log=target / "raw.jsonl").failure.value, "storage_policy")
        self.write(self.directory / "tracked.jsonl", "existing")
        self.git("add", "-f", str(self.directory / "tracked.jsonl"))
        self.assertEqual(self.execute(self.stream(), log=self.directory / "tracked.jsonl").failure.value, "storage_policy")

    def test_unignored_raw_log_is_rejected_before_creation(self):
        self.write(self.repo / ".gitignore", "")
        self.assertEqual(self.execute(self.stream()).failure.value, "storage_policy")
        self.assertFalse((self.directory / "codex.jsonl").exists())

    def test_ignoring_only_event_file_does_not_authorize_run_directory(self):
        self.write(self.repo / ".gitignore", "/var/agent-harness/runs/" + self.state.run_id + "/codex.jsonl*\n")
        result = self.execute(self.stream())
        self.assertEqual(result.failure.value, "storage_policy")
        self.assertFalse((self.directory / "codex.jsonl").exists())
