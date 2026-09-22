"""带访问策略的发布：插件必须回读到同一份策略，否则回滚（策略静默丢失＝失效开放）。"""

import base64
import hashlib
import json
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ElementTree

from pubgw.policy.probe import HttpPolicyProbe
from pubgw.publish import Publisher
from pubgw.rpc import RemoteControlClient, RpcError
from pubgw.service import PublishService, http_policy_probe
from pubgw.store import ResultStore

from .fakes import FakeEngine
from .gateway_support import INSTANCE, NOW, SECRET, encode, engine_config, envelope, make_service, signed_headers
from .policy_fixtures import full_policy, policy_definition, policy_payload, with_participants

_NATIVE = ("startdate", "expires", "usecaptcha")


class PolicyAwareEngine(FakeEngine):
    """记住导入的 LSS：原生设置从 LSS 回读，插件状态端点读 LSS 里的 plugin_settings。"""

    def __init__(self, *args, plugin_active=True, tamper=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.plugin_active = plugin_active
        self.tamper = tamper
        self.probed = []

    def _get_survey_properties(self, key, sid, properties=None):
        values = super()._get_survey_properties(key, sid, properties)
        survey = self.surveys.get(sid)
        if survey is not None:
            row = self._document(sid).find("surveys/rows/row")
            for name in _NATIVE:
                node = row.find(name)
                if node is not None and name not in self.applied_settings.get(sid, {}):
                    values[name] = node.text or ""
        return values

    def _document(self, sid):
        return ElementTree.fromstring(base64.b64decode(self.surveys[sid]["lss"]).decode("utf-8"))

    def probe(self, survey_id):
        self.probed.append(survey_id)
        if not self.plugin_active:
            return None
        survey = self.surveys.get(survey_id)
        values = []
        if survey is not None:
            root = self._document(survey_id)
            values = [row.find("value").text for row in root.findall("plugin_settings/rows/row")]
        if self.tamper:
            values = [self.tamper(value) for value in values]
        digest = hashlib.sha256(values[0].encode()).hexdigest() if len(values) == 1 else None
        return {"plugin": "MjyRuntimePolicy", "active": True, "surveyId": survey_id,
                "rows": len(values), "valid": len(values) == 1, "policyDigest": digest}


def publish(engine, definition, probe="engine"):
    client = RemoteControlClient(engine.transport)
    client.login("admin", "password")
    policy_probe = engine.probe if probe == "engine" else probe
    return Publisher(client, engine_instance="hd-engine-01", policy_probe=policy_probe).publish(definition)


class VerifiedPolicyTest(unittest.TestCase):
    def setUp(self):
        self.definition = policy_definition(full_policy(), with_participants())
        self.engine = PolicyAwareEngine(self.definition)
        self.result = publish(self.engine, self.definition)

    def test_publish_succeeds_and_reports_the_digest(self):
        self.assertTrue(self.result.ok, self.result.failures)
        self.assertEqual(64, len(self.result.to_dict()["policyDigest"]))

    def test_plugin_is_checked_before_activation(self):
        methods = self.engine.methods()
        self.assertEqual([self.result.survey_id], self.engine.probed)
        self.assertIn("activate_survey", methods)

    def test_native_settings_are_read_back(self):
        self.assertNotIn("startdate", self.engine.applied_settings.get(self.result.survey_id, {}))


class UnenforcedPolicyTest(unittest.TestCase):
    def definition(self):
        return policy_definition(full_policy(), with_participants())

    def assert_rolled_back(self, result, code):
        self.assertFalse(result.ok)
        self.assertEqual("apply", result.failed_stage)
        self.assertTrue(result.rolled_back, result.failures)
        self.assertTrue(any(code in failure for failure in result.failures), result.failures)

    def test_inactive_plugin_rolls_back_before_activation(self):
        engine = PolicyAwareEngine(self.definition(), plugin_active=False)
        result = publish(engine, self.definition())
        self.assert_rolled_back(result, "E_POLICY_NOT_ENFORCED")
        self.assertNotIn("activate_survey", engine.methods())

    def test_digest_mismatch_rolls_back(self):
        engine = PolicyAwareEngine(self.definition(), tamper=lambda value: value.replace("1800", "9999"))
        self.assert_rolled_back(publish(engine, self.definition()), "E_POLICY_DIGEST_MISMATCH")

    def test_policy_without_a_probe_is_never_published(self):
        engine = PolicyAwareEngine(self.definition())
        result = publish(engine, self.definition(), probe=None)
        self.assert_rolled_back(result, "E_POLICY_UNVERIFIED")

    def test_probe_transport_failure_rolls_back(self):
        def broken(survey_id):
            raise RpcError("policyStatus", "connection refused")

        engine = PolicyAwareEngine(self.definition())
        self.assert_rolled_back(publish(engine, self.definition(), probe=broken), "policyStatus")

    def test_engine_that_cannot_parse_the_policy_rolls_back(self):
        engine = PolicyAwareEngine(self.definition())
        original = engine.probe
        engine.probe = lambda sid: dict(original(sid), valid=False)
        self.assert_rolled_back(publish(engine, self.definition()), "E_POLICY_REJECTED")


class NoPluginNeededTest(unittest.TestCase):
    def test_captcha_only_policy_needs_no_probe(self):
        definition = policy_definition({"policyVersion": 1, "access": {"captcha": True}})
        engine = PolicyAwareEngine(definition)
        result = publish(engine, definition, probe=None)
        self.assertTrue(result.ok, result.failures)
        self.assertNotIn("policyDigest", result.to_dict())


class ServiceStatusTest(unittest.TestCase):
    def setUp(self):
        self.state_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.state_dir)

    def test_invalid_policy_is_422_and_never_touches_the_engine(self):
        payload = policy_payload({"policyVersion": 1, "access": {"password": "plaintext"}})
        engine = PolicyAwareEngine(policy_definition({"policyVersion": 1, "access": {"captcha": True}}))
        service = make_service(engine, self.state_dir)
        body = encode(envelope(definition=payload))
        response = service.publish(signed_headers(body), body)

        self.assertEqual(422, response.status)
        decoded = json.loads(response.body.decode("utf-8"))
        self.assertEqual("validate", decoded["result"]["failedStage"])
        self.assertTrue(any("E_POLICY_PLAINTEXT_PASSWORD" in f for f in decoded["result"]["failures"]))
        self.assertNotIn("plaintext", response.body.decode("utf-8"))
        self.assertEqual([], engine.calls)


    def test_service_wires_the_probe_per_engine_instance(self):
        definition = policy_definition(full_policy(), with_participants())
        engine = PolicyAwareEngine(definition)
        service = PublishService(
            engines={INSTANCE: engine_config()},
            store=ResultStore(self.state_dir + "/results.sqlite3"),
            secret=SECRET,
            transport_factory=lambda config: engine.transport,
            now=lambda: NOW,
            policy_probe_factory=lambda config: engine.probe,
        )
        body = encode(envelope(definition=policy_payload(full_policy(), with_participants())))
        response = service.publish(signed_headers(body), body)

        self.assertEqual(200, response.status, response.body)
        self.assertEqual(64, len(json.loads(response.body.decode("utf-8"))["result"]["policyDigest"]))

    def test_default_probe_derives_from_the_configured_rpc_url(self):
        self.assertIsInstance(http_policy_probe(engine_config()), HttpPolicyProbe)


