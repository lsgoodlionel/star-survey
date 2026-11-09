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
