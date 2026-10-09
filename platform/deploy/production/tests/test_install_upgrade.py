"""Native clean-host acceptance.

This suite is intentionally skipped unless a CI runner supplies real release assets,
DNS/TLS and Docker. When enabled, every step invokes the shipped surveyctl; there is
no fake-success path.
"""

import os
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import urllib.request


PRODUCTION_DIR = Path(__file__).resolve().parents[1]
SURVEYCTL = PRODUCTION_DIR / "surveyctl"
REQUIREMENT = PRODUCTION_DIR / "native-acceptance.requirement.json"


class NativeAcceptancePolicyTest(unittest.TestCase):
    def test_release_plan_has_a_machine_readable_blocking_native_matrix(self):
        policy = json.loads(REQUIREMENT.read_text(encoding="utf-8"))
        self.assertTrue(policy["releaseBlocking"])
        self.assertEqual(["22.04", "24.04"], policy["matrix"]["ubuntu"])
        self.assertEqual(["amd64", "arm64"], policy["matrix"]["architecture"])
        self.assertEqual("SURVEY_PRODUCTION_E2E=1", policy["enableEnvironment"])
        self.assertIn("doctor-restored", policy["requiredJourney"])


@unittest.skipUnless(os.environ.get("SURVEY_PRODUCTION_E2E") == "1", "native production acceptance is opt-in")
class NativeCleanHostAcceptanceTest(unittest.TestCase):
    def required(self, name):
        value = os.environ.get(name)
        self.assertTrue(value, f"{name} is required when SURVEY_PRODUCTION_E2E=1")
        return value

    def run_ctl(self, target, *args):
        result = subprocess.run(
            [str(SURVEYCTL), "--target", str(target), *args],
            capture_output=True,
            text=True,
            timeout=1800,
        )
        self.assertEqual(0, result.returncode, f"surveyctl {' '.join(args)} failed: {result.stderr}")
        return result

    def test_install_doctor_backup_upgrade_restore_and_uninstall(self):
        initial_manifest = self.required("SURVEY_E2E_INITIAL_MANIFEST")
        upgrade_manifest = self.required("SURVEY_E2E_UPGRADE_MANIFEST")
        public_host = self.required("SURVEY_E2E_PUBLIC_HOST")
        admin_user = self.required("SURVEY_E2E_ADMIN_USER")
        admin_email = self.required("SURVEY_E2E_ADMIN_EMAIL")
        with tempfile.TemporaryDirectory(prefix="survey-clean-host-") as root:
            target = Path(root) / "survey"
            self.run_ctl(target, "install", "--manifest", initial_manifest, "--public-host", public_host, "--admin-user", admin_user, "--admin-email", admin_email)
            self.run_ctl(target, "doctor")
            request = urllib.request.Request(f"https://{public_host}/.well-known/survey-health", headers={"User-Agent": "survey-clean-host/1"})
            with urllib.request.urlopen(request, timeout=15) as response:
                self.assertEqual(200, response.status)
                self.assertEqual("survey-production-v1", response.headers.get("X-Survey-Deployment"))
            self.run_ctl(target, "backup", "--output", "acceptance-before-upgrade")
            self.run_ctl(target, "upgrade", "--manifest", upgrade_manifest)
            self.run_ctl(target, "doctor")
            self.run_ctl(target, "restore", "acceptance-before-upgrade")
            self.run_ctl(target, "doctor")
            self.run_ctl(target, "uninstall")


if __name__ == "__main__":
    unittest.main()
