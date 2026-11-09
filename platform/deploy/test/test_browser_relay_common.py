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

    def test_gateway_mount_validation_fails_closed(self):
        script = r'''docker() { [[ "$SCENARIO" != inspect_failure ]] || return 9; printf "%s" "$INSPECT_JSON"; }
validate_gateway_mount synthetic-gateway
'''
        import json
        valid = [{"HostConfig": {"Tmpfs": {"/var/lib/pubgw": ""}}, "Mounts": []}]
        bad = [[], [{}], [{"HostConfig": {"Tmpfs": {}}, "Mounts": []}],
               [{"HostConfig": {"Tmpfs": {"/var/lib/pubgw": ""}},
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
        result = shell(script)
        self.assertEqual(5, result.returncode)
        self.assertIn('original status=7', result.stderr)
        self.assertIn('/synthetic/retained', result.stderr)


if __name__ == '__main__': unittest.main()
