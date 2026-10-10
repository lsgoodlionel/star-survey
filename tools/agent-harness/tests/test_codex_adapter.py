"""Controlled argv and bounded streams; subprocesses below are Python fakes."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
import io
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
                environment = self.adapter._environment(self.repo)
            self.assertNotIn(first, environment["PATH"])
            self.assertEqual(environment["HOME"], str(Path(first).resolve()))
        for index, values in enumerate(({"HOME": "relative"}, {"TMPDIR": "/does/not/exist"},
                                        {"LANG": "bad value"}, {"LC_ALL": "bad value"},
                                        {"TZ": "../escape"}, {"SYSTEMROOT": "relative"}, {"WINDIR": "relative"})):
            with self.subTest(values=values), patch.dict("os.environ", values, clear=True):
                result = self.adapter.run_codex(self.command, 2, self.directory / ("bad-env-" + str(index) + ".jsonl"))
                self.assertEqual(result.failure.value, "environment_policy")

    def test_controlled_path_exposes_project_tools_and_ignores_path_poisoning(self):
        poisoned = self.repo / "poisoned"
        poisoned.mkdir()
        for name in ("git", "node", "npm", "docker", "python3.11"):
            executable = poisoned / name
            self.write(executable, "#!/bin/sh\nexit 99\n")
            executable.chmod(0o755)
        with patch.dict("os.environ", {"PATH": str(poisoned), "HOME": str(Path.home())}, clear=True):
            environment = self.adapter._environment(self.repo)
        self.assertNotIn(str(poisoned), environment["PATH"].split(os.pathsep))
        for name in ("git", "node", "npm", "docker"):
            with self.subTest(name=name):
                executable = shutil.which(name, path=environment["PATH"])
                self.assertIsNotNone(executable)
                self.assertFalse(Path(executable).is_relative_to(self.repo))
        python = next((shutil.which(f"python3.{minor}", path=environment["PATH"])
                       for minor in range(11, 15)
                       if shutil.which(f"python3.{minor}", path=environment["PATH"])), None)
        self.assertIsNotNone(python)
        project = Path(__file__).resolve().parents[3]
        probe = subprocess.run([str(project / "scripts/agent-harness"), "--python", "--version"],
                               cwd=project, env=environment, text=True, capture_output=True, check=False)
        self.assertEqual(probe.returncode, 0)
        self.assertRegex(probe.stdout, r"^Python 3\.1[1-9]\.")

    def test_tool_discovery_never_anchors_to_path_before_module_import(self):
        project = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory(prefix="task7-path-", dir=project.parents[2]) as directory:
            poisoned = Path(directory)
            for name in ("codex", "git", "node", "npm", "docker", "python3.11", "java"):
                executable = poisoned / name
                self.write(executable, "#!/bin/sh\nexit 97\n")
                executable.chmod(0o755)
            (poisoned / "git").unlink()
            (poisoned / "git").symlink_to("/usr/bin/git")
            script = """
import json
from pathlib import Path
import shutil
import subprocess
import sys
from agent_harness import codex_adapter

project = Path(sys.argv[1])
environment = codex_adapter._environment(project)
tools = {name: shutil.which(name, path=environment["PATH"])
         for name in ("git", "node", "npm", "docker", "java")}
tools["python"] = next((shutil.which(f"python3.{minor}", path=environment["PATH"])
                        for minor in range(11, 15)
                        if shutil.which(f"python3.{minor}", path=environment["PATH"])), None)
wrapper = subprocess.run([str(project / "scripts/agent-harness"), "--python", "--version"],
                         cwd=project, env=environment, text=True, capture_output=True, check=False)
print(json.dumps({"path": environment["PATH"], "tools": tools, "codex": codex_adapter._BOUND_CODEX,
                  "wrapperCode": wrapper.returncode, "wrapperOutput": wrapper.stdout}))
