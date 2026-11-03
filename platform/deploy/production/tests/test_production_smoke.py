import importlib.util
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'production_smoke.py'


class ProductionSmokeTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('production_smoke', SOURCE)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_refuses_local_self_hosted_or_arm_execution(self):
        for env in ({}, {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'self-hosted', 'RUNNER_ARCH': 'X64'},
                    {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted', 'RUNNER_ARCH': 'ARM64'}):
            with self.subTest(env=env), self.assertRaises(ValueError):
                self.module.require_disposable_runner(env)

    def test_accepts_only_explicit_hosted_amd64_runner(self):
        self.module.require_disposable_runner({'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted', 'RUNNER_ARCH': 'X64'})

    def test_cleanup_failure_cannot_be_reported_as_pass(self):
        self.assertEqual('failed', self.module.overall_status(True, False))
        self.assertEqual('failed', self.module.overall_status(False, True))
        self.assertEqual('passed', self.module.overall_status(True, True))

    def test_compose_cleanup_failure_still_attempts_registry_cleanup(self):
        calls = []
        def compose():
            calls.append('compose')
            raise RuntimeError('fixture failure')
        def registry():
            calls.append('registry')
        self.assertFalse(self.module.attempt_cleanups([compose, registry]))
        self.assertEqual(['compose', 'registry'], calls)

    def test_created_or_exited_container_blocks_collision_and_cleanup(self):
        from types import SimpleNamespace
        def fake(command):
            names = 'survey-production-engine-1\n' if command[1] == 'container' and '--all' in command else ''
            return SimpleNamespace(stdout=names)
        for prefixes in (('survey-production', 'survey-smoke-registry'), ('survey-production',)):
            with self.subTest(prefixes=prefixes), self.assertRaises(ValueError):
                self.module.assert_resources_absent(prefixes, runner=fake)

    def test_engine_subprobes_do_not_copy_cli_output_or_http_body(self):
        import subprocess
        from types import SimpleNamespace
        def fake(command):
            if command[-1] == 'status':
                raise subprocess.CalledProcessError(1, command, output='secret=fixture-private')
            return SimpleNamespace(stdout='{"httpStatus":503,"requestCompleted":true,"bodyHasError":false,"edgeResolved":false,"gatewayResolved":true}')
        result = self.module.engine_subprobes('fixture', runner=fake)
        self.assertEqual(503, result['httpStatus'])
        self.assertEqual(1, result['initStatusExitCode'])
        self.assertNotIn('fixture-private', str(result))

    def test_rpc_precheck_uses_secret_file_wrapper_and_preserves_failure(self):
        import subprocess
        commands = []
        def failing(command):
            commands.append(command)
            raise subprocess.CalledProcessError(1, command)
        with self.assertRaises(subprocess.CalledProcessError):
            self.module.check_gateway_rpc(['docker','compose'], runner=failing)
        command = commands[0]
        self.assertIn('/opt/survey/run-with-secrets.sh', command)
        self.assertIn('PUBGW_ENGINE_ADMIN_PASSWORD=/run/secrets/pubgw_engine_admin_password', command)
        self.assertIn('PUBGW_ENGINE_CHANNEL_SECRET=/run/secrets/pubgw_engine_channel_secret', command)

    def test_bridge_diagnostic_rejects_extra_response_data(self):
        from types import SimpleNamespace
        def runner(command):
            return SimpleNamespace(stdout='{"rawBody":"private-fixture"}')
        with self.assertRaises(ValueError):
            self.module.bridge_diagnostic(['docker', 'compose'], runner=runner)

    def test_bridge_diagnostic_retains_counts_and_status_without_payload(self):
        from types import SimpleNamespace
        def runner(command):
            self.assertIn('/opt/survey/run-with-secrets.sh', command)
            self.assertIn('MJY_PLATFORM_EVENTS_SECRET=/run/secrets/engine_instance_events_secret', command)
            return SimpleNamespace(stdout='{"completedResponses":1,"recordedEvents":2,"pendingEvents":2,"deliveredEvents":0,"phase":"completed","errorCategory":"relay-rejected","httpStatus":403}')
        result = self.module.bridge_diagnostic(['docker', 'compose'], runner=runner)
        self.assertEqual(403, result['httpStatus'])
        self.assertEqual(1, result['completedResponses'])

    def test_actual_php_bridge_report_drops_log_payload(self):
        import base64
        import json
        import os
        import subprocess
        source = SOURCE.parent.joinpath('smoke-commands/SmokeBridgeCommand.php').read_bytes()
        script = r'''
class CConsoleCommand {}
class Yii { public static $app; public static function app(){return self::$app;} }
class Survey { public static function model(){return new self;} public function findAllByAttributes($a){return [];} }
class FixtureDb {
 public $tablePrefix='lime_'; public function quoteTableName($t){return $t;}
 public function createCommand($q){return new class($q){private $q;public function __construct($q){$this->q=$q;}public function queryScalar(){return strpos($this->q,'IS NOT NULL')!==false?0:2;}};}
}
class FixtureApp { public $runtimePath; public function getDb(){return new FixtureDb;} }
$dir=sys_get_temp_dir().'/bridge-fixture-'.bin2hex(random_bytes(8));mkdir($dir);
file_put_contents($dir.'/application.log','secret=private-fixture Event delivery rejected with HTTP 403: sensitive response');
Yii::$app=new FixtureApp;Yii::$app->runtimePath=$dir;
eval('?>'.base64_decode(''' + "'" + base64.b64encode(source).decode() + "'" + r'''));
(new SmokeBridgeCommand)->actionIndex();
unlink($dir.'/application.log');rmdir($dir);
'''
        result = subprocess.run([os.environ.get('TEST_PHP', 'php'), '-r', script], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(403, report['httpStatus'])
        self.assertEqual(2, report['pendingEvents'])
        self.assertEqual(0, report['deliveredEvents'])
        self.assertNotIn('private-fixture', result.stdout)
        self.assertNotIn('sensitive response', result.stdout)
