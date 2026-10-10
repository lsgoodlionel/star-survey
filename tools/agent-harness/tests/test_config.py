"""Exercise configuration trust boundaries using real files and loaders."""

import copy
from dataclasses import FrozenInstanceError
from fnmatch import fnmatchcase
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HARNESS = Path(__file__).resolve().parents[1]
REPO = HARNESS.parents[1]
sys.path.insert(0, str(HARNESS))

from agent_harness.config import (  # noqa: E402
    ConfigError, GateDefinition, GateMatrix, PathRule, PolicyConfig,
    load_gate_matrix, load_protected_paths,
)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "policy.yaml"
        self.matrix = {
            "version": 1,
            "gates": [{"id": "unit", "command": ["python3", "-m", "unittest"],
                       "cwd": ".", "timeout_seconds": 60}],
            "profiles": {"agent-harness": {"paths": ["tools/agent-harness/**"],
                                           "gates": ["unit"]}},
        }
        self.policy = {
            "version": 1,
            "rules": [{"id": "source", "patterns": ["platform/**"],
                       "operations": ["add", "modify", "delete"],
                       "action": "review_required"}],
        }

    def write(self, value):
        self.path.write_text(json.dumps(value), encoding="utf-8")
        return self.path

    def test_loads_json_compatible_yaml_without_dependency(self):
        matrix = load_gate_matrix(self.write(self.matrix))
        self.assertIsInstance(matrix, GateMatrix)
        self.assertIsInstance(matrix.gates[0], GateDefinition)
        self.assertEqual(matrix.gates[0].command, ("python3", "-m", "unittest"))
        self.assertEqual(matrix.profiles["agent-harness"], ("unit",))
        policy = load_protected_paths(self.write(self.policy))
        self.assertIsInstance(policy, PolicyConfig)
        self.assertIsInstance(policy.rules[0], PathRule)
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-c",
             "import sys; sys.path.insert(0, sys.argv[1]); "
             "from agent_harness.config import load_gate_matrix, load_protected_paths; "
             "from pathlib import Path; load_gate_matrix(Path(sys.argv[2])); "
             "load_protected_paths(Path(sys.argv[3])); "
             "assert 'yaml' not in sys.modules",
             str(HARNESS), str(REPO / "docs/agent/GATE_MATRIX.yaml"),
             str(REPO / "docs/agent/PROTECTED_PATHS.yaml")],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_unknown_top_level_keys(self):
        for loader, value in ((load_gate_matrix, self.matrix),
                              (load_protected_paths, self.policy)):
            with self.subTest(loader=loader.__name__):
                value["override"] = True
                with self.assertRaises(ConfigError):
                    loader(self.write(value))

    def test_rejects_shell_string_commands(self):
        for command in ("python3 -m unittest", [], [""], ["  "], [1], [True],
                        ["python3", None], ["python3", "a\x00b"]):
            with self.subTest(command=command):
                self.matrix["gates"][0]["command"] = command
                with self.assertRaises(ConfigError):
                    load_gate_matrix(self.write(self.matrix))

    def test_gate_ids_are_unique(self):
        self.matrix["gates"].append(copy.deepcopy(self.matrix["gates"][0]))
        with self.assertRaises(ConfigError):
            load_gate_matrix(self.write(self.matrix))

    def test_path_rule_actions_are_closed_enum(self):
        for action in ("deny", "approval_required", "review_required", "generated"):
            with self.subTest(action=action):
                self.policy["rules"][0]["action"] = action
                if action == "generated":
                    self.policy["rules"][0]["generator"] = ["python3", "generate.py"]
                result = load_protected_paths(self.write(self.policy))
                self.assertEqual(result.rules[0].action, action)
        for action in ("allow", "DENY", "", None, [], 1):
            with self.subTest(action=action):
                self.policy["rules"][0]["action"] = action
                with self.assertRaises(ConfigError):
                    load_protected_paths(self.write(self.policy))

    def test_timeouts_are_positive_integers(self):
        for timeout in (0, -1, True, 1.5, "60", None):
            with self.subTest(timeout=timeout):
                self.matrix["gates"][0]["timeout_seconds"] = timeout
                with self.assertRaises(ConfigError):
                    load_gate_matrix(self.write(self.matrix))

    def test_rejects_repository_escape_and_empty_patterns(self):
        for path in ("", " ", "../outside", "a/../../outside", "/tmp/outside",
                     "C:/outside", "a\\..\\outside", "a/../outside", "a\x00b"):
            with self.subTest(path=path):
                self.matrix["gates"][0]["cwd"] = path
                with self.assertRaises(ConfigError):
                    load_gate_matrix(self.write(self.matrix))
                self.policy["rules"][0]["patterns"] = [path]
                with self.assertRaises(ConfigError):
                    load_protected_paths(self.write(self.policy))
                matrix = copy.deepcopy(self.matrix)
                matrix["gates"][0]["cwd"] = "."
                matrix["profiles"]["agent-harness"]["paths"] = [path]
                with self.assertRaises(ConfigError):
                    load_gate_matrix(self.write(matrix))

    def test_rejects_unknown_nested_keys(self):
        for target in ("gate", "profile", "rule"):
            with self.subTest(target=target):
                matrix, policy = copy.deepcopy(self.matrix), copy.deepcopy(self.policy)
                if target == "gate":
                    matrix["gates"][0]["shell"] = True
                elif target == "profile":
                    matrix["profiles"]["agent-harness"]["skip"] = True
                else:
                    policy["rules"][0]["allow"] = True
                with self.assertRaises(ConfigError):
                    if target == "rule":
                        load_protected_paths(self.write(policy))
                    else:
                        load_gate_matrix(self.write(matrix))

    def test_rejects_missing_keys_and_wrong_container_types(self):
        for loader, valid in ((load_gate_matrix, self.matrix),
                              (load_protected_paths, self.policy)):
            for key in valid:
                value = copy.deepcopy(valid)
                del value[key]
                with self.subTest(loader=loader.__name__, missing=key):
                    with self.assertRaises(ConfigError):
                        loader(self.write(value))
            for value in ([], None, "policy", {"version": 1},
                          {**valid, "version": True}, {**valid, "version": 2}):
                with self.subTest(loader=loader.__name__, value=value):
                    with self.assertRaises(ConfigError):
                        loader(self.write(value))

    def test_rejects_unknown_gate_references_and_empty_profiles(self):
        for refs in (["missing"], [], ["unit", "unit"], "unit"):
            with self.subTest(refs=refs):
                self.matrix["profiles"]["agent-harness"]["gates"] = refs
                with self.assertRaises(ConfigError):
                    load_gate_matrix(self.write(self.matrix))

    def test_rejects_empty_collections_and_invalid_identifiers(self):
        for key, value in (("gates", []), ("gates", {}), ("profiles", {}),
                           ("profiles", [])):
            with self.subTest(key=key, value=value):
                matrix = {**self.matrix, key: value}
                with self.assertRaises(ConfigError):
                    load_gate_matrix(self.write(matrix))
        for identifier in ("", " ", None, 2):
            with self.subTest(identifier=identifier):
                self.matrix["gates"][0]["id"] = identifier
                self.policy["rules"][0]["id"] = identifier
                with self.assertRaises(ConfigError):
                    load_gate_matrix(self.write(self.matrix))
                with self.assertRaises(ConfigError):
                    load_protected_paths(self.write(self.policy))
        for patterns in ([], "platform/**", [1]):
            self.policy["rules"][0]["patterns"] = patterns
            with self.assertRaises(ConfigError):
                load_protected_paths(self.write(self.policy))

    def test_rejects_duplicate_rule_ids_and_invalid_operations(self):
        policy = copy.deepcopy(self.policy)
        policy["rules"].append(copy.deepcopy(policy["rules"][0]))
        with self.assertRaises(ConfigError):
            load_protected_paths(self.write(policy))
        for operations in ([], ["execute"], "modify", ["modify", "modify"]):
            self.policy["rules"][0]["operations"] = operations
            with self.assertRaises(ConfigError):
                load_protected_paths(self.write(self.policy))

    def test_generated_rules_require_valid_generator(self):
        self.policy["rules"][0]["action"] = "generated"
        with self.assertRaises(ConfigError):
            load_protected_paths(self.write(self.policy))
        for command in ("python3 generate.py", [], [""]):
            self.policy["rules"][0]["generator"] = command
            with self.assertRaises(ConfigError):
                load_protected_paths(self.write(self.policy))

    def test_models_are_deeply_immutable(self):
        matrix = load_gate_matrix(self.write(self.matrix))
        policy = load_protected_paths(self.write(self.policy))
        with self.assertRaises(FrozenInstanceError):
            matrix.gates[0].cwd = "outside"
        with self.assertRaises(TypeError):
            matrix.profiles["agent-harness"] = ("missing",)
        with self.assertRaises(TypeError):
            matrix.paths["agent-harness"] = ("outside",)
        with self.assertRaises(FrozenInstanceError):
            policy.rules[0].action = "allow"

    def test_malformed_files_fail_with_config_error(self):
        for content in ("version: 1", "{", '{"version":1,"version":2}',
                        '{"version":NaN}'):
            with self.subTest(content=content):
                self.path.write_text(content, encoding="utf-8")
                for loader in (load_gate_matrix, load_protected_paths):
                    with self.assertRaises(ConfigError):
                        loader(self.path)
        self.path.write_bytes(b"\xff")
        with self.assertRaises(ConfigError):
            load_gate_matrix(self.path)
        with self.assertRaises(ConfigError):
            load_gate_matrix(self.path.parent / "missing")

    def test_repository_policy_profiles_are_loadable(self):
        matrix = load_gate_matrix(REPO / "docs/agent/GATE_MATRIX.yaml")
        self.assertEqual(set(matrix.profiles), {
            "admin-web", "platform-java", "publish-gateway", "production-release",
            "cross-stack-e2e", "documentation", "agent-harness",
        })
        policy = load_protected_paths(REPO / "docs/agent/PROTECTED_PATHS.yaml")
        self.assertEqual({rule.action for rule in policy.rules}, {
            "deny", "approval_required", "review_required", "generated",
        })

    def test_sensitive_source_paths_have_approval_rules(self):
        policy = load_protected_paths(REPO / "docs/agent/PROTECTED_PATHS.yaml")
        sources = (
            "plugins/MjyRuntimePolicy/MjyAccessGate.php",
            "plugins/MjyQuestionExtensions/MjyChannelAuth.php",
            "platform/tools/publish-gateway/pubgw/auth.py",
            "platform/services/business/src/main/java/cn/mjy/platform/shared/security/SecurityConfig.java",
            "platform/services/business/src/main/resources/db/migration/V1__baseline.sql",
            "platform/deploy/production/README.md",
            ".github/workflows/platform-quality.yml",
        )
        for source in sources:
            with self.subTest(source=source):
                self.assertTrue((REPO / source).is_file())
                self.assertTrue(any(
                    rule.action == "approval_required"
                    and any(fnmatchcase(source, pattern) for pattern in rule.patterns)
                    for rule in policy.rules
                ), source)

    def test_gate_and_generator_entrypoints_exist(self):
        matrix = load_gate_matrix(REPO / "docs/agent/GATE_MATRIX.yaml")
        policy = load_protected_paths(REPO / "docs/agent/PROTECTED_PATHS.yaml")
        for gate in matrix.gates:
            with self.subTest(gate=gate.id):
                self.assertTrue((REPO / gate.cwd).is_dir())
                if "/" in gate.command[0]:
                    self.assertTrue((REPO / gate.cwd / gate.command[0]).is_file())
                if gate.command[0] == "python3" and gate.command[1].endswith(".py"):
                    self.assertTrue((REPO / gate.cwd / gate.command[1]).is_file())
        for rule in policy.rules:
            if rule.generator:
                self.assertTrue((REPO / rule.generator[1]).is_file())

    def test_secret_patterns_cover_root_and_nested_credentials(self):
        policy = load_protected_paths(REPO / "docs/agent/PROTECTED_PATHS.yaml")
        for source in (".env", ".env.production", "nested/.env.production",
                       "id_rsa", "id_ed25519", "nested/id_rsa",
                       "secrets/credentials.json", "nested/secrets/credentials.json",
                       "private.pem", "nested/private.key"):
            with self.subTest(source=source):
                self.assertTrue(any(
                    rule.action == "deny"
                    and any(fnmatchcase(source, pattern) for pattern in rule.patterns)
                    for rule in policy.rules
                ), source)

    def test_engine_event_authentication_requires_approval(self):
        policy = load_protected_paths(REPO / "docs/agent/PROTECTED_PATHS.yaml")
        for name in ("EventSignatureVerifier.java", "EngineEventsSecurityConfig.java",
                     "EngineEventSignatureFilter.java", "EngineEventKeys.java"):
            source = "platform/services/business/src/main/java/cn/mjy/platform/engine/" + name
            with self.subTest(source=source):
                self.assertTrue((REPO / source).is_file())
                self.assertTrue(any(
                    rule.action == "approval_required"
                    and set(rule.operations) == {"add", "modify", "delete"}
                    and any(fnmatchcase(source, pattern) for pattern in rule.patterns)
                    for rule in policy.rules
                ), source)

    def test_access_and_scoring_changes_require_specialized_dual_database_gates(self):
        matrix = load_gate_matrix(REPO / "docs/agent/GATE_MATRIX.yaml")
        cases = (
            ("plugins/MjyRuntimePolicy/MjyAccessGate.php",
             "platform/deploy/test/run-access-policy.sh"),
            ("platform/tools/publish-gateway/pubgw/logic/scoring.py",
             "platform/deploy/test/run-publish-gateway-parity.sh"),
        )
        for source, script in cases:
            self.assertTrue((REPO / source).is_file())
            self.assertTrue((REPO / script).is_file())
            selected = {
                gate_id
                for profile, patterns in matrix.paths.items()
                if any(fnmatchcase(source, pattern) for pattern in patterns)
                for gate_id in matrix.profiles[profile]
            }
            for database in ("mysql", "pgsql"):
                expected = ("env", "TEST_DB=" + database, script, "--fresh")
                with self.subTest(source=source, database=database):
                    self.assertTrue(any(
                        gate.id in selected and gate.command == expected and gate.cwd == "."
                        for gate in matrix.gates
                    ), expected)


if __name__ == "__main__":
    unittest.main()
