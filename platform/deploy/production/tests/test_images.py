import re
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
DOCKERFILES = {
    "admin-web": REPOSITORY_ROOT / "platform/apps/admin-web/Dockerfile",
    "business": REPOSITORY_ROOT / "platform/services/business/Dockerfile",
    "publish-gateway": REPOSITORY_ROOT / "platform/tools/publish-gateway/Dockerfile",
}


def dockerfile(name: str) -> str:
    path = DOCKERFILES[name]
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def runtime_stage(contents: str) -> str:
    stages = re.split(r"(?m)^FROM\s+", contents)
    return "FROM " + stages[-1]


class ProductionImagePolicyTest(unittest.TestCase):
    def test_every_runtime_is_non_root_and_health_checked(self):
        for name, path in DOCKERFILES.items():
            with self.subTest(image=name):
                contents = dockerfile(name)
                self.assertTrue(contents, f"missing Dockerfile: {path}")
                runtime = runtime_stage(contents)
                self.assertRegex(runtime, r"(?m)^USER\s+(?!0(?:\D|$))\d+(?::\d+)?\s*$")
                self.assertRegex(runtime, r"(?m)^HEALTHCHECK\s+")
                self.assertNotIn("chmod 777", contents)

    def test_every_image_carries_release_metadata(self):
        for name, path in DOCKERFILES.items():
            with self.subTest(image=name):
                contents = dockerfile(name)
                self.assertTrue(contents, f"missing Dockerfile: {path}")
                runtime = runtime_stage(contents)
                for argument in ("VERSION", "REVISION", "CREATED"):
                    self.assertRegex(runtime, rf"(?m)^ARG\s+{argument}(?:=\S*)?\s*$")
                self.assertIn('org.opencontainers.image.version="$VERSION"', runtime)
                self.assertIn('org.opencontainers.image.revision="$REVISION"', runtime)
                self.assertIn('org.opencontainers.image.created="$CREATED"', runtime)

    def test_admin_web_defaults_to_production_and_retains_explicit_e2e_build(self):
        contents = dockerfile("admin-web")
        runtime = runtime_stage(contents)
        self.assertGreaterEqual(len(re.findall(r"(?m)^FROM\s+", contents)), 2)
        self.assertRegex(contents, r"(?m)^ARG\s+VITE_E2E=false\s*$")
        self.assertRegex(contents, r"(?m)^ENV\s+VITE_E2E=\$\{VITE_E2E\}\s*$")
        self.assertRegex(
            contents,
            r'if \[ "\$VITE_E2E" = "true" \]; then npm run assert:e2e-bundle; '
            r"else npm run assert:production-bundle; fi",
        )
        self.assertRegex(runtime, r"(?m)^COPY\s+--from=build\s+/app/dist\s+/usr/share/nginx/html\s*$")
        self.assertRegex(runtime, r"chown\s+-R\s+10001:10001\s+[^\n]*\s/run(?:\s|$)")
        self.assertNotRegex(runtime, r"(?m)^COPY\s+(?!--from=)")
        self.assertNotRegex(runtime, r"(?m)^VOLUME\s+")

    def test_business_uses_java_21_builder_and_minimal_jre_runtime(self):
        contents = dockerfile("business")
        runtime = runtime_stage(contents)
        self.assertRegex(
            contents,
            r"(?m)^FROM\s+maven:\S*eclipse-temurin-21\S*\s+AS\s+build\s*$",
        )
        self.assertRegex(runtime, r"(?m)^FROM\s+eclipse-temurin:\S*21\S*jre\S*")
        self.assertRegex(contents, r"mvn\s+-B\s+-DskipTests\s+package")
        self.assertRegex(runtime, r"(?m)^COPY\s+--from=build\s+\S+\.jar\s+/app/business\.jar\s*$")
        self.assertNotRegex(runtime, r"(?m)^COPY\s+(?!--from=)")
        self.assertIn("/var/lib/survey", runtime)
        self.assertNotIn("/root/.m2", runtime)

    def test_gateway_pins_python_patch_and_only_persists_state(self):
        contents = dockerfile("publish-gateway")
        runtime = runtime_stage(contents)
        self.assertRegex(
            runtime,
            r"(?m)^FROM\s+python:\d+\.\d+\.\d+-alpine\d+\.\d+\s*$",
        )
        self.assertRegex(runtime, r"(?m)^USER\s+10001:10001\s*$")
        self.assertRegex(runtime, r'(?m)^VOLUME\s+\["/var/lib/pubgw"\]\s*$')
        self.assertRegex(runtime, r"(?m)^COPY\s+--chown=10001:10001\s+pubgw\s+/app/pubgw\s*$")
        self.assertNotRegex(runtime, r"(?m)^COPY\s+.*tests")


if __name__ == "__main__":
    unittest.main()