class HttpProbeTest(unittest.TestCase):
    def test_status_url_is_derived_from_the_rpc_endpoint(self):
        seen = []
        probe = HttpPolicyProbe.from_rpc_url(
            "http://engine-01/index.php/admin/remotecontrol",
            fetch=lambda url: seen.append(url) or b'{"plugin":"MjyRuntimePolicy"}',
        )
        self.assertEqual({"plugin": "MjyRuntimePolicy"}, probe(42))
        self.assertEqual(
            ["http://engine-01/index.php/plugins/direct?plugin=MjyRuntimePolicy&function=policyStatus&sid=42"], seen)

    def test_rpc_url_of_another_shape_gives_no_probe(self):
        self.assertIsNone(HttpPolicyProbe.from_rpc_url("http://engine-01/rpc"))

    def test_engine_url_form_used_by_the_cli(self):
        seen = []
        probe = HttpPolicyProbe.from_engine_url("http://localhost/", fetch=lambda url: seen.append(url) or b"{}")
        probe(7)
        self.assertTrue(seen[0].startswith("http://localhost/index.php/plugins/direct?"))

    def test_non_json_answer_means_plugin_not_listening(self):
        probe = HttpPolicyProbe.from_engine_url("http://localhost", fetch=lambda url: b"<html>login</html>")
        self.assertIsNone(probe(7))


if __name__ == "__main__":
    unittest.main()
