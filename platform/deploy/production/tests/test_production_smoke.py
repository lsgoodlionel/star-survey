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
        def runner(command, **kwargs):
            return SimpleNamespace(stdout='SMOKE_BRIDGE_JSON:{"rawBody":"private-fixture"}')
        with self.assertRaises(ValueError):
            self.module.bridge_diagnostic(['docker', 'compose'], 123, runner=runner)

    def test_bridge_diagnostic_retains_counts_and_status_without_payload(self):
        from types import SimpleNamespace
        def runner(command, **kwargs):
            self.assertEqual(30, kwargs.get('timeout'))
            self.assertIn('SMOKE_SURVEY_ID=123', command)
            self.assertNotIn('/opt/survey/run-with-secrets.sh', command)
            return SimpleNamespace(stdout='SMOKE_BRIDGE_JSON:{"completedResponses":1,"recordedEvents":2,"pendingEvents":2,"deliveredEvents":0,"phase":"completed","errorCategory":null,"httpStatus":null}')
        result = self.module.bridge_diagnostic(['docker', 'compose'], 123, runner=runner)
        self.assertIsNone(result['httpStatus'])
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
putenv('SMOKE_SURVEY_ID=123');
class FixtureDb {
 public $tablePrefix='lime_'; public function quoteTableName($t){return $t;} public function getSchema(){return $this;} public function getTable($t){return $t==='lime_responses_123'?new stdClass:null;}
 public function createCommand($q){return new class($q){private $q;public function __construct($q){$this->q=$q;}public function bindValue($k,$v){return $this;}public function queryScalar(){return strpos($this->q,'submitdate')!==false?1:(strpos($this->q,'IS NOT NULL')!==false?0:2);}};}
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
        report = json.loads(result.stdout.strip().removeprefix('SMOKE_BRIDGE_JSON:'))
        self.assertIsNone(report['httpStatus'])
        self.assertEqual(1, report['completedResponses'])
        self.assertEqual(2, report['pendingEvents'])
        self.assertEqual(0, report['deliveredEvents'])
        self.assertNotIn('private-fixture', result.stdout)
        self.assertNotIn('sensitive response', result.stdout)

    def test_scoped_observation_runs_before_cleanup_and_unavailable_is_explicit(self):
        import json
        import tempfile
        calls = []
        class Base:
            compose = ['docker', 'compose']
            def _close_engine_survey(self, instance, sid):
                calls.append('close')
        def collector(compose, sid):
            calls.append(sid)
            raise ValueError('fixture invalid report')
        with tempfile.TemporaryDirectory() as temporary:
            observed = self.module.observed_probe_class(Base, Path(temporary), collector)
            observed()._close_engine_survey('fixture', 123)
            report = json.loads(Path(temporary, 'bridge-diagnostic.json').read_text())
        self.assertEqual([123, 'close'], calls)
        self.assertEqual('unavailable', report['diagnosticStatus'])
        self.assertEqual('current-probe-before-cleanup', report['scope'])
        self.assertNotIn('123', str(report))

    def test_diagnostic_write_failure_does_not_interrupt_full_cleanup(self):
        from unittest.mock import patch
        calls = []
        class Base:
            compose = []
            def _close_engine_survey(self, instance, sid):
                calls.append('engine-close')
            def run(self):
                self._close_engine_survey('fixture', 123)
                calls.append('project-archive')
        observed = self.module.observed_probe_class(Base, Path('/unused'), lambda *_: {})
        with patch.object(Path, 'write_text', side_effect=OSError('fixture write failure')):
            observed().run()
        self.assertEqual(['engine-close', 'project-archive'], calls)

    def test_diagnostic_timeout_still_allows_cleanup(self):
        import subprocess
        import tempfile
        import json
        calls = []
        class Base:
            compose = []
            def _close_engine_survey(self, instance, sid):
                calls.append('engine-close')
        def collector(*_):
            raise subprocess.TimeoutExpired('fixture', 30)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            self.module.observed_probe_class(Base, path, collector)()._close_engine_survey('fixture', 123)
            self.assertEqual('unavailable', json.loads((path / 'bridge-diagnostic.json').read_text())['diagnosticStatus'])
        self.assertEqual(['engine-close'], calls)

    def test_unavailable_or_incomplete_bridge_evidence_never_passes(self):
        import json
        import tempfile
        for value in ({'diagnosticStatus': 'unavailable'},
                      {'diagnosticStatus': 'captured', 'phase': 'completed', 'errorCategory': None,
                       'completedResponses': 0, 'deliveredEvents': 0}):
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary)
                (path / 'bridge-diagnostic.json').write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    self.module.require_bridge_evidence(path)

    def test_real_yii_console_discovers_bridge_command(self):
        import base64
        import os
        import subprocess
        root = os.environ.get('TEST_YII_ROOT', str(SOURCE.parents[3]))
        encode = lambda value: base64.b64encode(value.encode()).decode()
        script = "require base64_decode('" + encode(root + '/vendor/yiisoft/yii/framework/yii.php') + "');"
        script += "$app=Yii::createConsoleApplication(['basePath'=>'/tmp']);"
        script += "$app->commandRunner->addCommands(base64_decode('" + encode(root + '/platform/deploy/production/smoke-commands') + "'));"
        script += "$app->commandRunner->run(['console','smokeBridge']);"
        result = subprocess.run([os.environ.get('TEST_PHP', 'php'), '-r', script], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(1, sum(line.startswith('SMOKE_BRIDGE_JSON:') for line in result.stdout.splitlines()))
