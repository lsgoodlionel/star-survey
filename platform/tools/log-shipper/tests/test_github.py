"""上传测试：路径按应用分区、失败可重试、令牌绝不出现在日志或异常里。"""

import base64
import json
import tempfile
import unittest
from pathlib import Path

from logship.github import GitHubUploader, UploadError


class FakeHttp:
    """替代真实 HTTP：记录请求，按脚本返回响应或抛错。"""

    def __init__(self, responses=None):
        self.requests = []
        self.responses = list(responses or [])

    def put_json(self, url, payload, token):
        self.requests.append({"url": url, "payload": payload, "token": token})
        if not self.responses:
            return {"content": {"html_url": url}}
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class GitHubUploaderTest(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.bundle = Path(self._tempdir.name) / "bundle.tar.gz"
        self.bundle.write_bytes(b"bundle-bytes")
        self.manifest = {"app": "survey", "fingerprint": "abc123", "errorSummary": "boom"}

    def tearDown(self):
        self._tempdir.cleanup()

    def uploader(self, http=None, **kwargs):
        options = dict(repository="lsgoodlionel/paper", token="ghp-secret-token", http=http or FakeHttp(), retries=1)
        options.update(kwargs)
        return GitHubUploader(**options)

    def test_paths_are_namespaced_per_app_and_incident(self):
        http = FakeHttp()

        result = self.uploader(http).upload(
            self.bundle, self.manifest, app="survey", incident_id="20260920T081503Z-abc123"
        )

        paths = [request["payload"]["path"] if "path" in request["payload"] else request["url"] for request in http.requests]
        for path in paths:
            self.assertIn("incidents/survey/2026/09/20/20260920T081503Z-abc123/", path)
        self.assertIn("incidents/survey/2026/09/20/20260920T081503Z-abc123/", result.directory)

    def test_uploads_manifest_and_bundle(self):
        http = FakeHttp()

        self.uploader(http).upload(self.bundle, self.manifest, app="survey", incident_id="20260920T081503Z-abc123")

        uploaded = [request["url"].rsplit("/", 1)[-1] for request in http.requests]
        self.assertIn("manifest.json", uploaded)
        self.assertIn("bundle.tar.gz", uploaded)

    def test_bundle_is_sent_base64_encoded(self):
        http = FakeHttp()

        self.uploader(http).upload(self.bundle, self.manifest, app="survey", incident_id="i1")

        bundle_request = [request for request in http.requests if request["url"].endswith("bundle.tar.gz")][0]
        self.assertEqual(base64.b64decode(bundle_request["payload"]["content"]), b"bundle-bytes")

    def test_retries_a_transient_failure_then_succeeds(self):
        http = FakeHttp([UploadError("502 Bad Gateway"), {"content": {}}, {"content": {}}])

        result = self.uploader(http, retries=2).upload(self.bundle, self.manifest, app="survey", incident_id="i1")

        self.assertTrue(result.uploaded)
        self.assertEqual(len(http.requests), 3)

    def test_gives_up_after_retries_and_keeps_the_bundle(self):
        http = FakeHttp([UploadError("500"), UploadError("500")])

        with self.assertRaises(UploadError):
            self.uploader(http, retries=1).upload(self.bundle, self.manifest, app="survey", incident_id="i1")
        self.assertTrue(self.bundle.exists())

    def test_token_never_appears_in_error_messages(self):
        http = FakeHttp([UploadError("401 Unauthorized for token ghp-secret-token")])

        with self.assertRaises(UploadError) as raised:
            self.uploader(http, retries=0).upload(self.bundle, self.manifest, app="survey", incident_id="i1")

        self.assertNotIn("ghp-secret-token", str(raised.exception))

    def test_dry_run_uploads_nothing(self):
        http = FakeHttp()

        result = self.uploader(http, dry_run=True).upload(
            self.bundle, self.manifest, app="survey", incident_id="i1"
        )

        self.assertEqual(http.requests, [])
        self.assertFalse(result.uploaded)
        self.assertTrue(result.directory)

    def test_manifest_is_uploaded_as_readable_json(self):
        http = FakeHttp()

        self.uploader(http).upload(self.bundle, self.manifest, app="survey", incident_id="i1")

        manifest_request = [request for request in http.requests if request["url"].endswith("manifest.json")][0]
        decoded = json.loads(base64.b64decode(manifest_request["payload"]["content"]).decode("utf-8"))
        self.assertEqual(decoded["fingerprint"], "abc123")


if __name__ == "__main__":
    unittest.main()
