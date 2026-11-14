"""Bounded initial-health observations; model snapshots are not native proof."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock
import test_surveyctl as fixtures


class InitialHealthEvidenceTest(unittest.TestCase):
    def setUp(self):
        f=fixtures.SurveyctlTest();f.setUp();self.addCleanup(f.doCleanups)
        self.f=f
        spec=importlib.util.spec_from_file_location('initial_health_native',
            Path(__file__).parent/'integration/owned_maintenance_native.py')
        self.native=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.native)

    def test_running_init_is_still_rejected_but_reports_state_and_elapsed(self):
        class Runner(fixtures.FakeRunner):
            def run(self,command,**kw):
                result=super().run(command,**kw)
                if 'ps' in command:
                    rows=json.loads(result.stdout)
                    for row in rows:
                        row['Env']=['SECRET=must-never-appear']
                        if row['Service']=='engine-init': row['State']='running'
                    result.stdout=json.dumps(rows)
                return result
        runner=Runner();runner.fixture_running=True
        manager=self.f.manager(runner=runner)
        with mock.patch.object(self.f.module.time,'monotonic',side_effect=[10,10.025]), \
                mock.patch.object(self.f.module.time,'sleep') as sleep:
            with self.assertRaises(self.f.module.RuntimeHealthError) as caught:
                manager._wait_healthy(['docker','compose'],'fixture',timeout=120)
        sleep.assert_not_called()  # No new retry or relaxed health rule.
        value=self.native.bounded_health_failure(caught.exception,self.f.module.LONG_RUNNING_SERVICES)
        self.assertEqual({'state':'running','exitCode':0},value['engineInit'])
        self.assertEqual(25,value['elapsedMs'])
        self.assertTrue(all(row=={'state':'running','health':'healthy'} for row in value['services'].values()))
        self.assertNotIn('must-never-appear',json.dumps(value))

    def test_unhealthy_and_invalid_init_values_remain_failure_and_are_redacted(self):
        runner=fixtures.FakeRunner(unhealthy_service='engine',engine_init_exit_code=False)
        runner.fixture_running=True
        manager=self.f.manager(runner=runner)
        with self.assertRaises(self.f.module.RuntimeHealthError) as caught:
            manager._wait_healthy(['docker','compose'],'fixture')
        value=self.native.bounded_health_failure(caught.exception,self.f.module.LONG_RUNNING_SERVICES)
        self.assertEqual('unhealthy',value['services']['engine']['health'])
        self.assertIsNone(value['engineInit']['exitCode'])

    def test_pending_deadline_keeps_last_snapshot_without_extending_timeout(self):
        runner=fixtures.FakeRunner();runner.fixture_running=True
        manager=self.f.manager(runner=runner)
        original=manager._assert_healthy
        def pending(*args):
            try: original(*args)
            except Exception: raise
            error=self.f.module.StartupPending('fixture')
            error.health_evidence={'services':{'edge':{'state':'running','health':'starting'}},
                'engineInit':{'state':'exited','exitCode':0}}
            raise error
        with mock.patch.object(manager,'_assert_healthy',side_effect=pending), \
                mock.patch.object(self.f.module.time,'sleep') as sleep:
            with self.assertRaises(self.f.module.RuntimeHealthError) as caught:
                manager._wait_healthy(['docker','compose'],'fixture',timeout=0)
        sleep.assert_not_called()
        value=self.native.bounded_health_failure(caught.exception,self.f.module.LONG_RUNNING_SERVICES)
        self.assertEqual('starting',value['services']['edge']['health'])

    def test_unknown_or_unbounded_projection_is_not_exported(self):
        error=RuntimeError('must-never-appear')
        self.assertIsNone(self.native.bounded_health_failure(error,{'edge'}))
        error.health_evidence={'services':{'foreign-secret':{'state':'running','health':'healthy'}},
            'engineInit':{'state':'exited','exitCode':0}}
        error.health_wait_elapsed_ms=1
        self.assertIsNone(self.native.bounded_health_failure(error,{'edge'}))
        valid={'services':{'edge':{'state':'running','health':'healthy'}},
            'engineInit':{'state':'exited','exitCode':0}}
        for extra in ({'raw':'must-never-appear'}, {'services':{'edge':{'state':'running','health':'secret'}}},
                {'engineInit':{'state':'exited','exitCode':True}},
                {'engineInit':{'state':'exited','exitCode':256}}):
            error.health_evidence={**valid,**extra}
            self.assertIsNone(self.native.bounded_health_failure(error,{'edge'}))
        error.health_evidence=valid
        for elapsed in (True,-1,3600001,'secret'):
            error.health_wait_elapsed_ms=elapsed
            self.assertIsNone(self.native.bounded_health_failure(error,{'edge'}))


if __name__=='__main__': unittest.main()
