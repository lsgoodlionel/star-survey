import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest


PRODUCTION_DIR = Path(__file__).resolve().parents[1]
COMPOSE_FILE = PRODUCTION_DIR / "compose.yml"
FIXTURE_ENV = Path(__file__).resolve().parent / "fixtures" / "test.env"


class ProductionComposeTest(unittest.TestCase):
    maxDiff = None

    def render_compose(self, env_file=FIXTURE_ENV):
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                str(env_file),
                "-f",
                str(COMPOSE_FILE),
                "config",
                "--format",
                "json",
            ],
            cwd=PRODUCTION_DIR,
            env={"PATH": os.environ.get("PATH", "")},
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("variable is not set", result.stderr)
        return json.loads(result.stdout)

    def test_only_edge_publishes_host_ports(self):
        config = self.render_compose()
        services = config["services"]
        self.assertEqual(
            {
                "edge",
                "admin-web",
                "platform",
                "publish-gateway",
                "engine",
                "engine-init",
                "platform-db",
                "engine-db",
            },
            set(services),
        )
        exposed = {name for name, service in services.items() if service.get("ports")}
        self.assertEqual({"edge"}, exposed)
        published = {
            (str(port["published"]), str(port["target"]))
            for port in services["edge"]["ports"]
        }
        self.assertEqual({("80", "80"), ("443", "443")}, published)

    def test_services_have_operational_guards(self):
        services = self.render_compose()["services"]
        for name, service in services.items():
            with self.subTest(service=name):
                self.assertIn("healthcheck", service)
                self.assertIn("restart", service)
                limits = service.get("deploy", {}).get("resources", {}).get("limits", {})
                self.assertIn("cpus", limits)
                self.assertIn("memory", limits)
        self.assertEqual("no", services["engine-init"]["restart"])
        for name in set(services) - {"engine-init"}:
            self.assertEqual("unless-stopped", services[name]["restart"])
        platform_db_probe = " ".join(services["platform-db"]["healthcheck"]["test"])
        self.assertIn("platform_owner", platform_db_probe)
        self.assertIn("platform_app", platform_db_probe)
        self.assertIn("datname='platform'", platform_db_probe)
        self.assertNotIn("$(", platform_db_probe)

    def test_networks_and_persistent_volumes_are_isolated_by_data_class(self):
        config = self.render_compose()
        self.assertEqual({"edge", "internal"}, set(config["networks"]))
        self.assertTrue(config["networks"]["internal"]["internal"])
        self.assertEqual(
            {
                "caddy-data",
                "caddy-config",
                "platform-db-data",
                "platform-assets",
                "platform-exports",
                "publish-gateway-state",
                "engine-db-data",
                "engine-upload",
                "engine-runtime",
            },
            set(config["volumes"]),
        )
        services = config["services"]
        self.assertEqual({"internal"}, set(services["platform-db"]["networks"]))
        self.assertEqual({"internal"}, set(services["engine-db"]["networks"]))
        self.assertEqual({"internal"}, set(services["publish-gateway"]["networks"]))

    def test_images_and_builds_are_production_only(self):
        config = self.render_compose()
        serialized = json.dumps(config, sort_keys=True)
        self.assertNotIn(":latest", serialized)
        self.assertNotIn("VITE_E2E", serialized)
        self.assertNotIn("/dev/token", serialized)
        self.assertNotIn("platform/deploy/dev", serialized)

        expected_contexts = {
            "admin-web": (PRODUCTION_DIR.parents[1] / "apps" / "admin-web").resolve(),
            "platform": (PRODUCTION_DIR.parents[1] / "services" / "business").resolve(),
            "publish-gateway": (
                PRODUCTION_DIR.parents[1] / "tools" / "publish-gateway"
            ).resolve(),
        }
        for service, expected in expected_contexts.items():
            with self.subTest(service=service):
                actual = Path(config["services"][service]["build"]["context"])
                self.assertEqual(expected, actual)

    def test_secret_files_are_required_and_not_rendered_as_values(self):
        required = {
            "ENGINE_ADMIN_PASSWORD_HASH_FILE",
            "ENGINE_ADMIN_PASSWORD_FILE",
            "ENGINE_DB_PASSWORD_FILE",
            "ENGINE_DB_ROOT_PASSWORD_FILE",
            "PLATFORM_DB_SUPERUSER_PASSWORD_FILE",
            "PLATFORM_DB_OWNER_PASSWORD_FILE",
            "PLATFORM_DB_APP_PASSWORD_FILE",
            "PLATFORM_JWT_HMAC_SECRET_FILE",
            "PLATFORM_ENGINE_EVENTS_SECRET_FILE",
            "PLATFORM_PUBGW_SECRET_FILE",
            "PUBGW_ENGINE_ADMIN_PASSWORD_FILE",
        }
        fixture_lines = FIXTURE_ENV.read_text(encoding="utf-8").splitlines()
        with tempfile.TemporaryDirectory() as tmp:
            for missing in sorted(required):
                with self.subTest(missing=missing):
                    env_file = Path(tmp) / "missing.env"
                    env_file.write_text(
                        "\n".join(
                            line
                            for line in fixture_lines
                            if not line.startswith(missing + "=")
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    result = subprocess.run(
                        [
                            "docker",
                            "compose",
                            "--env-file",
                            str(env_file),
                            "-f",
                            str(COMPOSE_FILE),
                            "config",
                        ],
                        cwd=PRODUCTION_DIR,
                        env={"PATH": os.environ.get("PATH", "")},
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn(missing, result.stderr)

        config = self.render_compose()
        rendered = json.dumps(config, sort_keys=True)
        for marker in ("fixture-db-password", "fixture-jwt-secret", "fixture-admin-password"):
            self.assertNotIn(marker, rendered)

    def test_caddy_routes_and_edge_policy_are_fail_closed(self):
        caddyfile = (PRODUCTION_DIR / "Caddyfile").read_text(encoding="utf-8")
        self.assertIn("handle /v1*", caddyfile)
        self.assertIn("handle /d/*", caddyfile)
        self.assertIn("handle /a/*", caddyfile)
        self.assertIn("handle_path /survey/*", caddyfile)
        self.assertIn("handle_path /engine-admin/*", caddyfile)
        self.assertIn("basic_auth", caddyfile)
        self.assertIn("/index.php/admin", caddyfile)
        self.assertIn("request_body", caddyfile)
        self.assertIn("max_size", caddyfile)
        self.assertIn("Strict-Transport-Security", caddyfile)
        self.assertIn("Content-Security-Policy", caddyfile)
        self.assertIn("X-Frame-Options", caddyfile)
        self.assertIn("trusted_proxies static private_ranges", caddyfile)
        self.assertIn("trusted_proxies_strict", caddyfile)
        self.assertNotIn("header_up X-Forwarded-Host", caddyfile)
        self.assertNotIn("header_up X-Forwarded-Proto", caddyfile)

        wrapper = PRODUCTION_DIR / "init" / "start-edge.sh"
        missing = subprocess.run(
            ["/bin/sh", str(wrapper), "/usr/bin/true"],
            env={"PATH": os.environ.get("PATH", "")},
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(0, missing.returncode)
        self.assertNotIn("fixture", missing.stdout + missing.stderr)

        configured = subprocess.run(
            ["/bin/sh", str(wrapper), "/usr/bin/true"],
            env={
                "PATH": os.environ.get("PATH", ""),
                "ENGINE_ADMIN_PASSWORD_HASH_FILE": str(
                    FIXTURE_ENV.parent / "secrets" / "engine_admin_hash"
                ),
            },
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, configured.returncode, configured.stderr)

    def test_engine_initialization_is_one_shot_and_idempotent(self):
        init_script = PRODUCTION_DIR / "init" / "engine-init.sh"
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            log = temp / "calls.log"
            state = temp / "installed"
            fake_php = temp / "php"
            fake_php.write_text(
                "#!/bin/sh\n"
                "set -eu\n"
                "printf '%s %s\\n' \"$2\" \"${3:-}\" >> \"$FAKE_LOG\"\n"
                "if [ \"$2\" = productionInit ] && [ \"${3:-}\" = status ]; then\n"
                "  [ -f \"$FAKE_STATE\" ] && exit 0\n"
                "  exit 10\n"
                "fi\n"
                "if [ \"$2\" = productionInit ] && [ \"${3:-}\" = install ]; then\n"
                "  touch \"$FAKE_STATE\"\n"
                "fi\n",
                encoding="utf-8",
            )
            fake_php.chmod(0o755)
            admin_password = temp / "admin-password"
            admin_password.write_text("fixture-admin-password-32-bytes-long\n", encoding="utf-8")
            env = {
                "PATH": os.environ.get("PATH", ""),
                "PHP_BIN": str(fake_php),
                "ENGINE_CONSOLE": "/fixture/console.php",
                "FAKE_LOG": str(log),
                "FAKE_STATE": str(state),
                "ENGINE_ADMIN_USER": "fixture-operator",
                "ENGINE_ADMIN_EMAIL": "operator@example.invalid",
                "ENGINE_ADMIN_PASSWORD_FILE": str(admin_password),
            }
            first = subprocess.run(
                ["/bin/sh", str(init_script)], env=env, capture_output=True, text=True
            )
            second = subprocess.run(
                ["/bin/sh", str(init_script)], env=env, capture_output=True, text=True
            )
            self.assertEqual(0, first.returncode, first.stderr)
            self.assertEqual(0, second.returncode, second.stderr)
            calls = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(1, calls.count("productionInit install"))
            self.assertEqual(2, calls.count("updatedb "))
            self.assertEqual(2, calls.count("productionInit plugins"))
            self.assertNotIn("fixture-admin-password", "\n".join(calls))

    def test_engine_config_uses_real_survey_prefix_and_https_settings(self):
        config = (PRODUCTION_DIR / "init" / "config.production.php").read_text(
            encoding="utf-8"
        )
        self.assertIn("$baseUrl = '/survey'", config)
        self.assertIn("HTTP_X_FORWARDED_PREFIX", config)
        self.assertIn("'force_ssl' => 'on'", config)
        self.assertIn("'ssl_disable_alert' => true", config)
        self.assertIn("ENGINE_DB_PASSWORD_FILE", config)
        self.assertNotIn("private-db-pass", config)
        self.assertNotIn("function productionSecret", config)
        self.assertIn("$productionSecret = static function", config)

    def test_container_entrypoint_scripts_are_executable(self):
        for name in (
            "start-edge.sh",
            "run-with-secrets.sh",
            "engine-init.sh",
            "init-platform-db.sh",
        ):
            with self.subTest(script=name):
                mode = (PRODUCTION_DIR / "init" / name).stat().st_mode
                self.assertTrue(mode & stat.S_IXUSR)


if __name__ == "__main__":
    unittest.main()