"""
            environment = {"PATH": str(poisoned), "PYTHONPATH": str(project / "tools/agent-harness")}
            probe = subprocess.run([sys.executable, "-c", script, str(project)], env=environment,
                                   text=True, capture_output=True, check=False)
        self.assertEqual(probe.returncode, 0, probe.stderr)
        result = json.loads(probe.stdout)
        self.assertNotIn(str(poisoned), result["path"].split(os.pathsep))
        self.assertFalse(Path(result["codex"]).is_relative_to(poisoned), result)
        self.assertTrue(all(result["tools"].values()), result)
        self.assertTrue(all(not Path(value).is_relative_to(poisoned)
                            for value in result["tools"].values()), result)
        self.assertEqual(result["wrapperCode"], 0, result)
        self.assertRegex(result["wrapperOutput"], r"^Python 3\.1[1-9]\.")

    def test_doctor_reports_controlled_tool_policy_failures(self):
        from agent_harness.cli import main
        from agent_harness.doctor import DoctorReport
        output = io.StringIO()
        issue = {"id": "controlled-tool:docker", "message": "受控工具不可用或不受信任"}
        with patch("agent_harness.cli.run_doctor", return_value=DoctorReport((), (), ())), \
                patch("agent_harness.cli.controlled_tool_errors", return_value=(issue,)), \
                redirect_stdout(output):
            code = main(["--repo", str(self.repo), "doctor", "--json"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["error"], [issue])

    def test_doctor_never_executes_path_candidates_before_controlled_validation(self):
        project = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory(prefix="task7-doctor-", dir=project.parents[2]) as directory:
            poisoned = Path(directory)
            marker = poisoned / "executed"
            script_body = "#!/bin/sh\nprintf x >> '" + str(marker) + "'\nprintf 'v22.0.0\\n'\n"
            for name in ("codex", "git", "node", "npm", "docker", "python3.11", "java"):
                executable = poisoned / name
                self.write(executable, script_body)
                executable.chmod(0o755)
            script = """
import contextlib
import io
import json
from pathlib import Path
import sys

from agent_harness.cli import main

output = io.StringIO()
with contextlib.redirect_stdout(output):
    code = main(["--repo", sys.argv[1], "doctor", "--json"])
