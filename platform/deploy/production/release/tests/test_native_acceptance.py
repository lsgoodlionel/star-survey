from __future__ import annotations

import hashlib
import csv
import io
import zipfile
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[5]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "release.yml"
SCRIPT_PATH = ROOT / "platform" / "deploy" / "production" / "release" / "native_acceptance.sh"
EVIDENCE_HELPER_PATH = ROOT / "platform" / "deploy" / "production" / "release" / "native_acceptance_evidence.py"
BASELINE_POLICY_PATH = ROOT / ".github" / "release" / "baseline-policy.json"
UPGRADE_HELPER_PATH = EVIDENCE_HELPER_PATH


def load_workflow() -> dict:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    if True in workflow and "on" not in workflow:
        workflow["on"] = workflow.pop(True)
    return workflow


def job_text(job: dict) -> str:
    return json.dumps(job, sort_keys=True)


def shell_without_heredocs(source: str) -> str:
    kept = []
    delimiter = None
    for line in source.splitlines():
        if delimiter is not None:
            if line.strip() == delimiter:
                delimiter = None
            continue
        match = re.search(r"<<-?['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", line)
        kept.append(line)
        if match:
            delimiter = match.group(1)
    return "\n".join(kept)


REQUIRED_LIFECYCLE_COMMANDS = (
    'run_ctl install',
    'run_ctl setup-probe',
    'write_doctor_evidence "doctor-installed"',
    'run_ctl backup',
    'python3 "$evidence_helper" upgrade-data-probe',
    'write_doctor_evidence "doctor-upgraded"',
    'run_ctl restore',
    'write_doctor_evidence "doctor-restored"',
    'run_ctl uninstall',
    'acceptance_status="passed"',
)


def assert_native_acceptance_contract(testcase: unittest.TestCase, source: str) -> None:
    shell = shell_without_heredocs(source)
    identifiers = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", shell)
    testcase.assertFalse(
        [word for word in identifiers if re.search(r"(?i)(skip|bypass|disable|opt_?out)", word)],
        "native acceptance contains a success-bypass identifier",
    )
    testcase.assertNotIn("||", shell)
    testcase.assertNotRegex(shell, r"(?m)(?:^|[;&]\s*)continue(?:\s|;|$)")
    testcase.assertNotRegex(shell, r"(?m)\b(?:exit|return)\s+0\b")
    testcase.assertNotRegex(shell, r"(?m)\b(?:exit|return)(?=\s*(?:;|$))")
    testcase.assertNotRegex(shell, r"(?m)\bexec\b")
    exits = re.findall(r"(?m)\bexit\s+([^;\n]+)", shell)
    testcase.assertEqual(["2", "2", '"$result"'], exits, "native acceptance exit propagation changed")
    testcase.assertEqual(1, shell.count('acceptance_status="passed"'))
    testcase.assertIn("trap collect_evidence EXIT", shell)
    testcase.assertIn("local result=$?", shell)
    testcase.assertIn('exit "$result"', shell)
    positions = []
    for command in REQUIRED_LIFECYCLE_COMMANDS:
        expected_count = 2 if command == "run_ctl uninstall" else 1
        testcase.assertEqual(expected_count, shell.count(command), f"required lifecycle command count changed: {command}")
        positions.append(shell.rindex(command))
    testcase.assertEqual(sorted(positions), positions, "native lifecycle command order changed")


def assert_native_workflow_invocation(testcase: unittest.TestCase, workflow: dict) -> None:
    job = workflow["jobs"]["native-acceptance"]
    step = next(item for item in job["steps"] if item.get("name") == "Execute real clean-host install upgrade restore and uninstall")
    testcase.assertEqual(
        "sudo -E NATIVE_ACCEPTANCE_REAL=1 bash orchestrator/release/native_acceptance.sh",
        step["run"],
    )


class NativeAcceptanceWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = load_workflow()
        cls.workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")

    def test_host_readiness_precedes_immutable_lifecycle(self):
        steps = self.workflow["jobs"]["native-acceptance"]["steps"]
        readiness = next(step for step in steps if step.get("name") == "Require ready native systemd and Docker")
        lifecycle = next(step for step in steps if step.get("name") == "Execute real clean-host install upgrade restore and uninstall")
        self.assertLess(steps.index(readiness), steps.index(lifecycle))
        self.assertEqual(4, readiness["timeout-minutes"])
        script = readiness["run"]
        for required in ("/run/systemd/system", "systemctl is-system-running --wait", "running|degraded", "docker info", "exit 1"):
            self.assertIn(required, script)
        self.assertNotIn("|| true", script)
        assert_native_workflow_invocation(self, self.workflow)

    def test_matrix_uses_all_four_official_native_ubuntu_labels(self):
        job = self.workflow["jobs"]["native-acceptance"]
        matrix = job["strategy"]["matrix"]["include"]
        self.assertEqual(
            {
                ("ubuntu-22.04", "22.04", "amd64"),
                ("ubuntu-24.04", "24.04", "amd64"),
                ("ubuntu-22.04-arm", "22.04", "arm64"),
                ("ubuntu-24.04-arm", "24.04", "arm64"),
            },
            {(entry["runner"], entry["ubuntu"], entry["architecture"]) for entry in matrix},
        )
        self.assertEqual("${{ matrix.runner }}", job["runs-on"])
        self.assertFalse(job["strategy"].get("fail-fast", True))
        self.assertNotIn("continue-on-error", job_text(job))

    def test_candidate_is_built_once_and_every_matrix_job_consumes_same_identity(self):
        jobs = self.workflow["jobs"]
        candidate = jobs["candidate-assets"]
        native = jobs["native-acceptance"]
        self.assertEqual(
            "needs.policy.outputs.release == 'true' && needs.policy.outputs.channel == 'candidate'",
            candidate["if"],
        )
        self.assertEqual({"policy", "quality", "publish-images", "secure-images"}, set(candidate["needs"]))
        self.assertIn("release-candidate", job_text(candidate))
        self.assertIn("identity.json", job_text(candidate))
        self.assertIn("baseline-policy.json", job_text(candidate))
        self.assertIn("gh release download", job_text(candidate))
        self.assertIn('--arg manifestSha256 "$BASELINE_MANIFEST_SHA256"', self.workflow_text)
        self.assertIn("manifestSha256: $manifestSha256", job_text(candidate))
        self.assertIn("commit: $commit", job_text(candidate))
        self.assertNotIn("baseline-version", job_text(candidate))
        self.assertEqual(1, job_text(candidate).count("build_release.py"))

        text = job_text(native)
        self.assertIn("actions/download-artifact@", text)
        self.assertIn("release-candidate", text)
        self.assertIn("CANDIDATE_IDENTITY", text)
        self.assertIn("CANDIDATE_MANIFEST_SHA256", text)
        self.assertIn("CANDIDATE_BUNDLE_SHA256", text)
        for image_path in ("images.admin", "images.platform", "images.engine", 'images[\\"publish-gateway\\"]'):
            self.assertIn(image_path, text)
        self.assertNotIn("docker/build-push-action@", text)
        self.assertNotIn("build_release.py", text)

    def test_manual_dispatch_can_reverify_an_existing_candidate_but_never_publish(self):
        inputs = self.workflow["on"]["workflow_dispatch"]["inputs"]
        self.assertIn("candidate_run_id", inputs)
        native_text = job_text(self.workflow["jobs"]["native-acceptance"])
        self.assertIn("inputs.candidate_run_id", native_text)
        self.assertIn("github-token", native_text)
        self.assertNotIn("actions/checkout@", native_text)
        self.assertIn("orchestrator/release/native_acceptance.sh", native_text)
        publication = self.workflow["jobs"]["publication"]
        self.assertIn("needs.policy.outputs.release == 'true'", publication["if"])
        self.assertIn("needs.policy.outputs.channel", publication["if"])

    def test_native_job_runs_real_lifecycle_and_always_uploads_redacted_evidence(self):
        job = self.workflow["jobs"]["native-acceptance"]
        text = job_text(job)
        self.assertIn("native_acceptance.sh", text)
        self.assertIn("sudo -E", text)
        self.assertIn("NATIVE_ACCEPTANCE_REAL=1", text)
        upload = next(step for step in job["steps"] if step.get("name") == "Preserve redacted native acceptance evidence")
        self.assertEqual("always()", upload["if"])
        self.assertIn("native-acceptance-${{ matrix.ubuntu }}-${{ matrix.architecture }}", text)
        self.assertIn("if-no-files-found", text)
        self.assertNotIn("SURVEY_PRODUCTION_E2E", text)
        self.assertNotIn("skip", text.lower())
        self.assertNotIn("SURVEY_ACCEPTANCE_DNS_SUFFIX", text)
        self.assertIn("survey-native.test", text)

    def test_tag_publication_gate_depends_on_the_complete_native_matrix(self):
        gate = self.workflow["jobs"]["release-ready"]
        self.assertEqual(
            "needs.policy.outputs.release == 'true' && needs.policy.outputs.channel == 'candidate'",
            gate["if"],
        )
        self.assertEqual({"policy", "candidate-assets", "native-acceptance"}, set(gate["needs"]))
        text = job_text(gate)
        self.assertIn("test '${{ needs.native-acceptance.result }}' = success", text)
        self.assertIn("native-acceptance-*", text)
        self.assertIn("release-readiness.json", text)
        self.assertIn("native-release-readiness", text)
        self.assertIn("readiness", text)
        self.assertIn("verify-readiness", text)
        self.assertIn("if-no-files-found", text)
        self.assertEqual("${{ steps.readiness.outputs.promotable }}", gate["outputs"]["promotable"])
        self.assertEqual("native-release-readiness", gate["outputs"]["readiness-artifact"])

    def test_baseline_policy_is_explicit_and_bootstrap_never_claims_upgrade(self):
        policy = json.loads(BASELINE_POLICY_PATH.read_text(encoding="utf-8"))
        self.assertEqual(1, policy["schemaVersion"])
        self.assertIn("releases", policy)
        self.assertEqual({"mode": "bootstrap"}, policy["releases"].get("0.3.2-rc.1"))
        self.assertEqual({"mode": "bootstrap"}, policy["releases"].get("0.3.8-rc.1"))
        self.assertEqual({"mode": "bootstrap"}, policy["releases"].get("0.3.9-rc.1"))
        self.assertEqual({"mode": "bootstrap"}, policy["releases"].get("0.3.10-rc.1"))
        self.assertEqual({"mode": "bootstrap"}, policy["releases"].get("0.3.11-rc.1"))
        self.assertEqual({"mode": "bootstrap"}, policy["releases"].get("0.3.12-rc.1"))
        self.assertEqual({"mode": "bootstrap"}, policy["releases"].get("0.3.13-rc.1"))
        self.assertEqual({"mode": "bootstrap"}, policy["releases"].get("0.3.14-rc.1"))
        self.assertEqual({"mode": "bootstrap"}, policy["releases"].get("0.3.15-rc.1"))
        self.assertEqual({"mode": "bootstrap"}, policy["releases"].get("0.3.16-rc.1"))
        self.assertEqual({"mode": "bootstrap"}, policy["releases"].get("0.3.17-rc.1"))
        for version, rule in policy["releases"].items():
            with self.subTest(version=version):
                self.assertIn(rule["mode"], {"predecessor", "predecessor-artifact", "bootstrap"})
                if rule["mode"] in {"predecessor", "predecessor-artifact"}:
                    fields = {'mode', 'version', 'manifestSha256'}
                    if rule['mode'] == 'predecessor-artifact':
                        fields |= {'runId', 'commit'}
                        self.assertIs(type(rule['runId']), int)
                        self.assertGreater(rule['runId'], 0)
                        self.assertRegex(rule['commit'], r'^[0-9a-f]{40}$')
                        self.assertRegex(rule['version'], r'^[0-9]+\.[0-9]+\.[0-9]+-rc\.[0-9]+$')
                    self.assertEqual(fields, set(rule))
                    self.assertRegex(rule["version"], r"^[0-9]+\.[0-9]+\.[0-9]+(?:-rc\.[0-9]+)?$")
                    self.assertRegex(rule["manifestSha256"], r"^[0-9a-f]{64}$")
                else:
                    self.assertEqual({'mode'}, set(rule))
                    self.assertRegex(version, r"^[0-9]+\.[0-9]+\.[0-9]+-rc\.1$")
                    self.assertNotIn("version", rule)
        candidate_text = job_text(self.workflow["jobs"]["candidate-assets"])
        self.assertIn("bootstrap is only valid for the first prerelease RC", candidate_text)
        self.assertNotIn("gh release list --limit 1", candidate_text)
        self.assertIn("upgrade-verified", job_text(self.workflow["jobs"]["manual-promotion"]))
        script = SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertIn("upgrade_verified=false", script)
        helper = EVIDENCE_HELPER_PATH.read_text(encoding="utf-8")
        self.assertIn('"upgradeVerified": upgrade_verified', helper)

    def test_rc18_uses_exact_reverified_rc17_actions_predecessor(self):
        policy = json.loads(BASELINE_POLICY_PATH.read_text())
        rule = {'mode': 'predecessor-artifact', 'version': '0.3.17-rc.1', 'runId': 41756770743,
                'commit': 'd745a5eeb7eca6af59d63986db004249efbd2acc',
                'manifestSha256': '95b113aa6c554c8e918bdf8d7b0e4ed47bfa59d6b129741d131e7ca4c611379b'}
        self.assertEqual(rule, policy['releases'].get('0.3.18-rc.1'))
        self.assertEqual({'mode': 'bootstrap'}, policy['releases']['0.3.17-rc.1'])
        step = next(step for step in self.workflow['jobs']['candidate-assets']['steps'] if step.get('id') == 'baseline')
        script = step['run'].split("<<'PY'\n", 1)[1].split('\nPY\n', 1)[0]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'policy.json'
            def resolve(value):
                path.write_text(json.dumps({'schemaVersion': 1, 'releases': {'0.3.18-rc.1': value}}))
                return subprocess.run([sys.executable, '-', '0.3.18-rc.1', str(path)], input=script, text=True,
                                      capture_output=True, check=False)
            result = resolve(rule)
            self.assertEqual(0, result.returncode, result.stderr)
            values = dict(line.split('=', 1) for line in result.stdout.splitlines())
            self.assertEqual('predecessor', values['BASELINE_MODE'])
            self.assertEqual('predecessor-artifact', values['BASELINE_SOURCE'])
            self.assertEqual(rule['version'], values['MINIMUM_SOURCE_VERSION'])
            self.assertEqual(str(rule['runId']), values['BASELINE_RUN_ID'])
            self.assertEqual(rule['commit'], values['BASELINE_COMMIT'])
            self.assertEqual(rule['manifestSha256'], values['BASELINE_MANIFEST_SHA256'])
            for field, value in [('runId', True), ('runId', 0), ('commit', 'bad'), ('manifestSha256', 'bad'),
                                 ('version', '0.3.17'), ('url', 'https://invalid.example')]:
                with self.subTest(field=field, value=value):
                    self.assertNotEqual(0, resolve({**rule, field: value}).returncode)

    def test_rc19_keeps_qualified_rc17_source_and_failed_rc18_policy_unchanged(self):
        policy = json.loads(BASELINE_POLICY_PATH.read_text())
        expected = {'mode': 'predecessor-artifact', 'version': '0.3.17-rc.1', 'runId': 41756770743,
                    'commit': 'd745a5eeb7eca6af59d63986db004249efbd2acc',
                    'manifestSha256': '95b113aa6c554c8e918bdf8d7b0e4ed47bfa59d6b129741d131e7ca4c611379b'}
        self.assertEqual(expected, policy['releases'].get('0.3.19-rc.1'))
        self.assertEqual(expected, policy['releases']['0.3.18-rc.1'])
        self.assertEqual({'mode': 'bootstrap'}, policy['releases']['0.3.17-rc.1'])
        step = next(s for s in self.workflow['jobs']['candidate-assets']['steps'] if s.get('id') == 'baseline')
        script = step['run'].split("<<'PY'\n", 1)[1].split('\nPY\n', 1)[0]
        result = subprocess.run([sys.executable, '-', '0.3.19-rc.1', str(BASELINE_POLICY_PATH)],
                                input=script, text=True, capture_output=True, check=False)
        self.assertEqual(0, result.returncode, result.stderr)
        values = dict(line.split('=', 1) for line in result.stdout.splitlines())
        self.assertEqual({'BASELINE_MODE': 'predecessor', 'BASELINE_SOURCE': 'predecessor-artifact',
                          'BASELINE_VERSION': expected['version'], 'MINIMUM_SOURCE_VERSION': expected['version'],
                          'BASELINE_RUN_ID': str(expected['runId']), 'BASELINE_COMMIT': expected['commit'],
                          'BASELINE_MANIFEST_SHA256': expected['manifestSha256']}, values)


class NativeAcceptanceScriptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SCRIPT_PATH.read_text(encoding="utf-8")

    def test_script_is_valid_strict_shell_without_skip_or_fake_success_path(self):
        result = subprocess.run(["bash", "-n", str(SCRIPT_PATH)], capture_output=True, text=True, check=False)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("set -Eeuo pipefail", self.text)
        self.assertIn('[[ "${NATIVE_ACCEPTANCE_REAL:-}" == "1" ]]', self.text)
        self.assertNotIn("continue-on-error", self.text)
        self.assertNotIn("compose-logs", self.text)
        assert_native_acceptance_contract(self, self.text)
        assert_native_workflow_invocation(self, load_workflow())

    def test_upgrade_data_is_verified_before_upgrade_can_be_claimed(self):
        self.assertIn('upgrade-data-probe', self.text)
        self.assertLess(self.text.index('upgrade-data-probe'), self.text.index('upgrade_verified=true'))
        self.assertIn('--upgrade-data-evidence', self.text)

    def test_success_bypass_mutations_are_rejected(self):
        mutations = (
            'ALLOW_SKIP=1',
            'SKIP=1',
            'continue',
            'false || true',
            'false || exit 0',
            '[[ "${ALLOW_SKIP:-}" != "1" ]] || exit 0',
            'if [[ -n "${BYPASS:-}" ]]; then exit 0; fi',
            'if [[ -n "${FAST:-}" ]]; then exit; fi',
            'if [[ -n "${FAST:-}" ]]; then exec /usr/bin/true; fi',
            'if [[ -n "${FAST:-}" ]]; then acceptance_status="passed"; exit; fi',
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(AssertionError):
                assert_native_acceptance_contract(self, self.text + "\n" + mutation + "\n")

    def test_deleting_any_required_lifecycle_command_is_rejected(self):
        for command in REQUIRED_LIFECYCLE_COMMANDS:
            with self.subTest(command=command), self.assertRaises(AssertionError):
                assert_native_acceptance_contract(self, self.text.replace(command, "", 1))

    def test_workflow_cannot_swallow_native_acceptance_failure(self):
        workflow = load_workflow()
        step = next(
            item for item in workflow["jobs"]["native-acceptance"]["steps"]
            if item.get("name") == "Execute real clean-host install upgrade restore and uninstall"
        )
        step["run"] += " || exit 0"
        with self.assertRaises(AssertionError):
            assert_native_workflow_invocation(self, workflow)

    def test_script_verifies_shared_candidate_identity_before_installing(self):
        for required in (
            "CANDIDATE_IDENTITY",
            "CANDIDATE_MANIFEST_SHA256",
            "CANDIDATE_BUNDLE_SHA256",
            'baseline.get("manifestSha256")',
            "ADMIN_IMAGE_DIGEST",
            "PLATFORM_IMAGE_DIGEST",
            "PUBLISH_GATEWAY_IMAGE_DIGEST",
            "sha256sum --check",
            "docker buildx imagetools inspect",
        ):
            self.assertIn(required, self.text)

    def test_script_executes_full_lifecycle_and_restores_into_second_project(self):
        calls = re.findall(r"^\s*(?:if\s+)?run_ctl\s+([a-z-]+)", self.text, re.MULTILINE)
        self.assertEqual(1, calls.count("setup-probe"))
        for command in ("install", "setup-probe", "doctor", "backup", "restore", "uninstall"):
            self.assertIn(command, calls)
        self.assertIn("survey-restore-", self.text)
        self.assertIn("BASELINE_MODE", self.text)

    def test_clean_host_check_precedes_any_test_installation(self):
        self.assertLess(self.text.index('"$evidence_helper" clean-host'), self.text.index('work_root='))
        self.assertLess(self.text.index('export DOCKER_HOST=unix:///var/run/docker.sock'), self.text.index('run_ctl install'))
        self.assertLess(self.text.index('export COMPOSE_PROJECT_NAME=survey-production'), self.text.index('run_ctl install'))

    def test_script_redacts_diagnostics_and_writes_success_or_failure_summary(self):
        self.assertIn("native_acceptance_evidence.py", self.text)
        self.assertIn("trap collect_evidence EXIT", self.text)
        self.assertIn("summary.json", self.text)
        self.assertIn("doctor", self.text)
        self.assertIn("scan", self.text)
        self.assertNotRegex(self.text, r">\s*\"?\$[^\n]*(?:\.log|\.raw)")

    def test_script_uses_local_trusted_ca_and_records_public_acme_as_separate(self):
        self.assertIn("/etc/hosts", self.text)
        self.assertIn("SSL_CERT_FILE", self.text)
        helper = EVIDENCE_HELPER_PATH.read_text(encoding="utf-8")
        self.assertIn('"publicAcmeVerified": False', helper)
        self.assertIn('"preflightDisk": "separately-tested"', helper)


class UpgradeContinuityProbeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('upgrade_data_probe', UPGRADE_HELPER_PATH)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        runtime = ROOT / 'platform/deploy/production'
        sys.path.insert(0, str(runtime))
        try:
            spec = importlib.util.spec_from_file_location('continuity_test_doctor', runtime / 'doctor.py')
            cls.doctor = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.doctor)
        finally:
            sys.path.pop(0)

    @staticmethod
    def export_fixture(mode):
        if mode == 'bare-csv': return b'PROBE\nproduction acceptance\n'
        header = ['version', 'sid', 'responseid', 'generation', 'state', 'startedat', 'completedat', 'answersstatus', 'PROBE', 'OTHER']
        labels = ['版本', '引擎问卷号', '答卷号', '代次', '状态', '开始时间', '完成时间', '作答状态', 'production acceptance', '其他']
        row = ['1', '123', '7', 'generation-one', 'engine_completed', '', '', 'available', 'production acceptance', '']
        if mode == 'lost-answer': row[8] = 'missing'
        if mode == 'wrong-column': row[8], row[9] = '', 'production acceptance'
        if mode == 'wrong-response': row[2] = '8'
        if mode == 'wrong-generation': row[3] = 'another-generation'
        rows = [header, labels] + ([] if mode == 'header-only' else [row])
        if mode == 'duplicate-row': rows.append(row)
        if mode == 'duplicate-header': header[-1] = 'PROBE'
        fields = [['version', 'sid', 'column', 'fieldname', 'questioncode', 'type', 'aid', 'scale', 'label', 'sensitive', 'masking', 'options'],
                  ['1', '123', 'PROBE', 'probe-column', 'PROBE', 'S', '', '0', 'probe', 'false', '', '']]
        if mode == 'wrong-field': fields[1][3] = 'unrelated-column'
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as archive:
            for name, table in [('responses.csv', rows), ('fields.csv', fields)]:
                stream = io.StringIO(newline=''); csv.writer(stream).writerows(table)
                archive.writestr(name, stream.getvalue().encode('utf-8-sig'))
        return out.getvalue()

    def test_export_requires_unique_original_response_and_exact_answer_column(self):
        for mode in ('bare-csv', 'header-only', 'wrong-column', 'wrong-response', 'wrong-generation',
                     'duplicate-row', 'duplicate-header', 'wrong-field'):
            with self.subTest(mode=mode):
                probe, events = self.fixture(mode)
                with self.assertRaises(self.doctor.DoctorRuntimeError): probe.run_and_verify()
                self.assertEqual(['close', 'archive'], events[-2:])
                self.assertFalse(probe.export_verified)

    def fixture(self, mode='success'):
        events, state = [], {'upgraded': False}
        config = {'tenantId': '11111111-1111-4111-8111-111111111111', 'actorId': 'fixture-owner'}
        def api(probe, token, method, path, body=None, raw=False):
            if method == 'GET' and path == '/v1/resources?limit=1': return 200, {}
            if method == 'POST' and path == '/v1/projects': return 201, {'id': 'project'}
            if method == 'POST' and path == '/v1/surveys': return 201, {'id': 'survey'}
            if method == 'POST' and path.endswith('/publish'):
                return 200, {'version': {'engineSid': 123, 'engineInstanceId': 'engine'}}
            if method == 'GET' and '/responses?' in path:
                events.append('read-after' if state['upgraded'] else 'read-before')
                if state['upgraded']:
                    self.assertEqual('after', token)
                    self.assertEqual(['candidate-compose'], probe.compose)
                row = {'version': 1, 'engineSid': 123, 'generation': 'generation-one', 'responseId': 7, 'state': 'engine_completed', 'answersStatus': 'available',
                       'answers': {'probe-column': 'production acceptance'}}
                if mode == 'no-original-answer':
                    row.update(answersStatus='missing', answers=None)
                return 200, {'items': [] if mode == 'lost-response' and state['upgraded'] else [row]}
            if method == 'POST' and path.endswith('/exports'):
                events.append('export');self.assertTrue(state['upgraded']);return 202, {'jobId': 'export'}
            if method == 'GET' and path.endswith('/download'):
                events.append('download');return 200, self.export_fixture(mode)
            if method == 'GET' and path == '/v1/exports/export': return 200, {'status': 'completed'}
            if method == 'POST' and path.endswith('/archive'):
                events.append('archive');return (500 if mode == 'cleanup-failure' else 200), {}
            raise AssertionError((method, path))
        def configuration(probe):
            return {**config, 'actorId': 'changed'} if state['upgraded'] and mode == 'identity-change' else config
        methods = {'_configuration': configuration, '_token': lambda probe, config: 'after' if state['upgraded'] else 'before',
                   '_api': api, '_answer': lambda probe, sid: events.append('answer'),
                   '_close_engine_survey': lambda probe, instance, sid: events.append('close')}
        for name, replacement in methods.items():
            patch = mock.patch.object(self.doctor.ProductProbe, name, replacement)
            patch.start();self.addCleanup(patch.stop)
        def upgrade():
            events.append('upgrade')
            self.assertLess(events.index('answer'), events.index('read-before'))
            if mode == 'upgrade-failure': raise self.doctor.DoctorRuntimeError('upgrade failed')
            state['upgraded'] = True
        Probe = self.module.make_continuity_probe(self.doctor.ProductProbe, self.doctor.DoctorRuntimeError)
        return Probe('/unused', 'fixture.test', ['baseline-compose'], mock.Mock(),
                     upgrade_action=upgrade, upgraded_compose=lambda: ['candidate-compose']), events

    def test_same_response_and_original_answer_are_verified_after_upgrade(self):
        probe, events = self.fixture()
        flags = probe.run_and_verify()
        self.assertTrue(all(flags.values()))
        self.assertEqual(['answer', 'read-before', 'upgrade', 'read-after', 'export', 'download', 'close', 'archive'], events)

    def test_data_loss_identity_change_upgrade_or_cleanup_failure_never_passes(self):
        for mode in ('no-original-answer', 'lost-response', 'lost-answer', 'identity-change', 'upgrade-failure', 'cleanup-failure'):
            with self.subTest(mode=mode):
                probe, events = self.fixture(mode)
                with self.assertRaises(self.doctor.DoctorRuntimeError): probe.run_and_verify()
                self.assertEqual(['close', 'archive'], events[-2:])
                if mode == 'no-original-answer': self.assertNotIn('upgrade', events)
                if mode == 'cleanup-failure': self.assertEqual('cleanup', probe.continuity_phase)

    def test_evidence_write_failure_cannot_prevent_product_cleanup(self):
        probe, events = self.fixture()
        with self.assertRaises(OSError):
            self.module.record_continuity(probe, '0.3.17-rc.1', '0.3.18-rc.1', 'a'*64, 'b'*64, Path('/unused'),
                                          mock.Mock(side_effect=OSError('write failed')))
        self.assertEqual(['close', 'archive'], events[-2:])
        self.assertEqual('evidence-write', probe.continuity_phase)

    def test_real_upgrade_command_and_activated_version_are_both_required(self):
        manager = mock.Mock();manager._read_state.return_value = {'version': '0.3.18-rc.1'}
        with mock.patch.object(self.module.subprocess, 'run') as run:
            self.module.perform_upgrade('/ctl', '/target', '/manifest', 'a'*64, '0.3.18-rc.1', manager, self.doctor.DoctorRuntimeError)
            self.assertEqual(['/ctl', '--target', '/target', 'upgrade', '--manifest', '/manifest', '--manifest-sha256', 'a'*64], run.call_args.args[0])
            self.assertTrue(run.call_args.kwargs['check'])
            manager._read_state.return_value = {'version': '0.3.17-rc.1'}
            with self.assertRaises(self.doctor.DoctorRuntimeError):
                self.module.perform_upgrade('/ctl', '/target', '/manifest', 'a'*64, '0.3.18-rc.1', manager, self.doctor.DoctorRuntimeError)
        with mock.patch.object(self.module.subprocess, 'run', side_effect=subprocess.CalledProcessError(4, ['/ctl'], stderr='password=do-not-expose')):
            with self.assertRaises(self.doctor.DoctorRuntimeError) as failure:
                self.module.perform_upgrade('/ctl', '/target', '/manifest', 'a'*64, '0.3.18-rc.1', manager, self.doctor.DoctorRuntimeError)
            self.assertEqual(('cli-exit', 4), (failure.exception.upgrade_category, failure.exception.upgrade_exit_code))
            self.assertNotIn('do-not-expose', str(failure.exception))


class NativeAcceptanceEvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("native_acceptance_evidence", EVIDENCE_HELPER_PATH)
        cls.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.module
        spec.loader.exec_module(cls.module)

    def test_clean_host_guard_fails_closed_on_nonhosted_or_existing_resources(self):
        completed = subprocess.CompletedProcess([], 0, stdout='', stderr='')
        with mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted'}):
            with mock.patch.object(self.module.subprocess, 'run', return_value=completed) as run:
                self.module.require_clean_native_host()
                self.assertEqual(6, run.call_count)
                for call in run.call_args_list:
                    self.assertEqual(['docker', '--host', 'unix:///var/run/docker.sock'], call.args[0][:3])
                    self.assertTrue(call.kwargs['check'])
                commands = [call.args[0][3:] for call in run.call_args_list]
                self.assertEqual([
                    ['ps', '-aq', '--filter', 'label=com.docker.compose.project=survey-production'],
                    ['ps', '-aq', '--filter', 'name=^/survey-production-'],
                    ['volume', 'ls', '-q', '--filter', 'label=com.docker.compose.project=survey-production'],
                    ['volume', 'ls', '-q', '--filter', 'name=^survey-production_'],
                    ['network', 'ls', '-q', '--filter', 'label=com.docker.compose.project=survey-production'],
                    ['network', 'ls', '-q', '--filter', 'name=^survey-production_'],
                ], commands)
            for position in range(6):
                responses = [completed] * 6
                responses[position] = subprocess.CompletedProcess([], 0, stdout='existing-resource\n', stderr='')
                with self.subTest(position=position), mock.patch.object(self.module.subprocess, 'run', side_effect=responses):
                    with self.assertRaises(self.module.EvidenceError):
                        self.module.require_clean_native_host()
            with mock.patch.object(self.module.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'docker')):
                with self.assertRaises(self.module.EvidenceError):
                    self.module.require_clean_native_host()
        with mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'self-hosted'}):
            with self.assertRaises(self.module.EvidenceError):
                self.module.require_clean_native_host()
        with mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted',
                                          'DOCKER_CONTEXT': 'remote'}):
            with self.assertRaises(self.module.EvidenceError):
                self.module.require_clean_native_host()
        for override in ('other-project', ''):
            with self.subTest(project_override=override), mock.patch.dict(os.environ,
                    {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted',
                     'COMPOSE_PROJECT_NAME': override}, clear=True):
                with mock.patch.object(self.module.subprocess, 'run', return_value=completed) as run:
                    with self.assertRaises(self.module.EvidenceError):
                        self.module.require_clean_native_host()
                    run.assert_not_called()

    def test_fixed_project_environment_overrides_env_file_and_compose_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / '.env').write_text('COMPOSE_PROJECT_NAME=other-project\n')
            (root / 'compose.yml').write_text('name: another-project\nservices:\n  fixture:\n    image: fixture:test\n')
            result = subprocess.run(['docker', 'compose', '--env-file', str(root / '.env'), '-f',
                                     str(root / 'compose.yml'), 'config', '--format', 'json'],
                                    env={'PATH': os.environ['PATH'], 'COMPOSE_PROJECT_NAME': 'survey-production'},
                                    capture_output=True, text=True, check=True)
            self.assertEqual('survey-production', json.loads(result.stdout)['name'])

    def test_engine_health_evidence_never_copies_probe_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'health.json'
            self.module.write_engine_health_evidence({'Status': 'unhealthy', 'FailingStreak': 3,
                'Log': [{'ExitCode': 1, 'Output': 'password=unique-secret'}, {'ExitCode': -1, 'Output': 'private'}]}, output)
            result = json.loads(output.read_text())
            self.assertEqual({'schemaVersion': 1, 'status': 'unhealthy', 'probeExitCodes': [1, -1],
                              'probeCategories': ['unknown', 'unknown']}, result)
            self.assertNotIn('unique-secret', output.read_text())

    def test_gateway_diagnostic_retains_only_fixed_categories(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'gateway-diagnostic.json'
            self.module.write_gateway_diagnostic_evidence('request secret=private: connection refused\n', output)
            self.assertEqual({'schemaVersion': 1, 'observedCategories': ['connection-refused']},
                             json.loads(output.read_text()))
            self.assertNotIn('private', output.read_text())

    def test_failed_summary_tls_marker_is_not_claimed_as_verified(self):
        self.assertFalse(self.module.tls_marker_from_doctor({'checks': []}))
        self.assertFalse(self.module.tls_marker_from_doctor({'checks': [{'id': 'tls', 'status': 'blocked'}]}))
        self.assertTrue(self.module.tls_marker_from_doctor({'checks': [{'id': 'tls', 'status': 'ok'}]}))

    def test_upgrade_claim_requires_data_continuity_evidence(self):
        env = {'EXPECTED_UBUNTU': '22.04', 'EXPECTED_ARCHITECTURE': 'amd64',
               'CANDIDATE_MANIFEST_SHA256': 'a' * 64, 'CANDIDATE_BUNDLE_SHA256': 'b' * 64,
               'ADMIN_IMAGE_DIGEST': 'sha256:' + 'c' * 64, 'PLATFORM_IMAGE_DIGEST': 'sha256:' + 'd' * 64,
               'PUBLISH_GATEWAY_IMAGE_DIGEST': 'sha256:' + 'e' * 64, 'ENGINE_IMAGE_DIGEST': 'sha256:' + '2' * 64,
               'BASELINE_MODE': 'predecessor'}
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(os.environ, env):
            with self.assertRaises(self.module.EvidenceError):
                self.module.write_summary_evidence(Path(temporary) / 'summary.json', 'passed', 0, 'uninstall', True,
                                                   {'checks': [{'id': 'tls', 'status': 'ok'}]})

    def test_summary_reports_missing_marker_without_making_failure_look_successful(self):
        env = {'EXPECTED_UBUNTU': '22.04', 'EXPECTED_ARCHITECTURE': 'amd64',
               'CANDIDATE_MANIFEST_SHA256': 'a' * 64, 'CANDIDATE_BUNDLE_SHA256': 'b' * 64,
               'ADMIN_IMAGE_DIGEST': 'sha256:' + 'c' * 64, 'PLATFORM_IMAGE_DIGEST': 'sha256:' + 'd' * 64,
               'PUBLISH_GATEWAY_IMAGE_DIGEST': 'sha256:' + 'e' * 64, 'ENGINE_IMAGE_DIGEST': 'sha256:' + '2' * 64, 'BASELINE_MODE': 'bootstrap'}
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(os.environ, env):
            path = Path(temporary) / 'summary.json'
            self.module.write_summary_evidence(path, 'failed', 4, 'install', False)
            result = json.loads(path.read_text())
            self.assertFalse(result['tls']['httpsMarkerVerified'])
            self.assertEqual(('failed', 4), (result['status'], result['exitCode']))
            with self.assertRaises(self.module.EvidenceError):
                self.module.write_summary_evidence(path, 'passed', 0, 'uninstall', False)

    def test_failed_doctor_is_sanitized_and_preserves_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            secret_dir = root / "secrets"
            secret_dir.mkdir()
            evidence_dir = root / "evidence"
            evidence_dir.mkdir()
            (secret_dir / "test").write_text("unique-test-secret")
            script = SCRIPT_PATH.read_text(encoding="utf-8")
            function = script[script.index("write_doctor_evidence() {"):script.index("\nwrite_summary() {")]
            fake = "run_ctl() { printf '%s\\n' '{\"status\":\"error\",\"checks\":[{\"id\":\"tls\",\"status\":\"error\",\"summary\":\"token=unique-test-secret\"}]}'; return 4; }\n"
            fake += "evidence_secret_args() { printf '%s\\n' --secrets-dir \"$secret_dir\"; }\n"
            result = subprocess.run(["bash", "-c", "set -e\n" + fake + function + "\nwrite_doctor_evidence doctor-installed"],
                                    capture_output=True, text=True, env={**os.environ, "work_root": str(root),
                                    "evidence_root": str(evidence_dir), "secret_dir": str(secret_dir),
                                    "evidence_helper": str(EVIDENCE_HELPER_PATH)})
            self.assertEqual(4, result.returncode)
            evidence = (evidence_dir / "doctor-installed.json").read_text()
            self.assertEqual("error", json.loads(evidence)["status"])
            self.assertNotIn("unique-test-secret", evidence)

    def test_real_shell_gate_accepts_only_expected_first_install_backup_warning(self):
        import copy
        production = SCRIPT_PATH.parent.parent
        sys.path.insert(0, str(production))
        try:
            from doctor import Doctor
        finally:
            sys.path.pop(0)
        script = SCRIPT_PATH.read_text()
        function = script[script.index('write_doctor_evidence() {'):script.index('\nwrite_summary() {')]
        ids = ['ports', 'internal-exposure', 'runtime-exposure', 'tls', 'containers',
               'migrations', 'volumes', 'backups', 'minimal-probe']
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            doctor = object.__new__(Doctor)
            doctor.target = root
            report = {'status': 'warning', 'checks': [
                doctor._backup_check() if name == 'backups' else
                {'id': name, 'status': 'ok', 'summary': 'fixture passed'} for name in ids]}
            cases = [('doctor-installed', report, True), ('doctor-upgraded', report, False),
                     ('doctor-restored', report, False)]
            for name in ('tls', 'minimal-probe', 'migrations', 'backups'):
                invalid = copy.deepcopy(report)
                next(item for item in invalid['checks'] if item['id'] == name)['status'] = 'blocked'
                invalid['status'] = 'blocked'
                cases.append(('doctor-installed', invalid, False))
            invalid = copy.deepcopy(report)
            next(item for item in invalid['checks'] if item['id'] == 'backups')['summary'] = 'no readable verified backup exists'
            cases.append(('doctor-installed', invalid, False))
            invalid = copy.deepcopy(report)
            invalid['checks'][0]['status'] = 'warning'
            cases.append(('doctor-installed', invalid, False))
            healthy = copy.deepcopy(report)
            healthy['status'] = 'ok'
            next(item for item in healthy['checks'] if item['id'] == 'backups').update(status='ok', summary='1 verified backup(s) available')
            cases.extend((label, healthy, True) for label in ('doctor-installed', 'doctor-upgraded', 'doctor-restored'))
            for shape in ('missing', 'duplicate'):
                invalid = copy.deepcopy(healthy)
                if shape == 'missing': invalid['checks'].pop(0)
                else: invalid['checks'].append(copy.deepcopy(invalid['checks'][0]))
                cases.append(('doctor-installed', invalid, False))
            for index, (label, payload, accepted) in enumerate(cases):
                with self.subTest(case=index, label=label):
                    evidence = root / str(index)
                    evidence.mkdir()
                    fixture = root / 'report.json'
                    fixture.write_text(json.dumps(payload))
                    fake = 'run_ctl() { cat "$fixture_report"; }\nevidence_secret_args() { :; }\n'
                    result = subprocess.run(['bash', '-c', 'set -e\n' + fake + function + '\nwrite_doctor_evidence ' + label],
                        capture_output=True, text=True, env={**os.environ, 'work_root': str(root),
                        'evidence_root': str(evidence), 'fixture_report': str(fixture),
                        'evidence_helper': str(EVIDENCE_HELPER_PATH)})
                    self.assertEqual(accepted, result.returncode == 0, result.stderr)
                    self.assertTrue((evidence / (label + '.json')).exists())

    def test_nested_keys_authorization_uris_multiline_and_encoded_variants_are_redacted(self):
        payload = {
            "password": "json-secret",
            "nested": {
                "apiToken": "json-token",
                "private_key": "line-one\nline-two",
                "Authorization": "Bearer bearer-secret",
                "database": "postgres://user:uri-secret@db/app?token=query-secret&safe=yes",
                "encoded": "https://user:encoded%2Dsecret@example.test/path?password=url%2Dsecret",
            },
            "safe": "retained",
        }
        sanitized = self.module.sanitize(payload, secret_values={"line-one\nline-two"})
        rendered = json.dumps(sanitized, sort_keys=True)
        for secret in ("json-secret", "json-token", "line-one", "line-two", "bearer-secret", "uri-secret", "query-secret", "encoded%2Dsecret", "url%2Dsecret"):
            self.assertNotIn(secret, rendered)
        self.assertEqual("retained", sanitized["safe"])

    def test_evidence_writer_keeps_only_allowlisted_doctor_fields_and_scanner_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "doctor-installed.json"
            self.module.write_doctor_evidence({
                "status": "ok",
                "checks": [{"id": "tls", "status": "ok", "summary": "secret=hidden", "raw": "forbidden"}],
                "unexpected": "forbidden",
            }, output, secret_values={"hidden"})
            evidence = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual({"schemaVersion", "status", "checks"}, set(evidence))
            self.assertEqual({"id", "status", "summary"}, set(evidence["checks"][0]))
            self.module.scan_directory(root, secret_values={"hidden"})
            (root / "services.json").write_text('{"safe":"Bearer mutation-secret"}', encoding="utf-8")
            with self.assertRaises(self.module.EvidenceError):
                self.module.scan_directory(root, secret_values={"hidden"})

    def test_unknown_headers_cookies_and_pem_blocks_are_never_preserved(self):
        attack = (
            "X-Api-Key: unknown-header-secret\n"
            "Cookie: session=unknown-cookie-secret\n"
            "-----BEGIN PRIVATE KEY-----\nunknown-pem-secret\n-----END PRIVATE KEY-----"
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "doctor-installed.json"
            self.module.write_doctor_evidence({
                "status": "ok",
                "checks": [{"id": "tls", "status": "ok", "summary": attack}],
            }, output)
            rendered = output.read_text(encoding="utf-8")
            for secret in ("unknown-header-secret", "unknown-cookie-secret", "unknown-pem-secret"):
                self.assertNotIn(secret, rendered)
            self.module.scan_directory(output.parent)
            output.write_text(json.dumps({
                "schemaVersion": 1,
                "status": "ok",
                "checks": [{"id": "tls", "status": "ok", "summary": attack}],
            }), encoding="utf-8")
            with self.assertRaises(self.module.EvidenceError):
                self.module.scan_directory(output.parent)

    def test_strict_schema_rejects_unknown_fields_wrong_types_and_long_strings(self):
        valid = {
            "schemaVersion": 1,
            "status": "ok",
            "checks": [{"id": "tls", "status": "ok", "summary": "trusted HTTPS marker verified"}],
        }
        self.module.validate_evidence("doctor", valid)
        mutations = (
            {**valid, "unexpected": "value"},
            {**valid, "status": 1},
            {**valid, "checks": [{"id": "tls", "status": "ok", "summary": "x" * 4097}]},
            {**valid, "checks": [{**valid["checks"][0], "raw": "forbidden"}]},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(self.module.EvidenceError):
                self.module.validate_evidence("doctor", mutation)

    def test_release_readiness_blocks_stable_bootstrap_or_unverified_upgrade(self):
        runners = (
            ("22.04", "amd64"),
            ("24.04", "amd64"),
            ("22.04", "arm64"),
            ("24.04", "arm64"),
        )

        def summaries(mode: str, verified: bool) -> list[dict]:
            return [
                {
                    "schemaVersion": 1,
                    "status": "passed",
                    "exitCode": 0,
                    "lastStep": "uninstall",
                    "runner": {"ubuntu": ubuntu, "architecture": architecture},
                    "candidate": {
                        "manifestSha256": "a" * 64,
                        "bundleSha256": ("b" if architecture == "amd64" else "f") * 64,
                        "images": {
                            "admin": "sha256:" + "c" * 64,
                            "platform": "sha256:" + "d" * 64,
                            "publish-gateway": "sha256:" + "e" * 64,
                            "engine": "sha256:" + "2" * 64,
                        },
                    },
                    "baselineMode": mode,
                    "upgradeVerified": verified,
                    "tls": {"scope": "ci-local-trusted-ca", "httpsMarkerVerified": True, "publicAcmeVerified": False},
                    "preflightDisk": "separately-tested",
                    "completedAt": "2026-10-09T00:00:00+00:00",
                }
                for ubuntu, architecture in runners
            ]

        def identity(version: str, mode: str) -> dict:
            return {
                "version": version,
                "commit": "1" * 40,
                "manifestSha256": "a" * 64,
                "bundles": {
                    "amd64": {"sha256": "b" * 64},
                    "arm64": {"sha256": "f" * 64},
                },
                "images": {
                    "admin": {"digest": "sha256:" + "c" * 64},
                    "platform": {"digest": "sha256:" + "d" * 64},
                    "publish-gateway": {"digest": "sha256:" + "e" * 64},
                    "engine": {"digest": "sha256:" + "2" * 64},
                },
                "baseline": {"mode": mode},
            }

        def evidence_hashes() -> dict[str, str]:
            return {
                f"ubuntu-{ubuntu}-{architecture}": hashlib.sha256(
                    json.dumps(summary, sort_keys=True).encode("utf-8")
                ).hexdigest()
                for summary, (ubuntu, architecture) in zip(summaries("predecessor", True), runners)
            }

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "release-readiness.json"
            with self.assertRaises(self.module.EvidenceError):
                self.module.write_release_readiness(
                    identity("1.0.0", "bootstrap"),
                    summaries("bootstrap", False), evidence_hashes(), output,
                )
            with self.assertRaises(self.module.EvidenceError):
                self.module.write_release_readiness(
                    identity("1.0.0", "predecessor"),
                    summaries("predecessor", False), evidence_hashes(), output,
                )
            mismatched = summaries("predecessor", True)
            mismatched[0]["candidate"]["manifestSha256"] = "9" * 64
            with self.assertRaises(self.module.EvidenceError):
                self.module.write_release_readiness(
                    identity("1.0.0", "predecessor"), mismatched, evidence_hashes(), output,
                )
            self.module.write_release_readiness(
                identity("1.0.0", "predecessor"), summaries("predecessor", True), evidence_hashes(), output,
            )
            stable = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(stable["promotable"])
            self.assertEqual("upgrade-verified", stable["reason"])
            self.assertEqual({
                "schemaVersion", "candidateVersion", "candidateCommit", "manifestSha256",
                "amd64BundleSha256", "arm64BundleSha256", "adminManifestDigest",
                "platformManifestDigest", "gatewayManifestDigest", "engineManifestDigest", "matrixEvidenceSha256",
                "baselineMode", "nativeMatrixCount", "promotable", "reason",
            }, set(stable))
            self.assertEqual("1" * 40, stable["candidateCommit"])
            self.assertEqual("a" * 64, stable["manifestSha256"])
            self.assertEqual("b" * 64, stable["amd64BundleSha256"])
            self.assertEqual("f" * 64, stable["arm64BundleSha256"])
            self.assertEqual(evidence_hashes(), stable["matrixEvidenceSha256"])
            self.module.write_release_readiness(
                identity("0.3.0-rc.1", "bootstrap"),
                summaries("bootstrap", False), evidence_hashes(), output,
            )
            bootstrap = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(bootstrap["promotable"])
            self.assertEqual("bootstrap-not-promotable", bootstrap["reason"])
            with self.assertRaises(self.module.EvidenceError):
                self.module.write_release_readiness(
                    identity("0.3.0-rc.1", "bootstrap"), summaries("bootstrap", True), evidence_hashes(), output,
                )

    def test_downloaded_assets_are_recomputed_before_readiness_is_trusted(self):
        runners = (("22.04", "amd64"), ("24.04", "amd64"), ("22.04", "arm64"), ("24.04", "arm64"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "candidate"
            evidence = root / "evidence"
            candidate.mkdir()
            evidence.mkdir()
            bundle_hashes = {}
            bundles = {}
            for architecture in ("amd64", "arm64"):
                name = f"survey-1.0.0-linux-{architecture}.tar.gz"
                payload = f"bundle-{architecture}".encode("utf-8")
                (candidate / name).write_bytes(payload)
                digest = hashlib.sha256(payload).hexdigest()
                bundle_hashes[architecture] = digest
                bundles[architecture] = {"name": name, "sha256": digest}
            image_digests = {
                "admin": "sha256:" + "c" * 64,
                "platform": "sha256:" + "d" * 64,
                "publish-gateway": "sha256:" + "e" * 64,
                "engine": "sha256:" + "2" * 64,
            }
            manifest = {
                "version": "1.0.0", "commit": "1" * 40,
                "assets": {"bundles": bundles},
                "images": {
                    "ADMIN_IMAGE": "ghcr.io/example/admin@" + image_digests["admin"],
                    "PLATFORM_IMAGE": "ghcr.io/example/platform@" + image_digests["platform"],
                    "PUBLISH_GATEWAY_IMAGE": "ghcr.io/example/gateway@" + image_digests["publish-gateway"],
                    "ENGINE_IMAGE": "ghcr.io/example/engine@" + image_digests["engine"],
                },
            }
            manifest_path = candidate / "release.json"
            manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
            identity = {
                "schemaVersion": 1, "version": "1.0.0", "commit": "1" * 40,
                "manifestSha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                "bundles": bundles, "baseline": {"mode": "predecessor", 'version': '0.9.0', 'manifestSha256': '8'*64},
                "images": {
                    name: {"reference": manifest["images"][key], "digest": image_digests[name]}
                    for name, key in (("admin", "ADMIN_IMAGE"), ("platform", "PLATFORM_IMAGE"), ("publish-gateway", "PUBLISH_GATEWAY_IMAGE"), ("engine", "ENGINE_IMAGE"))
                },
            }
            identity_path = root / "identity.json"
            identity_path.write_text(json.dumps(identity), encoding="utf-8")
            for ubuntu, architecture in runners:
                directory = evidence / f"native-acceptance-{ubuntu}-{architecture}"
                directory.mkdir()
                summary = {
                    "schemaVersion": 1, "status": "passed", "exitCode": 0, "lastStep": "uninstall",
                    "runner": {"ubuntu": ubuntu, "architecture": architecture},
                    "candidate": {
                        "manifestSha256": identity["manifestSha256"],
                        "bundleSha256": bundle_hashes[architecture], "images": image_digests,
                    },
                    "baselineMode": "predecessor", "upgradeVerified": True,
                    "tls": {"scope": "ci-local-trusted-ca", "httpsMarkerVerified": True, "publicAcmeVerified": False},
                    "preflightDisk": "separately-tested", "completedAt": "2026-10-09T00:00:00+00:00",
                }
                (directory / "summary.json").write_text(json.dumps(summary, sort_keys=True), encoding="utf-8")
                proof = {'schemaVersion': 1, 'status': 'passed', 'baselineVersion': '0.9.0', 'candidateVersion': '1.0.0',
                         'baselineManifestSha256': '8'*64, 'candidateManifestSha256': identity['manifestSha256'],
                         'versionSwitchVerified': True, 'responseBeforeUpgradeVerified': True,
                         'responseAfterUpgradeVerified': True, 'exportAnswerVerified': True, 'cleanupCompleted': True}
                self.module.write_evidence(directory / 'upgrade-data.json', 'upgrade-data', proof)
            readiness_path = root / "release-readiness.json"
            self.module.write_release_readiness_from_assets(identity_path, candidate, evidence, readiness_path)
            self.module.verify_release_readiness_assets(readiness_path, identity_path, candidate, evidence)

            proof_path = evidence / 'native-acceptance-22.04-amd64' / 'upgrade-data.json'
            original_proof = proof_path.read_text()
            proof_path.unlink()
            with self.assertRaises(self.module.EvidenceError):
                self.module.verify_release_readiness_assets(readiness_path, identity_path, candidate, evidence)
            for field, value in [('baselineManifestSha256', '0'*64), ('candidateManifestSha256', '0'*64),
                                 ('candidateVersion', '1.0.1'), ('baselineVersion', '0.8.0'),
                                 ('exportAnswerVerified', False), ('cleanupCompleted', 1), ('raw', 'forbidden'),
                                 *[('schemaVersion', value) for value in (True, False, 1.0, '1', None, 0, 2)]]:
                altered = json.loads(original_proof);altered[field] = value;proof_path.write_text(json.dumps(altered))
                with self.subTest(upgrade_data=field, value=value), self.assertRaises(self.module.EvidenceError):
                    self.module.verify_release_readiness_assets(readiness_path, identity_path, candidate, evidence)
            proof_path.write_text(original_proof)

            original = readiness_path.read_text(encoding="utf-8")
            self.assertEqual(image_digests["engine"], json.loads(original)["engineManifestDigest"])
            for mutation in ("missing-engine", "wrong-engine", "wrong-reference", "extra-image"):
                altered = json.loads(json.dumps(identity))
                if mutation == "missing-engine":
                    del altered["images"]["engine"]
                elif mutation == "wrong-engine":
                    altered["images"]["engine"] = {"reference": "ghcr.io/example/engine@sha256:" + "9" * 64,
                                                     "digest": "sha256:" + "9" * 64}
                elif mutation == "wrong-reference":
                    altered["images"]["engine"]["reference"] = "ghcr.io/example/engine:mutable"
                else:
                    altered["images"]["unexpected"] = altered["images"]["engine"]
                with self.subTest(identity=mutation), self.assertRaises(self.module.EvidenceError):
                    self.module.verify_candidate_assets(altered, candidate)
            summary_path = evidence / "native-acceptance-22.04-amd64" / "summary.json"
            original_summary = summary_path.read_text()
            for mutation in ("missing-engine", "wrong-engine"):
                altered = json.loads(original_summary)
                if mutation == "missing-engine":
                    del altered["candidate"]["images"]["engine"]
                else:
                    altered["candidate"]["images"]["engine"] = "sha256:" + "9" * 64
                summary_path.write_text(json.dumps(altered))
                with self.subTest(summary=mutation), self.assertRaises(self.module.EvidenceError):
                    self.module.verify_release_readiness_assets(readiness_path, identity_path, candidate, evidence)
            summary_path.write_text(original_summary)
            mutations = {
                "candidateVersion": "1.0.1", "candidateCommit": "2" * 40,
                "manifestSha256": "9" * 64, "amd64BundleSha256": "9" * 64,
                "arm64BundleSha256": "9" * 64,
                "adminManifestDigest": "sha256:" + "9" * 64,
                "platformManifestDigest": "sha256:" + "9" * 64,
                "gatewayManifestDigest": "sha256:" + "9" * 64,
                "engineManifestDigest": "sha256:" + "9" * 64,
            }
            for field, replacement in mutations.items():
                mutated = json.loads(original)
                mutated[field] = replacement
                readiness_path.write_text(json.dumps(mutated), encoding="utf-8")
                with self.subTest(field=field), self.assertRaises(self.module.EvidenceError):
                    self.module.verify_release_readiness_assets(readiness_path, identity_path, candidate, evidence)
            mutated = json.loads(original)
            mutated["matrixEvidenceSha256"]["ubuntu-22.04-amd64"] = "9" * 64
            readiness_path.write_text(json.dumps(mutated), encoding="utf-8")
            with self.assertRaises(self.module.EvidenceError):
                self.module.verify_release_readiness_assets(readiness_path, identity_path, candidate, evidence)
            mutated = json.loads(original)
            mutated["unexpected"] = "forbidden"
            readiness_path.write_text(json.dumps(mutated), encoding="utf-8")
            with self.assertRaises(self.module.EvidenceError):
                self.module.verify_release_readiness_assets(readiness_path, identity_path, candidate, evidence)
            readiness_path.write_text(original, encoding="utf-8")
            amd64_bundle = candidate / bundles["amd64"]["name"]
            original_bundle = amd64_bundle.read_bytes()
            amd64_bundle.write_bytes(b"mutated bundle")
            with self.assertRaises(self.module.EvidenceError):
                self.module.verify_release_readiness_assets(readiness_path, identity_path, candidate, evidence)
            amd64_bundle.write_bytes(original_bundle)
            summary_path = evidence / "native-acceptance-22.04-amd64" / "summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["completedAt"] = "2026-10-09T00:00:01+00:00"
            summary_path.write_text(json.dumps(summary, sort_keys=True), encoding="utf-8")
            with self.assertRaises(self.module.EvidenceError):
                self.module.verify_release_readiness_assets(readiness_path, identity_path, candidate, evidence)


if __name__ == "__main__":
    unittest.main()
