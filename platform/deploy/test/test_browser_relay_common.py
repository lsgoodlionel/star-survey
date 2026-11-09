import os
from pathlib import Path
import subprocess
import tempfile
import unittest


COMMON = Path(__file__).with_name('browser-relay-common.sh')
IMAGE = 'sha256:' + 'a' * 64


def shell(code, *args, env=None):
    return subprocess.run(['bash', '-c', 'source "$1"\nshift\n' + code, 'fixture', str(COMMON), *args],
                          env={**os.environ, **(env or {})}, capture_output=True, text=True)


class BrowserRelayFixtureGuardTest(unittest.TestCase):
    def test_argument_validation(self):
        for args in [('mysql', 'fixture-cache', IMAGE, IMAGE), ('pgsql', 'fixture-cache', IMAGE, IMAGE)]:
            with self.subTest(args=args): self.assertEqual(0, shell('validate_fixture_args "$@"', *args).returncode)
        for args in [('oracle', 'fixture-cache', IMAGE, IMAGE), ('mysql', '../cache', IMAGE, IMAGE),
                     ('mysql', 'cache', 'latest', IMAGE), ('mysql', 'cache', IMAGE, 'sha256:abc'), ('mysql',)]:
            with self.subTest(args=args): self.assertNotEqual(0, shell('validate_fixture_args "$@"', *args).returncode)

    def test_cleanup_rejects_unknown_or_uninspectable_resources_before_delete(self):
        script = r'''prefix=survey-browser-relay-it-0123456789abcdef01234567
marker="$1"
docker() {
  case "$SCENARIO:$1:$2" in
    bad-list:ps:*) return 9 ;;
    unknown-manual:ps:*) [[ "$*" == *survey.fixture.run* ]] && echo "$prefix-foreign" ;;
    unknown-container:ps:*) [[ "$*" == *com.docker.compose.project* ]] && echo "$prefix-other" ;;
    unknown-volume:volume:ls) echo "${prefix}_foreign" ;;
    wrong-volume:volume:ls) echo "${prefix}_test-tmp" ;;
    wrong-volume:volume:inspect) echo wrong-project ;;
    unknown-network:network:ls) echo "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa ${prefix}_foreign" ;;
    *:rm:*|*:volume:rm|*:network:rm) echo deleted >> "$marker" ;;
  esac
  return 0
}
cleanup_owned_fixture
'''
        with tempfile.TemporaryDirectory() as tmp:
            marker = str(Path(tmp) / 'deleted')
            for scenario in ('bad-list', 'unknown-manual', 'unknown-container', 'unknown-volume', 'wrong-volume', 'unknown-network'):
                with self.subTest(scenario=scenario):
                    result = shell(script, marker, env={'SCENARIO': scenario})
                    self.assertNotEqual(0, result.returncode, result.stderr)
                    self.assertFalse(Path(marker).exists())
            self.assertEqual(0, shell(script, marker, env={'SCENARIO': 'empty'}).returncode)
            self.assertFalse(Path(marker).exists())

    def test_image_declarations_must_be_covered_and_inspectable(self):
        import json
        script = r'''docker() { [[ "$SCENARIO" != inspect_failure ]] || return 9; printf "%s" "$INSPECT_JSON"; }
inspect_fixture_image synthetic-image /allowed /another
'''
        def image(volumes):
            return [{"Id": IMAGE, "Config": {"Volumes": volumes}}]
        for value in (image(None), image({}), image({"/allowed": {}}), image({"/allowed": {}, "/another": {}})):
            with self.subTest(value=value):
                result = shell(script, env={"INSPECT_JSON": json.dumps(value)})
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(IMAGE, result.stdout.strip())
        for value in ([], [{}], image({"/allowed": {}, "/extra": {}}), image(["/allowed"]),
                      image({"relative": {}}), [{"Id": "bad", "Config": {}}]):
            with self.subTest(value=value):
                self.assertNotEqual(0, shell(script, env={"INSPECT_JSON": json.dumps(value)}).returncode)
        self.assertNotEqual(0, shell(script, env={"INSPECT_JSON": "malformed"}).returncode)
        self.assertNotEqual(0, shell(script, env={"INSPECT_JSON": json.dumps(image({})), "SCENARIO": "inspect_failure"}).returncode)

    def test_only_audited_synthetic_configs_become_readable(self):
        import shutil
        import stat
        repo = COMMON.parents[3]
        compose = (repo / 'docker-compose.dev.yml').read_text()
        test_web = compose.split('  test-web:', 1)[1].split('\n  # Optional', 1)[0]
        self.assertIn('ENGINE_DB_ROOT_PASSWORD: root', test_web)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            private = root / 'private-evidence'
            private.mkdir(mode=0o700)
            runtime = root / '.runtime'
            runtime.mkdir(mode=0o700)
            for engine in ('mysql', 'pgsql'):
                with self.subTest(engine=engine):
                    source = root / f'config.{engine}.php'
                    target = runtime / 'config.php'
                    shutil.copyfile(COMMON.parent / source.name, source)
                    shutil.copyfile(source, target)
                    target.chmod(0o600)
                    result = shell('browser_relay_make_synthetic_config_readable "$@"', str(source), str(target), engine)
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertEqual(0o644, stat.S_IMODE(target.stat().st_mode))
                    self.assertEqual(0o700, stat.S_IMODE(private.stat().st_mode))
                    self.assertEqual(0o700, stat.S_IMODE(runtime.stat().st_mode))
                    target.write_text(target.read_text() + "\npassword=synthetic-tamper-example\n")
                    target.chmod(0o600)
                    self.assertNotEqual(0, shell('browser_relay_make_synthetic_config_readable "$@"', str(source), str(target), engine).returncode)
                    self.assertEqual(0o600, stat.S_IMODE(target.stat().st_mode))
                    target.unlink()
                    shutil.copyfile(source, target)
                    source.write_text(source.read_text() + "\nsecret=synthetic-tamper-example\n")
                    target.chmod(0o600)
                    self.assertNotEqual(0, shell('browser_relay_make_synthetic_config_readable "$@"', str(source), str(target), engine).returncode)
                    self.assertEqual(0o600, stat.S_IMODE(target.stat().st_mode))
                    target.unlink()
                    shutil.copyfile(COMMON.parent / source.name, source)
                    os.link(source, target)
                    self.assertNotEqual(0, shell('browser_relay_make_synthetic_config_readable "$@"', str(source), str(target), engine).returncode)
                    target.unlink()
                    target.symlink_to(source)
                    self.assertNotEqual(0, shell('browser_relay_make_synthetic_config_readable "$@"', str(source), str(target), engine).returncode)
                    target.unlink()

    def test_fixed_stage_and_cleanup_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage = str(Path(tmp) / 'stage')
            cleanup = str(Path(tmp) / 'cleanup')
            env = {'SURVEY_CROSSLAYER_STAGE_FILE': stage, 'SURVEY_CROSSLAYER_CLEANUP_FILE': cleanup}
            self.assertEqual(0, shell('fixture_stage browser; fixture_cleanup_record verified', env=env).returncode)
            self.assertEqual('browser\n', Path(stage).read_text())
            self.assertEqual('verified\n', Path(cleanup).read_text())
            self.assertNotEqual(0, shell('fixture_stage "$1"', 'secret-value', env=env).returncode)
            self.assertEqual('browser\n', Path(stage).read_text())
            self.assertNotEqual(0, shell('fixture_cleanup_record "$1"', 'unexpected', env=env).returncode)
            self.assertEqual('verified\n', Path(cleanup).read_text())

    def test_platform_database_diagnostic_substages_and_state_are_fixed(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp) / 'stage'
            state = Path(tmp) / 'state'
            env = {'SURVEY_CROSSLAYER_STAGE_FILE': str(stage), 'SURVEY_CROSSLAYER_PLATFORM_FILE': str(state)}
            for name in ('platform_db_create', 'platform_db_wait'):
                with self.subTest(name=name):
                    self.assertEqual(0, shell('fixture_stage "$1"', name, env=env).returncode)
                    self.assertEqual(name + '\n', stage.read_text())
            self.assertEqual(0, shell('docker() { echo exited; }; fixture_platform_state synthetic', env=env).returncode)
            self.assertEqual('exited\n', state.read_text())
            self.assertEqual(0, shell('docker() { echo sensitive_value; }; fixture_platform_state synthetic', env=env).returncode)
            self.assertEqual('unknown\n', state.read_text())
            self.assertEqual(0, shell('docker() { return 1; }; fixture_platform_state synthetic', env=env).returncode)
            self.assertEqual('unknown\n', state.read_text())

    def test_probe_start_records_run_failure_without_ready_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / 'probe'
            result = shell('fixture_probe_record start no 0 unknown 0', env={'SURVEY_CROSSLAYER_PROBE_FILE': str(probe)})
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual('probe_phase=start first_ready=no attempt_count=0 pg_isready_rc=unknown elapsed=0\n', probe.read_text())
            self.assertNotEqual(0, shell('fixture_probe_record start no 61 unknown 0', env={'SURVEY_CROSSLAYER_PROBE_FILE': str(probe)}).returncode)
            self.assertNotEqual(0, shell('fixture_probe_record start no 0 secret 0', env={'SURVEY_CROSSLAYER_PROBE_FILE': str(probe)}).returncode)

    def test_platform_probe_records_first_ready_then_confirmation_failure(self):
        script = r'''calls=0
sleep() { :; }
docker() {
  if [[ "$1" == exec ]]; then calls=$((calls+1)); [[ "$calls" == 1 ]] && return 0; return 2; fi
  if [[ "$1" == inspect ]]; then echo exited; return 0; fi
  return 1
}
if fixture_wait_platform_database synthetic; then exit 88; fi
'''
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / 'probe'
            state = Path(tmp) / 'platform'
            result = shell(script, env={'SURVEY_CROSSLAYER_PROBE_FILE': str(probe), 'SURVEY_CROSSLAYER_PLATFORM_FILE': str(state)})
            self.assertEqual(0, result.returncode, result.stderr)
            fields = dict(part.split('=', 1) for part in probe.read_text().split())
            self.assertEqual('confirm', fields['probe_phase'])
            self.assertEqual('yes', fields['first_ready'])
            self.assertEqual('1', fields['attempt_count'])
            self.assertEqual('2', fields['pg_isready_rc'])
            self.assertTrue(fields['elapsed'].isdigit())
            self.assertEqual('exited\n', state.read_text())

    def test_platform_probe_delayed_ready_and_exhaustion(self):
        script = r'''calls=0
sleep() { :; }
docker() {
  if [[ "$1" == inspect ]]; then echo running; return 0; fi
  calls=$((calls+1))
  if [[ "$SCENARIO" == delayed ]]; then [[ "$calls" -ge 3 ]]; return; fi
  if [[ "$calls" -le 60 ]]; then return 1; fi
  return 2
}
if [[ "$SCENARIO" == delayed ]]; then
  fixture_wait_platform_database synthetic || exit 88
else
  if fixture_wait_platform_database synthetic; then exit 89; fi
fi
'''
        with tempfile.TemporaryDirectory() as tmp:
            for scenario, expected in [('delayed', ('confirm', 'yes', '3', '0')),
                                       ('exhausted', ('confirm', 'no', '60', '2'))]:
                with self.subTest(scenario=scenario):
                    path = Path(tmp) / scenario
                    result = shell(script, env={'SURVEY_CROSSLAYER_PROBE_FILE': str(path), 'SCENARIO': scenario})
                    self.assertEqual(0, result.returncode, result.stderr)
                    fields = dict(part.split('=', 1) for part in path.read_text().split())
                    self.assertEqual(expected, tuple(fields[k] for k in ('probe_phase', 'first_ready', 'attempt_count', 'pg_isready_rc')))

    def test_unwritable_probe_fails_before_readiness_call(self):
        script = r'''docker() { touch "$CALL_MARKER"; }
if fixture_wait_platform_database synthetic; then exit 88; fi
'''
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'called'
            result = shell(script, env={'SURVEY_CROSSLAYER_PROBE_FILE': '/dev/null/unwritable', 'CALL_MARKER': str(marker)})
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertFalse(marker.exists())

    def test_temporary_socket_readiness_does_not_release_fixture(self):
        script = r'''calls=0
sleep() { :; }
docker() {
  if [[ "$1" == exec && "$*" == *pg_isready* ]]; then
    if [[ "$*" != *"-h 127.0.0.1"* ]]; then return 0; fi
    calls=$((calls+1)); [[ "$calls" -ge 2 ]]; return
  fi
  return 0
}
fixture_wait_platform_database synthetic
'''
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / 'probe'
            result = shell(script, env={'SURVEY_CROSSLAYER_PROBE_FILE': str(probe)})
            self.assertEqual(0, result.returncode, result.stderr)
            fields = dict(part.split('=', 1) for part in probe.read_text().split())
            self.assertEqual('2', fields['attempt_count'])
            self.assertEqual('0', fields['pg_isready_rc'])

    def test_missing_platform_role_fails_closed_after_tcp_ready(self):
        script = r'''sleep() { :; }
docker() {
  if [[ "$*" == *pg_isready* ]]; then return 0; fi
  if [[ "$*" == *psql* ]]; then return 1; fi
  if [[ "$1" == inspect ]]; then echo running; return 0; fi
  return 1
}
if fixture_wait_platform_database synthetic; then exit 88; fi
'''
        result = shell(script)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_ready_requires_both_real_fixture_roles(self):
        script = r'''docker() {
  if [[ "$*" == *pg_isready* ]]; then [[ "$*" == *"-h 127.0.0.1"* ]]; return; fi
  if [[ "$*" == *psql* ]]; then
    case "$*" in
      *"PGPASSWORD=platform_owner"*"-U platform_owner"*) echo owner >> "$ROLE_LOG"; return 0 ;;
      *"PGPASSWORD=platform_app"*"-U platform_app"*) echo app >> "$ROLE_LOG"; return 0 ;;
    esac
  fi
  return 1
}
fixture_wait_platform_database synthetic
'''
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'roles'
            result = shell(script, env={'ROLE_LOG': str(log)})
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(['owner', 'app'], log.read_text().splitlines())

    def test_independent_database_owner_and_app_absences_fail_closed(self):
        script = r'''sleep() { :; }
docker() {
  if [[ "$*" == *pg_isready* ]]; then return 0; fi
  if [[ "$*" == *psql* && "$*" == *"-U platform_owner"* ]]; then
    echo owner >> "$ROLE_LOG"
    [[ "$SCENARIO" != missing_database && "$SCENARIO" != missing_owner ]]; return
  fi
  if [[ "$*" == *psql* && "$*" == *"-U platform_app"* ]]; then
    echo app >> "$ROLE_LOG"
    [[ "$SCENARIO" != missing_app ]]; return
  fi
  if [[ "$1" == inspect ]]; then echo running; return 0; fi
  return 1
}
if fixture_wait_platform_database synthetic; then exit 88; fi
'''
        with tempfile.TemporaryDirectory() as tmp:
            for scenario, expected in [('missing_database', ['owner']), ('missing_owner', ['owner']),
                                       ('missing_app', ['owner', 'app'])]:
                with self.subTest(scenario=scenario):
                    log = Path(tmp) / scenario
                    result = shell(script, env={'ROLE_LOG': str(log), 'SCENARIO': scenario})
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertEqual(expected, log.read_text().splitlines())

    def test_gateway_user_must_be_explicit_nonroot_numeric(self):
        self.assertEqual(0, shell('validate_gateway_user "$1"', '10001:10001').returncode)
        for value in ('', 'root', '0:0', '10001:0', '0:10001', 'gateway', '10001', '1234567:10001'):
            with self.subTest(value=value):
                self.assertNotEqual(0, shell('validate_gateway_user "$1"', value).returncode)

    def test_gateway_mount_validation_fails_closed(self):
        script = r'''docker() { [[ "$SCENARIO" != inspect_failure ]] || return 9; printf "%s" "$INSPECT_JSON"; }
gateway_uid=10001 gateway_gid=10001 validate_gateway_mount synthetic-gateway
'''
        import json
        valid = [{"HostConfig": {"Tmpfs": {"/var/lib/pubgw": "uid=10001,gid=10001,mode=0700"}}, "Mounts": []}]
        bad = [[], [{}], [{"HostConfig": {"Tmpfs": {}}, "Mounts": []}],
               [{"HostConfig": {"Tmpfs": {"/var/lib/pubgw": "uid=10001,gid=10001,mode=0700"}},
                 "Mounts": [{"Destination": "/var/lib/pubgw", "Type": "volume"}]}]]
        self.assertEqual(0, shell(script, env={"INSPECT_JSON": json.dumps(valid)}).returncode)
        for value in bad:
            with self.subTest(value=value):
                self.assertNotEqual(0, shell(script, env={"INSPECT_JSON": json.dumps(value)}).returncode)
        self.assertNotEqual(0, shell(script, env={"INSPECT_JSON": "not-json"}).returncode)
        self.assertNotEqual(0, shell(script, env={"INSPECT_JSON": json.dumps(valid), "SCENARIO": "inspect_failure"}).returncode)

    def test_cleanup_failure_cannot_hide_original_failure(self):
        script = r'''work=/synthetic/retained
cleanup_owned_fixture() { return 1; }
(exit 7)
fixture_exit
'''
        with tempfile.TemporaryDirectory() as tmp:
            cleanup = Path(tmp) / 'cleanup'
            result = shell(script, env={'SURVEY_CROSSLAYER_CLEANUP_FILE': str(cleanup)})
            self.assertEqual(5, result.returncode)
            self.assertIn('original status=7', result.stderr)
            self.assertIn('/synthetic/retained', result.stderr)
            self.assertEqual('unverified\n', cleanup.read_text())


if __name__ == '__main__': unittest.main()
