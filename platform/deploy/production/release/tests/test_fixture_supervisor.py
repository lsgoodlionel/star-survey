import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

SUPERVISOR = Path(__file__).resolve().parents[1] / 'fixture_supervisor.py'


LAUNCH = '''import importlib.util, sys
spec=importlib.util.spec_from_file_location('supervisor', sys.argv[1])
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
sys.exit(m.supervise([sys.executable, '-c', sys.argv[2]], [sys.executable, '-c', sys.argv[3]], verify_containment=lambda phase, pid: True))
'''

class SupervisorSignalTest(unittest.TestCase):
    def test_sigterm_during_worker_still_runs_recovery_and_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            ready = Path(temporary) / 'ready'
            recovered = Path(temporary) / 'recovered'
            worker = f"from pathlib import Path; import time; Path({str(ready)!r}).touch(); time.sleep(60)"
            recovery = f"from pathlib import Path; Path({str(recovered)!r}).touch()"
            process = subprocess.Popen([sys.executable, '-c', LAUNCH, str(SUPERVISOR), worker, recovery], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 10
                while not ready.exists() and time.monotonic() < deadline:
                    if process.poll() is not None:
                        self.fail('supervisor exited before worker started')
                    time.sleep(.02)
                self.assertTrue(ready.exists())
                os.kill(process.pid, signal.SIGTERM)
                self.assertNotEqual(0, process.wait(timeout=10))
                self.assertTrue(recovered.exists())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()

    def test_cancel_entire_supervisor_group_runs_separate_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            ready = Path(temporary) / 'ready'
            recovered = Path(temporary) / 'recovered'
            worker = f"from pathlib import Path; import time; Path({str(ready)!r}).touch(); time.sleep(60)"
            recovery = f"from pathlib import Path; Path({str(recovered)!r}).touch()"
            process = subprocess.Popen([sys.executable, '-c', LAUNCH, str(SUPERVISOR), worker, recovery],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                deadline = time.monotonic() + 10
                while not ready.exists() and time.monotonic() < deadline:
                    if process.poll() is not None: self.fail('worker never started')
                    time.sleep(.02)
                self.assertTrue(ready.exists())
                os.killpg(process.pid, signal.SIGTERM)
                self.assertNotEqual(0, process.wait(timeout=10))
                self.assertTrue(recovered.exists())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()

    def test_sigterm_during_recovery_does_not_abandon_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            started = Path(temporary) / 'started'
            recovered = Path(temporary) / 'recovered'
            recovery = (f"from pathlib import Path; import time; Path({str(started)!r}).touch(); "
                        f"time.sleep(.5); Path({str(recovered)!r}).touch()")
            process = subprocess.Popen([sys.executable, '-c', LAUNCH, str(SUPERVISOR), 'pass', recovery],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 10
                while not started.exists() and time.monotonic() < deadline:
                    if process.poll() is not None: self.fail('recovery never started')
                    time.sleep(.02)
                self.assertTrue(started.exists())
                os.kill(process.pid, signal.SIGTERM)
                self.assertNotEqual(0, process.wait(timeout=10))
                self.assertTrue(recovered.exists())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()

    def test_missing_containment_proof_prevents_execution(self):
        code = """import importlib.util, sys
spec=importlib.util.spec_from_file_location('supervisor',sys.argv[1]); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
sys.exit(m.supervise([sys.executable,'-c',sys.argv[2]],[sys.executable,'-c',sys.argv[3]]))"""
        with tempfile.TemporaryDirectory() as temporary:
            created = Path(temporary) / 'created'
            command = f"from pathlib import Path; Path({str(created)!r}).touch()"
            result = subprocess.run([sys.executable, '-c', code, str(SUPERVISOR), command, command], timeout=10)
            self.assertNotEqual(0, result.returncode)
            self.assertFalse(created.exists())

    def test_worker_grandchild_prevents_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            child_pid = Path(temporary) / 'pid'
            recovered = Path(temporary) / 'recovered'
            worker = ("import subprocess,sys; from pathlib import Path; "
                      f"p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); Path({str(child_pid)!r}).write_text(str(p.pid))")
            recovery = f"from pathlib import Path; Path({str(recovered)!r}).touch()"
            try:
                result = subprocess.run([sys.executable, '-c', LAUNCH, str(SUPERVISOR), worker, recovery], timeout=10)
                self.assertNotEqual(0, result.returncode)
                self.assertFalse(recovered.exists())
            finally:
                if child_pid.exists():
                    try: os.kill(int(child_pid.read_text()), signal.SIGKILL)
                    except ProcessLookupError: pass

    def test_recovery_grandchild_prevents_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            child_pid = Path(temporary) / 'pid'
            recovery = ("import subprocess,sys; from pathlib import Path; "
                        f"p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); Path({str(child_pid)!r}).write_text(str(p.pid))")
            try:
                result = subprocess.run([sys.executable, '-c', LAUNCH, str(SUPERVISOR), 'pass', recovery], timeout=10)
                self.assertNotEqual(0, result.returncode)
            finally:
                if child_pid.exists():
                    try: os.kill(int(child_pid.read_text()), signal.SIGKILL)
                    except ProcessLookupError: pass

    def test_failed_worker_and_failed_recovery_are_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            recovered = Path(temporary) / 'recovered'
            recovery = f"from pathlib import Path; Path({str(recovered)!r}).touch()"
            cmd = [sys.executable, '-c', LAUNCH, str(SUPERVISOR), 'raise SystemExit(7)', recovery]
            result = subprocess.run(cmd, timeout=10, capture_output=True)
            self.assertNotEqual(0, result.returncode)
            self.assertTrue(recovered.exists())
            cmd[-1] = 'raise SystemExit(8)'
            self.assertNotEqual(0, subprocess.run(cmd, timeout=10, capture_output=True).returncode)
