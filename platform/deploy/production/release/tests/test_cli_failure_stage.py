"""Exercise the real CLI/probe process boundary with only synthetic operations."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

RELEASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE))
import native_acceptance_evidence as evidence


class StageBoundaryTest(unittest.TestCase):
    def child(self, body):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        script = Path(directory.name) / "child"
        script.write_text("#!" + sys.executable + "\nimport os, sys\n"
                          + f"sys.path.insert(0, {str(RELEASE.parent)!r})\n" + body)
        script.chmod(0o700)
        return script

    def read_failure(self, child):
        manager = Mock()
        with self.assertRaises(RuntimeError) as failure:
            evidence.perform_upgrade(child, "unused", "unused", "a"*64, "v", manager, RuntimeError)
        manager._read_state.assert_not_called()
        self.assertEqual(failure.exception.upgrade_category, "cli-exit")
        return failure.exception.upgrade_stage, failure.exception.upgrade_exit_code

    def test_real_cli_emits_first_failure_and_retains_exit_code(self):
        child = self.child(
            "import surveyctl as ctl\n"
            "def operation(manager, *args):\n"
            "    with manager._failure_boundary('upgrade-runtime-switch'):\n"
            "        with manager._failure_boundary('upgrade-health'):\n"
            "            raise ctl.RuntimeHealthError('synthetic private detail')\n"
            "ctl.SurveyManager.upgrade = operation\nsys.exit(ctl.main())\n")
        self.assertEqual(self.read_failure(child), ("upgrade-health", 4))

    def test_real_backup_boundary_preserves_state_and_withholds_success_evidence(self):
        child = self.child("""import surveyctl as ctl
from types import SimpleNamespace
from unittest.mock import MagicMock
def upgrade(manager, *args):
    manager._require_personnel_capability = MagicMock()
    manager._verify_owned_support = MagicMock()
    manager._read_state = MagicMock(return_value={})
    manager._backup_for_upgrade_or_manual = MagicMock(return_value="mock-backup")
    manager._verify_backup = MagicMock(side_effect=ctl.RuntimeHealthError("private detail"))
    manager._write_state = MagicMock()
    try:
        manager._upgrade_owned_staged(SimpleNamespace(version="0.3.30-rc.1"),
                                      {"version":"0.3.29-rc.1","databaseSchema":"953"})
    finally:
        assert manager._write_state.call_args.kwargs["status"] == "recovery_required"
        assert manager._upgrade_execution_version is None
ctl.SurveyManager.upgrade = upgrade
sys.exit(ctl.main())
""")
        probe, writer = Mock(), Mock()
        def run():
            evidence.perform_upgrade(child, "unused", "unused", "a"*64, "v", Mock(), RuntimeError)
        probe.run_and_verify.side_effect = run
        with self.assertRaises(RuntimeError) as failure:
            evidence.record_continuity(probe, {}, {}, "", "", "upgrade-data.json", writer, execution={})
        self.assertEqual((failure.exception.upgrade_stage, failure.exception.upgrade_exit_code),
                         ("upgrade-backup-check", 4))
        writer.assert_not_called()

    def test_channel_unavailable_preserves_original_exit(self):
        child = self.child("sys.exit(4)\n")
        with patch.object(evidence.tempfile, "TemporaryFile", side_effect=OSError):
            self.assertEqual(self.read_failure(child), ("unknown", 4))

    def test_invalid_missing_and_mixed_frames_are_unknown(self):
        for frame in (b'', b'other\n', b'install-owner-init\n', b'upgrade-health',
                      b'upgrade-health\nextra\n', b'upgrade-health\n'*2, b'x'*100):
            with self.subTest(frame=frame):
                child = self.child("fd=os.environ.get('SURVEYCTL_STAGE_FD')\n"
                                   f"if fd: os.write(int(fd), {frame!r})\n"
                                   "print('upgrade-health', file=sys.stderr)\nsys.exit(5)\n")
                self.assertEqual(self.read_failure(child), ("unknown", 5))

    def test_install_emits_only_the_fixed_stage_on_diagnostic_channel(self):
        child = self.child("""import surveyctl as ctl
from types import SimpleNamespace
from unittest.mock import MagicMock
from contextlib import nullcontext
original = ctl.SurveyManager.install
def install(manager, *args):
    manager._mutation_lock = MagicMock(return_value=nullcontext())
    manager._assert_target_available = manager._validate_install_inputs = MagicMock()
    manager.preflight = MagicMock(return_value={"architecture":"arm64"})
    manager._begin_operation = manager._mkdir = manager._stage_release = MagicMock()
    manager._activate_personnel_floor = manager._verify_owned_support = MagicMock()
    manager._compose = MagicMock(return_value=["compose"])
    manager._run = MagicMock()
    manager._runtime = MagicMock(side_effect=ctl.RuntimeHealthError("synthetic private detail"))
    manager._write_state = MagicMock()
    manager._atomic_write = MagicMock()
    ctl.load_manifest = MagicMock(return_value=SimpleNamespace(version="0.3.29-rc.1", database_schema="953"))
    try:
        original(manager, *args)
    finally:
        assert manager._write_state.call_args.kwargs["status"] == "install_failed"
        manager._atomic_write.assert_not_called()
ctl.SurveyManager.install = install
sys.exit(ctl.main())
""")
        with tempfile.TemporaryFile() as channel:
            result = subprocess.run([child,'--target','unused','install','--manifest','unused','--public-host','host',
                                     '--admin-user','user','--admin-email','mail'], capture_output=True,
                                    env={**os.environ,'SURVEYCTL_STAGE_FD':str(channel.fileno())},
                                    pass_fds=(channel.fileno(),))
            channel.seek(0)
            self.assertEqual(result.returncode, 4)
            self.assertEqual(channel.read(), b'install-owner-init\n')
            self.assertNotIn(b"synthetic private detail", result.stderr + result.stdout)
