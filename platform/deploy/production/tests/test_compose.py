import json
import os
from pathlib import Path
import re
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

    def adapt_caddy(self):
        fixture = dict(
            line.split("=", 1)
            for line in FIXTURE_ENV.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")
        )
        password_hash = (
            FIXTURE_ENV.parent / "secrets" / "engine_admin_hash"
        ).read_text(encoding="utf-8").strip()
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-e",
                f"PUBLIC_HOST={fixture['PUBLIC_HOST']}",
                "-e",
                f"ENGINE_ADMIN_USER={fixture['ENGINE_ADMIN_USER']}",
                "-e",
                f"ENGINE_ADMIN_PASSWORD_HASH={password_hash}",
                "-v",
                f"{PRODUCTION_DIR / 'Caddyfile'}:/etc/caddy/Caddyfile:ro",
                fixture["CADDY_IMAGE"],
                "caddy",
                "adapt",
                "--config",
                "/etc/caddy/Caddyfile",
                "--adapter",
                "caddyfile",
            ],
            cwd=PRODUCTION_DIR,
            env={"PATH": os.environ.get("PATH", "")},
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        return json.loads(result.stdout)

    def walk_dicts(self, value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from self.walk_dicts(child)
        elif isinstance(value, list):
            for child in value:
                yield from self.walk_dicts(child)

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
        expected_networks = {
            "edge": {"edge"},
            "admin-web": {"edge"},
            "platform": {"edge", "internal"},
            "publish-gateway": {"internal"},
            "engine": {"edge", "internal"},
            "engine-init": {"internal"},
            "platform-db": {"internal"},
            "engine-db": {"internal"},
        }
        for service, expected in expected_networks.items():
            with self.subTest(service=service):
                self.assertEqual(expected, set(services[service]["networks"]))

    def test_production_compose_is_source_free_and_images_are_versioned(self):
        config = self.render_compose()
        serialized = json.dumps(config, sort_keys=True)
        self.assertNotIn(":latest", serialized)
        self.assertNotIn("VITE_E2E", serialized)
        self.assertNotIn("/dev/token", serialized)
        self.assertNotIn("platform/deploy/dev", serialized)

        for service, definition in config["services"].items():
            with self.subTest(service=service):
                self.assertNotIn("build", definition)
                image = definition["image"]
                leaf = image.rsplit("/", 1)[-1]
                has_digest = "@sha256:" in image
                has_version_tag = ":" in leaf and not leaf.endswith(":latest")
                self.assertTrue(has_digest or has_version_tag, image)

        for path in (COMPOSE_FILE, PRODUCTION_DIR / "env.example"):
            self.assertNotIn("tests/fixtures", path.read_text(encoding="utf-8"))

        fixture_lines = FIXTURE_ENV.read_text(encoding="utf-8").splitlines()
        required_images = {
            "CADDY_IMAGE",
            "ADMIN_IMAGE",
            "PLATFORM_IMAGE",
            "PUBLISH_GATEWAY_IMAGE",
            "ENGINE_IMAGE",
            "POSTGRES_IMAGE",
            "MARIADB_IMAGE",
        }
        with tempfile.TemporaryDirectory() as tmp:
            for missing in sorted(required_images):
                with self.subTest(missing=missing):
                    env_file = Path(tmp) / "missing-image.env"
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

        sensitive = re.compile(r"(PASSWORD|SECRET|TOKEN|PRIVATE_KEY)", re.I)
        for service_name, service in config["services"].items():
            for assignment in service.get("environment", []):
                key, _, value = assignment.partition("=")
                if sensitive.search(key):
                    with self.subTest(service=service_name, key=key):
                        self.assertTrue(
                            key.endswith("_FILE") or value.startswith("/run/secrets/"),
                            assignment,
                        )
            for assignment in service.get("command") or []:
                if "=" not in assignment:
                    continue
                key, value = assignment.split("=", 1)
                if sensitive.search(key):
                    with self.subTest(service=service_name, key=key):
                        self.assertTrue(value.startswith("/run/secrets/"), assignment)

    def test_caddy_routes_and_edge_policy_are_fail_closed(self):
        caddyfile = (PRODUCTION_DIR / "Caddyfile").read_text(encoding="utf-8")
        self.assertNotIn("handle /v1*", caddyfile)
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

    def test_caddy_admin_paths_are_canonical_and_authenticated(self):
        adapted = self.adapt_caddy()
        nodes = list(self.walk_dicts(adapted))
        admin_route = next(
            node
            for node in nodes
            if node.get("match") == [{"path": ["/engine-admin/*"]}]
            and node.get("handle", [{}])[0].get("handler") == "subroute"
        )
        routes = admin_route["handle"][0]["routes"]
        self.assertEqual(
            "/engine-admin",
            routes[0]["handle"][0]["strip_path_prefix"],
        )
        root_rewrite = next(
            route
            for route in routes
            if route.get("match") == [{"path": ["/"]}]
        )
        self.assertEqual("/index.php/admin", root_rewrite["handle"][0]["uri"])
        self.assertFalse(
            any(
                node.get("handler") == "rewrite"
                and "{http.request.uri}" in node.get("uri", "")
                for node in self.walk_dicts(admin_route)
            )
        )
        handlers = [
            node.get("handler")
            for node in self.walk_dicts(admin_route)
            if node.get("handler") in {"authentication", "reverse_proxy"}
        ]
        self.assertEqual(["authentication", "reverse_proxy"], handlers)

        def mapped_path(external_path):
            stripped = external_path.removeprefix("/engine-admin") or "/"
            return "/index.php/admin" if stripped == "/" else stripped

        self.assertEqual("/index.php/admin", mapped_path("/engine-admin/"))
        self.assertEqual(
            "/index.php/admin/authentication/sa/login",
            mapped_path("/engine-admin/index.php/admin/authentication/sa/login"),
        )

    def test_caddy_v1_matcher_does_not_capture_neighboring_paths(self):
        nodes = list(self.walk_dicts(self.adapt_caddy()))
        platform_routes = [
            node
            for node in nodes
            if node.get("match")
            and any(
                child.get("dial") == "platform:8080"
                for child in self.walk_dicts(node)
            )
        ]
        matched_paths = {
            path
            for route in platform_routes
            for matcher in route["match"]
            for path in matcher.get("path", [])
        }
        self.assertIn("/v1", matched_paths)
        self.assertIn("/v1/*", matched_paths)
        self.assertNotIn("/v1*", matched_paths)

    def test_caddy_public_handler_order_keeps_restricted_routes_first(self):
        nodes = list(self.walk_dicts(self.adapt_caddy()))
        public_subroute = next(
            node
            for node in nodes
            if node.get("handler") == "subroute"
            and any(
                route.get("match") == [{"path": ["/engine-admin/*"]}]
                for route in node.get("routes", [])
            )
        )
        route_paths = [
            tuple(path for matcher in route.get("match", []) for path in matcher.get("path", []))
            for route in public_subroute["routes"]
        ]
        admin_index = route_paths.index(("/engine-admin/*",))
        survey_index = route_paths.index(("/survey/*",))
        api_index = route_paths.index(("/v1", "/v1/*"))
        fallback_index = max(i for i, paths in enumerate(route_paths) if not paths)
        self.assertLess(admin_index, survey_index)
        self.assertLess(survey_index, api_index)
        self.assertLess(api_index, fallback_index)

    def test_engine_and_edge_healthchecks_exercise_http_application_routes(self):
        services = self.render_compose()["services"]
        engine_probe = " ".join(services["engine"]["healthcheck"]["test"])
        edge_probe = " ".join(services["edge"]["healthcheck"]["test"])
        self.assertIn("http://127.0.0.1/production-health.php", engine_probe)
        self.assertIn("file_get_contents", engine_probe)
        self.assertIn("php", engine_probe)
        self.assertIn("productionInit status", engine_probe)
        self.assertIn("CException", engine_probe)
        self.assertNotIn("fsockopen", engine_probe)
        self.assertIn("http://127.0.0.1/.well-known/survey-health", edge_probe)
        self.assertNotIn("caddy version", edge_probe)
        caddyfile = (PRODUCTION_DIR / "Caddyfile").read_text(encoding="utf-8")
        self.assertIn("@deployment_health path /.well-known/survey-health", caddyfile)
        self.assertIn("header X-Survey-Deployment survey-production-v1", caddyfile)
        self.assertIn('respond "survey-production-v1" 200', caddyfile)
        self.assertTrue(
            any(
                mount["target"] == "/var/www/html/production-health.php"
                and mount["read_only"]
                for mount in services["engine"]["volumes"]
            )
        )
        health_source = (PRODUCTION_DIR / "init" / "production-health.php").read_text(
            encoding="utf-8"
        )
        self.assertIn("new PDO", health_source)
        self.assertIn("mjy_production_init_v1", health_source)
        self.assertIn("http_response_code(503)", health_source)

    def test_engine_initialization_recovers_after_each_partial_phase(self):
        init_script = PRODUCTION_DIR / "init" / "engine-init.sh"
        for fail_after in ("tables", "admin", "settings"):
            with self.subTest(fail_after=fail_after), tempfile.TemporaryDirectory() as tmp:
                temp = Path(tmp)
                log = temp / "calls.log"
                state = temp / "state"
                state.mkdir()
                fake_php = temp / "php"
                fake_php.write_text(
                    "#!/bin/sh\n"
                    "set -eu\n"
                    "command=${2:-}\n"
                    "action=${3:-}\n"
                    "printf '%s %s\\n' \"$command\" \"$action\" >> \"$FAKE_LOG\"\n"
                    "[ \"$command\" = productionInit ] || exit 0\n"
                    "case \"$action\" in\n"
                    "  status)\n"
                    "    [ -f \"$FAKE_STATE/complete\" ] && exit 0\n"
                    "    [ -f \"$FAKE_STATE/tables\" ] && exit 20\n"
                    "    exit 10;;\n"
                    "  install) touch \"$FAKE_STATE/tables\";;\n"
                    "  admin) touch \"$FAKE_STATE/admin\";;\n"
                    "  settings) touch \"$FAKE_STATE/settings\";;\n"
                    "  plugins) touch \"$FAKE_STATE/plugins\";;\n"
                    "  complete)\n"
                    "    for phase in tables admin settings plugins; do\n"
                    "      [ -f \"$FAKE_STATE/$phase\" ] || exit 30\n"
                    "    done\n"
                    "    touch \"$FAKE_STATE/complete\";;\n"
                    "esac\n"
                    "if [ \"${FAKE_FAIL_AFTER:-}\" = \"$action\" ]; then exit 75; fi\n",
                    encoding="utf-8",
                )
                fake_php.chmod(0o755)
                admin_password = temp / "admin-password"
                admin_password.write_text(
                    "fixture-admin-password-32-bytes-long\n", encoding="utf-8"
                )
                env = {
                    "PATH": os.environ.get("PATH", ""),
                    "PHP_BIN": str(fake_php),
                    "ENGINE_CONSOLE": "/fixture/console.php",
                    "FAKE_LOG": str(log),
                    "FAKE_STATE": str(state),
                    "FAKE_FAIL_AFTER": "install" if fail_after == "tables" else fail_after,
                    "ENGINE_ADMIN_USER": "fixture-operator",
                    "ENGINE_ADMIN_EMAIL": "operator@example.invalid",
                    "ENGINE_ADMIN_PASSWORD_FILE": str(admin_password),
                }
                interrupted = subprocess.run(
                    ["/bin/sh", str(init_script)], env=env, capture_output=True, text=True
                )
                self.assertEqual(75, interrupted.returncode, interrupted.stderr)
                env.pop("FAKE_FAIL_AFTER")
                recovered = subprocess.run(
                    ["/bin/sh", str(init_script)], env=env, capture_output=True, text=True
                )
                repeated = subprocess.run(
                    ["/bin/sh", str(init_script)], env=env, capture_output=True, text=True
                )
                self.assertEqual(0, recovered.returncode, recovered.stderr)
                self.assertEqual(0, repeated.returncode, repeated.stderr)
                self.assertTrue((state / "complete").exists())
                calls = log.read_text(encoding="utf-8").splitlines()
                self.assertEqual(1, calls.count("productionInit install"))
                self.assertEqual(1, calls.count("productionInit complete"))
                self.assertNotIn("fixture-admin-password", "\n".join(calls))

    def test_engine_init_distinguishes_empty_database_from_partial_schema(self):
        source = (PRODUCTION_DIR / "init" / "ProductionInitCommand.php").read_text(
            encoding="utf-8"
        )
        self.assertIn("getTableNames()", source)
        self.assertIn("Partial LimeSurvey schema detected", source)
        self.assertIn("return 30", source)

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
