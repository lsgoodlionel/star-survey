"""引擎实例配置：口令只从环境变量来，配置有任何毛病都拒绝启动。"""

import json
import os
import tempfile
import unittest

from pubgw.engines import ConfigError, load_engines

GOOD = {
    "hd-engine-01": {
        "rpcUrl": "http://survey-test-web/index.php/admin/remotecontrol",
        "user": "admin",
        "passwordEnv": "PUBGW_ENGINE_HD01_PASSWORD",
    }
}
ENV = {"PUBGW_ENGINE_HD01_PASSWORD": "s3cret-engine-pw"}


class LoadEnginesTest(unittest.TestCase):
    def setUp(self):
        self.paths = []

    def tearDown(self):
        for path in self.paths:
            os.unlink(path)

    def write(self, payload):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        handle.write(payload if isinstance(payload, str) else json.dumps(payload))
        handle.close()
        self.paths.append(handle.name)
        return handle.name

    def load(self, payload, env=ENV):
        return load_engines(self.write(payload), env)

    def refused(self, payload, env=ENV):
        with self.assertRaises(ConfigError) as caught:
            self.load(payload, env)
        return str(caught.exception)

    def with_entry(self, **overrides):
        entry = dict(GOOD["hd-engine-01"], **overrides)
        return {"hd-engine-01": {k: v for k, v in entry.items() if v is not None}}

    def test_loads_an_instance_with_its_password_from_the_environment(self):
        engines = self.load(GOOD)
        engine = engines["hd-engine-01"]

        self.assertEqual("http://survey-test-web/index.php/admin/remotecontrol", engine.rpc_url)
        self.assertEqual("admin", engine.user)
        self.assertEqual("s3cret-engine-pw", engine.password)

    def test_the_password_never_appears_in_the_repr(self):
        engine = self.load(GOOD)["hd-engine-01"]

        self.assertNotIn("s3cret-engine-pw", repr(engine))

    def test_the_result_is_read_only(self):
        engines = self.load(GOOD)

        with self.assertRaises(TypeError):
            engines["other"] = engines["hd-engine-01"]

    def test_refuses_a_missing_password_variable(self):
        self.assertIn("PUBGW_ENGINE_HD01_PASSWORD", self.refused(GOOD, env={}))

    def test_refuses_an_empty_password_variable(self):
        self.refused(GOOD, env={"PUBGW_ENGINE_HD01_PASSWORD": ""})

    def test_the_error_message_never_contains_the_password(self):
        message = self.refused(self.with_entry(user=""))

        self.assertNotIn("s3cret-engine-pw", message)

    def test_refuses_a_literal_password_in_the_file(self):
        self.refused(self.with_entry(password="inline"))

    def test_refuses_unknown_keys(self):
        self.refused(self.with_entry(timeout=5))

    def test_refuses_missing_fields(self):
        for name in ("rpcUrl", "user", "passwordEnv"):
            with self.subTest(field=name):
                self.refused(self.with_entry(**{name: None}))

    def test_refuses_a_non_http_url(self):
        self.refused(self.with_entry(rpcUrl="file:///etc/passwd"))

    def test_refuses_credentials_embedded_in_the_url(self):
        self.refused(self.with_entry(rpcUrl="http://admin:pw@engine/index.php/admin/remotecontrol"))

    def test_refuses_a_malformed_instance_id(self):
        self.refused({"bad id!": GOOD["hd-engine-01"]})

    def test_refuses_an_empty_configuration(self):
        self.refused({})

    def test_refuses_a_file_that_is_not_json(self):
        self.refused("{not json")

    def test_refuses_a_top_level_that_is_not_an_object(self):
        self.refused([GOOD])

    def test_refuses_a_missing_file(self):
        with self.assertRaises(ConfigError):
            load_engines("/nonexistent/pubgw-engines.json", ENV)


if __name__ == "__main__":
    unittest.main()
