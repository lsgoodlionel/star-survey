"""Read-only environment reports with controlled external tool discovery."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_harness.doctor import run_doctor
from test_run_service import WorktreeCase


class DoctorTests(unittest.TestCase):
    def test_report_separates_checks_and_does_not_mutate_repository(self):
        repo = Path(__file__).resolve().parents[3]
        before = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True).stdout
        report = run_doctor(repo)
        document = report.to_dict()
        self.assertEqual(set(document), {"ok", "warning", "error"})
        self.assertEqual(json.loads(json.dumps(document)), document)
        after = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True).stdout
        self.assertEqual(before, after)

    def test_missing_tools_and_disk_failure_are_typed_without_raw_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("agent_harness.doctor.shutil.which", return_value=None), \
                    patch("agent_harness.doctor.shutil.disk_usage", side_effect=OSError("TOKEN=fake-secret")):
                report = run_doctor(root)
            checks = report.to_dict()
            all_ids = {check["id"] for items in checks.values() for check in items}
            self.assertTrue({"git", "docker", "node", "java", "python", "codex", "disk"} <= all_ids)
            self.assertTrue(report.error)
            self.assertNotIn("fake-secret", json.dumps(checks))
            self.assertEqual(list(root.iterdir()), [])

    def test_version_checks_use_fixed_read_only_commands(self):
        real_run = subprocess.run
        versions = {"git": "git version 2.45.0", "docker": "Docker version 28.0.0",
                    "node": "v22.20.0", "java": 'openjdk version "21.0.8"',
                    "codex": "codex-cli 0.1"}
        def fake_run(args, **kwargs):
            name = Path(args[0]).name
            if name in versions and args[1:] in (["--version"], ["-version"]):
                return subprocess.CompletedProcess(args, 0, versions[name], "")
            return real_run(args, **kwargs)
        repo = Path(__file__).resolve().parents[3]
        with patch("agent_harness.doctor.shutil.which", side_effect=lambda name: "/fake/" + name), \
                patch("agent_harness.doctor.subprocess.run", side_effect=fake_run), \
                patch("agent_harness.doctor.shutil.disk_usage", return_value=shutil._ntuple_diskusage(10**12, 0, 10**12)):
            report = run_doctor(repo).to_dict()
        ok_ids = {item["id"] for item in report["ok"]}
        self.assertTrue({"node", "java", "python", "docker", "codex", "disk"} <= ok_ids)

    def test_wrong_node_java_versions_are_warnings(self):
        real_run = subprocess.run
        def old_version(args, **kwargs):
            if args[1:] in (["--version"], ["-version"]):
                return subprocess.CompletedProcess(args, 0, 'v18.0.0' if "node" in args[0] else 'openjdk version "17.0.1"', "")
            return real_run(args, **kwargs)
        with tempfile.TemporaryDirectory() as directory, \
                patch("agent_harness.doctor.shutil.which", side_effect=lambda name: "/fake/" + name), \
                patch("agent_harness.doctor.subprocess.run", side_effect=old_version):
            report = run_doctor(Path(directory)).to_dict()
        warnings = {item["id"] for item in report["warning"]}
        self.assertTrue({"node", "java"} <= warnings)

    def test_doctor_checks_gate_interpreter_even_when_cli_python_is_supported(self):
        real_run = subprocess.run
        def wrong_gate_runtime(args, **kwargs):
            if args[1:] == ["--python", "--version"]:
                return subprocess.CompletedProcess(args, 0, "Python 3.9.6\n", "")
            return real_run(args, **kwargs)
        with patch("agent_harness.doctor.subprocess.run", side_effect=wrong_gate_runtime):
            report = run_doctor(Path(__file__).resolve().parents[3])
        self.assertTrue(any(check["id"] == "gate-python:harness-tests" for check in report.error))


class DoctorTrustTests(WorktreeCase):
    def setUp(self):
        super().setUp()
        source = Path(__file__).resolve().parents[3] / "scripts/agent-harness"
        self.wrapper = self.repo / "scripts/agent-harness"
        self.write(self.wrapper, source.read_text())
        self.wrapper.chmod(0o755)
        for name in ("AGENTS.md", "docs/agent/STATE_SCHEMA.json", "docs/agent/PROJECT_MEMORY.md", "docs/agent/AUTONOMY.md"):
            self.write(self.repo / name, "{}\n")
        self.matrix_path = self.repo / "docs/agent/GATE_MATRIX.yaml"
        self.matrix = json.loads(self.matrix_path.read_text())
        for gate in self.matrix["gates"]:
            gate["command"] = ["scripts/agent-harness", "--python", "--version"]
        self.write(self.matrix_path, json.dumps(self.matrix))
        self.git("add", ".")
        self.git("commit", "-m", "trusted doctor fixture")

    def doctor_calls(self):
        real_run, commands = subprocess.run, []
        def record(args, **kwargs):
            commands.append(tuple(map(str, args)))
            return real_run(args, **kwargs)
        before = {p: p.read_bytes() for p in self.repo.rglob("*") if p.is_file()}
        with patch("agent_harness.doctor.subprocess.run", side_effect=record):
            report = run_doctor(self.repo)
        after = {p: p.read_bytes() for p in self.repo.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        probes = [args for args in commands if args[1:] in (("--python", "--version"), ("--version",))
                  and Path(args[0]).name not in ("git", "docker", "node", "codex", "java")]
        return report, probes

    def test_trusted_python_gates_share_one_fixed_wrapper_probe(self):
        report, probes = self.doctor_calls()
        self.assertFalse(report.error, report.to_dict())
        self.assertEqual(probes, [(str(self.wrapper), "--python", "--version")])
        self.assertTrue({"gate-python:unit", "gate-python:extra"} <= {c["id"] for c in report.ok})

    def test_matrix_cannot_select_unknown_or_malicious_python_entry(self):
        fake = self.repo / "fake-runtime"
        self.write(fake, "#!/bin/sh\nprintf changed > doctor-mutated.txt\nprintf 'Python 3.11.15\\n'\n")
        fake.chmod(0o755)
        for command in (["./fake-runtime", "--python", "-m", "unittest"],
                        ["./fake-runtime", "-m", "unittest"],
                        [sys.executable, "-m", "unittest"],
                        ["scripts/missing-runtime", "--python", "-m", "unittest"],
                        [str(self.wrapper), "--python", "-m", "unittest"]):
            with self.subTest(command=command):
                matrix = json.loads(json.dumps(self.matrix))
                matrix["gates"][0]["command"] = command
                self.write(self.matrix_path, json.dumps(matrix))
                self.git("add", "fake-runtime", str(self.matrix_path.relative_to(self.repo)))
                self.git("commit", "-m", "untrusted matrix entry fixture")
                report, probes = self.doctor_calls()
                self.assertTrue(report.error)
                self.assertEqual(probes, [])
                self.assertFalse((self.repo / "doctor-mutated.txt").exists())

    def test_uncommitted_matrix_or_wrapper_is_never_executed(self):
        original_wrapper = self.wrapper.read_bytes()
        original_matrix = self.matrix_path.read_bytes()
        for change in ("matrix", "wrapper", "staged-matrix", "staged-wrapper"):
            with self.subTest(change=change):
                target = self.matrix_path if "matrix" in change else self.wrapper
                original = original_matrix if "matrix" in change else original_wrapper
                target.write_bytes(original + b"\n")
                if change.startswith("staged"):
                    self.git("add", str(target.relative_to(self.repo)))
                report, probes = self.doctor_calls()
                self.assertTrue(report.error)
                self.assertEqual(probes, [])
                target.write_bytes(original)
                self.git("add", str(target.relative_to(self.repo)))

    def test_symlink_or_untracked_fixed_wrapper_is_never_executed(self):
        original = self.wrapper.read_bytes()
        target = self.repo / "scripts/alternate"
        self.write(target, original.decode())
        target.chmod(0o755)
        self.wrapper.unlink()
        self.wrapper.symlink_to(target.name)
        report, probes = self.doctor_calls()
        self.assertTrue(report.error)
        self.assertEqual(probes, [])
        self.wrapper.unlink()
        self.write(self.wrapper, original.decode())
        self.wrapper.chmod(0o755)
        self.git("rm", "--cached", "scripts/agent-harness")
        self.git("commit", "-m", "untracked wrapper fixture")
        report, probes = self.doctor_calls()
        self.assertTrue(report.error)
        self.assertEqual(probes, [])

    def test_cyclic_symlink_wrapper_or_gate_cwd_reports_error_without_execution(self):
        original = self.wrapper.read_bytes()
        self.wrapper.unlink()
        self.wrapper.symlink_to(self.wrapper.name)
        try:
            report, probes = self.doctor_calls()
        except RuntimeError:
            self.fail("doctor must reject symlink loops with a typed report")
        self.assertTrue(report.error)
        self.assertEqual(probes, [])
        self.wrapper.unlink()
        self.wrapper.write_bytes(original)
        self.wrapper.chmod(0o755)
        alias = self.repo / "alias"
        alias.symlink_to(alias.name)
        matrix = json.loads(json.dumps(self.matrix))
        matrix["gates"][0]["cwd"] = "alias"
        matrix["gates"][0]["command"] = ["../scripts/agent-harness", "--python", "--version"]
        self.write(self.matrix_path, json.dumps(matrix))
        self.git("add", "alias", str(self.matrix_path.relative_to(self.repo)))
        self.git("commit", "-m", "cyclic cwd fixture")
        try:
            report, probes = self.doctor_calls()
        except RuntimeError:
            self.fail("doctor must reject cyclic Gate cwd with a typed report")
        self.assertTrue(report.error)
        self.assertEqual(probes, [])
