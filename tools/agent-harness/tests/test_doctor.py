"""Read-only environment reports with controlled external tool discovery."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_harness.doctor import run_doctor


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
