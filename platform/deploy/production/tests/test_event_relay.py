import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "init" / "event-relay.sh"


class EventRelayLoopTest(unittest.TestCase):
    def test_failed_invocation_retries_and_term_stops_loop(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            calls = root / "calls"
            php = root / "php"
            php.write_text('#!/bin/sh\nprintf "call\\n" >> "$TEST_CALLS"\nexit 1\n')
            php.chmod(0o755)
            sleep = root / "sleep"
            sleep.write_text('#!/bin/sh\nexec /bin/sleep 0.05\n')
            sleep.chmod(0o755)
            env = {"PATH": f"{root}:/usr/bin:/bin", "TEST_CALLS": str(calls),
                   "SURVEY_RELAY_HEARTBEAT": str(root / "heartbeat")}
            process = subprocess.Popen(["/bin/sh", str(SCRIPT)], env=env,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       start_new_session=True)
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if calls.exists() and len(calls.read_text().splitlines()) >= 2:
                        break
                    time.sleep(0.02)
                else:
                    self.fail("failed cron invocation was not retried")
                self.assertTrue((root / "heartbeat").exists())
                process.send_signal(signal.SIGTERM)
                self.assertEqual(0, process.wait(timeout=3))
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
