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

    def test_specific_bridge_runs_scan_and_relay_even_if_scan_fails(self):
        command = Path(__file__).resolve().parents[1] / "init" / "ProductionRelayCommand.php"
        program = r'''
class CConsoleCommand {}
class MjyPlatformBridge {
    public $scan = 0; public $relay = 0; public $fail = false;
    function runCompletionScan() { $this->scan++; if ($this->fail) throw new Exception("secret"); }
    function relayEvents() { $this->relay++; }
}
class Plugin { static function model() { return new self; }
    function findByAttributes($x) { return (object)["id" => 4]; } }
class Manager { public $bridge; function __construct() { $this->bridge = new MjyPlatformBridge; }
    function loadPlugin($name, $id) { if ($name !== "MjyPlatformBridge" || $id !== 4) exit(7); return $this->bridge; } }
class Yii { static $manager; static function app() { return new self; }
    function getPluginManager() { return self::$manager; } }
Yii::$manager = new Manager;
require $argv[1];
$runner = new ProductionRelayCommand;
if ($runner->actionRun() !== 0 || Yii::$manager->bridge->scan !== 1 || Yii::$manager->bridge->relay !== 1) exit(8);
Yii::$manager->bridge->fail = true;
if ($runner->actionRun() !== 1 || Yii::$manager->bridge->scan !== 2 || Yii::$manager->bridge->relay !== 2) exit(9);
'''
        result = subprocess.run([os.environ.get("TEST_PHP", "php"), "-r", program, str(command)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("event reconciliation failed", result.stderr)
        self.assertNotIn("secret", result.stderr)


if __name__ == "__main__":
    unittest.main()
