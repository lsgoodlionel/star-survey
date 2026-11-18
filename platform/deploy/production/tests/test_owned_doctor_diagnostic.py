"""Fail-closed diagnostic projection tests; all inputs are fictitious."""
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

class OwnedDoctorDiagnosticTest(unittest.TestCase):
    def setUp(self):
        self.owned = importlib.import_module('owned_maintenance')
        self.ctl = importlib.import_module('surveyctl')

    def test_last_observed_exception_is_fixed_projection(self):
        op = SimpleNamespace()
        doctor = object.__new__(self.owned.OwnedDoctor)
        doctor.operation = op
        failure = PermissionError('private-path token=secret-value')
        def fail(): raise failure
        with self.assertRaises(PermissionError) as caught:
            doctor._stage('migrations', fail)
        self.assertIs(failure, caught.exception)
        self.assertEqual({'id':'doctor-last-observed-migrations', 'status':'blocked', 'summary':'category=permission'},
                         self.ctl._safe_doctor_check(op))

    def test_success_clears_old_failure_before_later_outer_failure(self):
        op = SimpleNamespace(_doctor_diagnostic=('migrations','permission'))
        doctor = object.__new__(self.owned.OwnedDoctor)
        doctor.operation = op
        self.assertEqual('ok', doctor._stage('report', lambda:'ok'))
        self.assertEqual({'id':'doctor-last-observed-unknown','status':'blocked','summary':'category=unknown'},
                         self.ctl._safe_doctor_check(op))

    def test_pre_stage_failure_clears_previous_diagnostic(self):
        op = object.__new__(self.owned.OwnedMaintenance)
        op._doctor_diagnostic = ('migrations','permission')
        def fail(kind, effect): raise RuntimeError('outer failure')
        op._perform = fail
        with self.assertRaises(RuntimeError): op.doctor()
        self.assertEqual('doctor-last-observed-unknown', self.ctl._safe_doctor_check(op)['id'])

    def test_stage_failure_followed_by_probe_failure_is_last_observed_only(self):
        op = object.__new__(self.owned.OwnedMaintenance)
        op.uncertain, op.held = False, []
        op.manager = SimpleNamespace(_owned_contexts=[op])
        def probe(): raise OSError('probe-private')
        op.runtime = SimpleNamespace(probe=probe, protocol=SimpleNamespace(mutate=lambda kind, fn:fn()))
        doctor = object.__new__(self.owned.OwnedDoctor); doctor.operation = op
        first = PermissionError('stage-private')
        def failing_stage(): raise first
        with self.assertRaises(OSError) as caught:
            op._perform('doctor', lambda:doctor._stage('migrations', failing_stage))
        self.assertEqual('probe-private',str(caught.exception))
        self.assertIs(first,caught.exception.__context__)
        self.assertEqual({'id':'doctor-last-observed-migrations','status':'blocked','summary':'category=permission'},
                         self.ctl._safe_doctor_check(op))

    def test_outer_failure_after_success_cannot_blame_successful_stage(self):
        op = object.__new__(self.owned.OwnedMaintenance)
        op.uncertain, op.held = False, []
        op.manager = SimpleNamespace(_owned_contexts=[op])
        def mutate(kind,fn):
            fn()
            raise OSError('post-effect-private')
        op.runtime = SimpleNamespace(protocol=SimpleNamespace(mutate=mutate))
        doctor = object.__new__(self.owned.OwnedDoctor); doctor.operation = op
        with self.assertRaises(OSError):
            op._perform('doctor',lambda:doctor._stage('report',lambda:'ok'))
        self.assertEqual('doctor-last-observed-unknown',self.ctl._safe_doctor_check(op)['id'])

    def test_malformed_or_sensitive_values_fail_closed(self):
        values = [None, ('migrations','secret-value'), ('secret /private/path','permission'),
                  ['migrations','permission'], ('migrations',), ('migrations','permission','extra')]
        class Broken:
            @property
            def _doctor_diagnostic(self): raise RuntimeError('secret-value')
        for value in values:
            with self.subTest(value=type(value).__name__):
                self.assertEqual('doctor-last-observed-unknown',
                                 self.ctl._safe_doctor_check(SimpleNamespace(_doctor_diagnostic=value))['id'])
        self.assertEqual('category=unknown',self.ctl._safe_doctor_check(Broken())['summary'])

    def test_projection_matches_existing_doctor_report_schema(self):
        from importlib.util import spec_from_file_location, module_from_spec
        path = Path(__file__).resolve().parents[1] / 'release' / 'native_acceptance_evidence.py'
        spec = spec_from_file_location('fixture_native_evidence', path)
        evidence = module_from_spec(spec); spec.loader.exec_module(evidence)
        check = self.ctl._safe_doctor_check(SimpleNamespace(_doctor_diagnostic=('migrations','permission')))
        evidence.validate_doctor({'schemaVersion':1,'status':'blocked','checks':[
            {'id':'doctor-runtime','status':'blocked','summary':'controlled diagnostic operation failed; preserve its receipts and marker'},check]})

    def test_actual_doctor_sequence_marks_failing_stage(self):
        op = SimpleNamespace()
        doctor = object.__new__(self.owned.OwnedDoctor)
        doctor.operation = op
        doctor._load_installation = lambda: ('0.2.0','1','fixture.invalid',['compose'])
        doctor._config_checks = lambda compose: ()
        def fail(compose): raise FileNotFoundError('private-path')
        doctor._runtime_exposure_check = fail
        with self.assertRaises(FileNotFoundError): doctor.run()
        self.assertEqual({'id':'doctor-last-observed-runtime-exposure','status':'blocked','summary':'category=missing'},
                         self.ctl._safe_doctor_check(op))

if __name__ == '__main__': unittest.main()
