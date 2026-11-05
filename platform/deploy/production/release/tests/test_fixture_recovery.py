import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / 'fixture_recovery.py'
spec = importlib.util.spec_from_file_location('fixture_recovery', PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FixtureRecoveryTest(unittest.TestCase):
    def test_recovery_order_and_binding_before_every_action(self):
        calls = []
        def step(name):
            return lambda: calls.append(name)
        module.recover_fixture(step('bind'), step('revoke'), step('restore'), step('verify'), step('doctor'))
        self.assertEqual(['bind', 'revoke', 'bind', 'restore', 'bind', 'verify', 'bind', 'doctor'], calls)

    def test_failures_do_not_suppress_later_independent_recovery(self):
        for failed in ('revoke', 'restore', 'verify', 'doctor'):
            calls = []
            def action(name):
                def run():
                    calls.append(name)
                    if name == failed:
                        raise RuntimeError('sensitive details')
                return run
            with self.subTest(failed=failed), self.assertRaises(module.RecoveryError) as raised:
                module.recover_fixture(action('bind'), *(action(name) for name in ('revoke', 'restore', 'verify', 'doctor')))
            expected = ['bind', 'revoke', 'bind', 'restore']
            if failed != 'restore':
                expected += ['bind', 'verify']
                if failed != 'verify':
                    expected += ['bind', 'doctor']
            self.assertEqual(expected, calls)
            self.assertNotIn('sensitive details', str(raised.exception))

    def test_binding_failure_never_calls_the_associated_mutation(self):
        calls = []
        def bind():
            calls.append('bind')
            raise ValueError('unsafe')
        def operation():
            calls.append('operation')
        with self.assertRaises(module.RecoveryError):
            module.recover_fixture(bind, operation, operation, operation, operation)
        self.assertEqual(['bind'], calls)

    def test_interruption_still_attempts_remaining_recovery(self):
        calls = []
        def revoke():
            calls.append('revoke')
            raise KeyboardInterrupt()
        def step(name):
            return lambda: calls.append(name)
        with self.assertRaises(module.RecoveryError):
            module.recover_fixture(step('bind'), revoke, step('restore'), step('verify'), step('doctor'))
        self.assertEqual(['bind', 'revoke', 'bind', 'restore', 'bind', 'verify', 'bind', 'doctor'], calls)


if __name__ == '__main__':
    unittest.main()

class FixtureSupervisorTest(unittest.TestCase):
    def test_ambiguous_provision_always_triggers_recovery_and_is_redacted(self):
        calls = []
        def provision():
            calls.append('provision')
            raise RuntimeError('sensitive database output')
        with self.assertRaises(module.FixtureExecutionError) as caught:
            module.execute_fixture(provision, lambda: calls.append('journey'), lambda: calls.append('recovery'))
        self.assertEqual(['provision', 'recovery'], calls)
        self.assertNotIn('sensitive', str(caught.exception))

    def test_success_does_not_precede_recovery(self):
        calls = []
        module.execute_fixture(*(lambda name=name: calls.append(name) for name in ('provision', 'journey', 'recovery')))
        self.assertEqual(['provision', 'journey', 'recovery'], calls)

    def test_interrupt_and_failed_cleanup_cannot_report_success(self):
        for interruption in (KeyboardInterrupt, SystemExit):
            calls = []
            def journey():
                calls.append('journey')
                raise interruption()
            def recovery():
                calls.append('recovery')
                raise ValueError('secret')
            with self.subTest(interruption=interruption), self.assertRaises(module.FixtureExecutionError) as caught:
                module.execute_fixture(lambda: calls.append('provision'), journey, recovery)
            self.assertEqual(['provision', 'journey', 'recovery'], calls)
            self.assertNotIn('secret', str(caught.exception))

class MarkedFixtureSupervisorTest(unittest.TestCase):
    def test_mark_before_provision_and_clear_only_after_verified_recovery(self):
        calls = []
        module.execute_marked_fixture(*(lambda name=name: calls.append(name)
                                       for name in ('mark', 'provision', 'journey', 'recovery', 'clear')))
        self.assertEqual(['mark', 'provision', 'journey', 'recovery', 'clear'], calls)

    def test_failed_marker_prevents_mutation_and_failed_recovery_retains_marker(self):
        calls = []
        def fail():
            raise ValueError('private')
        with self.assertRaises(module.FixtureExecutionError):
            module.execute_marked_fixture(fail, lambda: calls.append('provision'), lambda: None,
                                          lambda: None, lambda: calls.append('clear'))
        self.assertEqual([], calls)
        with self.assertRaises(module.FixtureExecutionError):
            module.execute_marked_fixture(lambda: calls.append('mark'), lambda: calls.append('provision'),
                                          lambda: None, fail, lambda: calls.append('clear'))
        self.assertEqual(['mark', 'provision'], calls)

    def test_journey_failure_can_clear_only_after_successful_recovery(self):
        calls = []
        def fail():
            raise ValueError('private')
        with self.assertRaises(module.FixtureExecutionError):
            module.execute_marked_fixture(lambda: calls.append('mark'), lambda: calls.append('provision'), fail,
                                          lambda: calls.append('recovery'), lambda: calls.append('clear'))
        self.assertEqual(['mark', 'provision', 'recovery', 'clear'], calls)