print(json.dumps({"code": code, "marker": Path(sys.argv[2]).exists(), "output": output.getvalue()}))
"""
            environment = {"PATH": str(poisoned), "PYTHONPATH": str(project / "tools/agent-harness")}
            probe = subprocess.run([sys.executable, "-c", script, str(project), str(marker)], env=environment,
                                   text=True, capture_output=True, check=False)
        self.assertEqual(probe.returncode, 0, probe.stderr)
        result = json.loads(probe.stdout)
        self.assertFalse(result["marker"], result)
        self.assertIn(result["code"], (0, 2))

    def test_real_wrapper_and_module_cli_never_execute_caller_path_programs(self):
        project = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory(prefix="task7-entrypoint-", dir=project.parents[2]) as directory:
            poisoned = Path(directory)
            marker = poisoned / "executed"
            script_body = "#!/bin/sh\nprintf '%s\\n' \"$0\" >> '" + str(marker) + "'\nexit 99\n"
            for name in ("dirname", "pwd", "git", "command", "python3.11", "python3.12",
                         "python3.13", "python3.14", "python3"):
                executable = poisoned / name
                self.write(executable, script_body)
                executable.chmod(0o755)
            environment = {
                "HOME": str(Path.home()),
                "PATH": str(poisoned),
                "PYTHONPATH": str(project / "tools/agent-harness"),
            }
            wrapper = subprocess.run([str(project / "scripts/agent-harness"), "doctor", "--json"],
                                     cwd=project / "tools/agent-harness", env=environment,
                                     text=True, capture_output=True, check=False)
            wrapper_marker = marker.read_text() if marker.exists() else ""
            marker.unlink(missing_ok=True)
            module = subprocess.run([sys.executable, "-m", "agent_harness.cli", "doctor", "--json"],
                                    cwd=project / "tools/agent-harness", env=environment,
                                    text=True, capture_output=True, check=False)
            module_marker = marker.read_text() if marker.exists() else ""
        self.assertIn(wrapper.returncode, (0, 2), wrapper.stderr or wrapper.stdout)
        self.assertIn(module.returncode, (0, 2), module.stderr or module.stdout)
        self.assertEqual(wrapper_marker, "")
        self.assertEqual(module_marker, "")

    def test_controlled_tool_errors_include_codex(self):
        with patch("agent_harness.codex_adapter._trusted_codex", side_effect=ValueError("fixture")):
            errors = self.adapter.controlled_tool_errors(self.repo)
        self.assertIn("controlled-tool:codex", {error["id"] for error in errors})

    def test_linux_trusted_root_symlinks_and_supported_python_are_bound(self):
        project = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory(prefix="task7-linux-", dir=project.parents[2]) as directory:
            root = Path(directory)
            java_target = root / "lib/jvm/java-21/bin/java"
            java_target.parent.mkdir(parents=True)
            self.write(java_target, "#!/bin/sh\nexit 0\n")
            java_target.chmod(0o755)
            entry = root / "bin/java"
            entry.parent.mkdir()
            entry.symlink_to(Path("../lib/jvm/java-21/bin/java"))
            with patch("agent_harness.codex_adapter.sys.platform", "linux"):
                bound = self.adapter._bound_tool("java", ((str(entry), str(root)),))
            self.assertEqual(bound.target, str(java_target))
            self.assertEqual(self.adapter._trusted_tool_directory(self.repo, bound), entry.parent)

            codex_target = root / "lib/codex/codex"
            codex_target.parent.mkdir(parents=True)
            self.write(codex_target, "#!/bin/sh\nexit 0\n")
            codex_target.chmod(0o755)
            codex_entry = root / "bin/codex"
            codex_entry.symlink_to(Path("../lib/codex/codex"))
            with patch("agent_harness.codex_adapter.sys.platform", "linux"):
                bound_codex = self.adapter._bound_tool("codex", ((str(codex_entry), str(root)),))
            self.assertEqual(bound_codex.target, str(codex_target))
            with patch("agent_harness.codex_adapter._BOUND_CODEX_TOOL", bound_codex), \
                    patch("agent_harness.codex_adapter._BOUND_CODEX", str(codex_entry)):
                self.assertEqual(self.adapter._trusted_codex(self.repo, str(codex_entry)), codex_entry)

            python = root / "bin/python3.13"
            self.write(python, "#!/bin/sh\nexit 0\n")
            python.chmod(0o755)
            bound_python = self.adapter._bound_tool("python", ((str(python), str(root)),),
                                                     target_directory=True)
            self.assertEqual(bound_python.target, str(python))

    def test_linux_symlink_policy_rejects_escape_writable_and_identity_changes(self):
        project = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory(prefix="task7-linux-negative-", dir=project.parents[2]) as directory:
            root = Path(directory)
            binary = root / "lib/java"
            binary.parent.mkdir()
            self.write(binary, "#!/bin/sh\nexit 0\n")
            binary.chmod(0o755)
            entry = root / "bin/java"
            entry.parent.mkdir()
            entry.symlink_to(Path("../lib/java"))
            with patch("agent_harness.codex_adapter.sys.platform", "linux"):
                bound = self.adapter._bound_tool("java", ((str(entry), str(root)),))
            entry.parent.chmod(0o777)
            with self.assertRaises(ValueError):
                self.adapter._trusted_tool_directory(self.repo, bound)
            entry.parent.chmod(0o755)
            binary.chmod(0o775)
            with self.assertRaises(ValueError):
                self.adapter._trusted_tool_directory(self.repo, bound)
            binary.chmod(0o755)
            with patch("agent_harness.codex_adapter.os.getuid", return_value=os.getuid() + 1), \
                    self.assertRaises(ValueError):
                self.adapter._trusted_tool_directory(self.repo, bound)
            entry.unlink()
            entry.symlink_to(Path("../lib/java"))
            with self.assertRaises(ValueError):
                self.adapter._trusted_tool_directory(self.repo, bound)

            external = root.parent / (root.name + "-outside")
            self.write(external, "#!/bin/sh\nexit 0\n")
            external.chmod(0o755)
            escaping = root / "bin/codex"
            escaping.symlink_to(external)
            with patch("agent_harness.codex_adapter.sys.platform", "linux"):
                rejected = self.adapter._bound_tool("codex", ((str(escaping), str(root)),))
            self.assertIsNone(rejected.entry)
            external.unlink()

    def test_linux_alternatives_chain_across_approved_roots_is_revalidated(self):
        project = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory(prefix="task7-linux-alternatives-",
                                         dir=project.parents[2]) as directory:
            fixture = Path(directory)
            usr, alternatives = fixture / "usr", fixture / "etc/alternatives"
            target = usr / "lib/jvm/java-21/bin/java"
            target.parent.mkdir(parents=True)
            alternatives.mkdir(parents=True)
            self.write(target, "#!/bin/sh\nexit 0\n")
            target.chmod(0o755)
            intermediate = alternatives / "java"
            intermediate.symlink_to(target)
            entry = usr / "bin/java"
            entry.parent.mkdir(parents=True)
            entry.symlink_to(intermediate)
            with patch("agent_harness.codex_adapter.sys.platform", "linux"):
                bound = self.adapter._bound_tool("java", ((str(entry), (str(usr), str(alternatives))),))
            self.assertEqual(bound.target, str(target))
            self.assertEqual(len(bound.chain_identities), 3)
            self.assertEqual(self.adapter._trusted_tool_directory(self.repo, bound), entry.parent)
            intermediate.unlink()
            intermediate.symlink_to(target)
            with self.assertRaises(ValueError):
                self.adapter._trusted_tool_directory(self.repo, bound)

    def test_environment_path_resolution_runtime_errors_are_typed(self):
        loop = self.root / "environment-loop"
        loop.symlink_to(loop, target_is_directory=True)
        with patch.dict("os.environ", {"HOME": str(loop)}, clear=True), \
                patch("agent_harness.codex_adapter.subprocess.Popen") as spawn:
            result = self.adapter.run_codex(self.command, 2, self.directory / "loop.jsonl")
        self.assertEqual(result.failure.value, "environment_policy")
        self.assertNotIn(str(loop), repr(result))
        spawn.assert_not_called()

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
