"""契约 v1.4：``POST /v1/participants/revoke``（platform/contracts/publish-gateway-v1.4.md）。

撤销的判据只有一条：**引擎 tokens_<sid> 里那一行没了**。所以每个用例都直接查假引擎的
参与者表，而不是只看应答状态码——"返回 200 但行还在"正是这个缺口的形状。

失败语义同样要钉死：问卷不存在、没有参与者表、删除没生效，一律 502。平台看到非 200
就不写 ``revoked_at``，于是"平台以为撤销了、令牌照样能进"这件事不会发生。
"""

import json
import shutil
import tempfile
import unittest
import uuid

from pubgw.rpc import ALLOWED_METHODS

from .fixtures import sample_payload
from .gateway_support import (
    ENGINE_PASSWORD,
    INSTANCE,
    encode,
    make_service,
    new_engine,
    signed_headers,
)

TOKEN_A = "tok1"
TOKEN_B = "tok2"


def revoke_payload(sid, token=TOKEN_A, instance=INSTANCE):
    return {
        "requestId": str(uuid.uuid4()),
        "engineInstanceId": instance,
        "surveyId": sid,
        "participantToken": token,
    }


class RevokeTestCase(unittest.TestCase):
    def setUp(self):
        self.state_dir = tempfile.mkdtemp()
        self.engine = new_engine()
        self.service = make_service(self.engine, self.state_dir)
        self.bodies = []
        self.sid = self._publish_with_participants()

    def tearDown(self):
        shutil.rmtree(self.state_dir)
        for body in self.bodies:
            self.assertNotIn(ENGINE_PASSWORD, body.decode("utf-8"))

    def call(self, operation, payload=None, body=None, headers=None):
        body = encode(payload) if body is None else body
        response = getattr(self.service, operation)(signed_headers(body) if headers is None else headers, body)
        self.bodies.append(response.body)
        return response.status, json.loads(response.body.decode("utf-8"))

    def _publish_with_participants(self):
        definition = sample_payload()
        definition["participants"] = [{"ref": "contact-a"}, {"ref": "contact-b"}]
        status, body = self.call("publish", {
            "requestId": str(uuid.uuid4()), "engineInstanceId": INSTANCE, "definition": definition,
        })
        self.assertEqual(200, status, body)
        return body["result"]["binding"]["surveyId"]

    def tokens_in_engine(self):
        return sorted(row["token"] for row in self.engine.token_rows.get(self.sid, []))

    # -------------------------------------------------------------- 撤销

    def test_the_participant_row_is_gone_from_the_engine(self):
        status, body = self.call("revoke_participant", revoke_payload(self.sid, TOKEN_A))

        self.assertEqual(200, status, body)
        self.assertEqual("revoked", body["status"])
        self.assertIs(False, body["result"]["alreadyAbsent"])
        self.assertEqual([TOKEN_B], self.tokens_in_engine())

    def test_only_the_named_token_is_removed(self):
        self.call("revoke_participant", revoke_payload(self.sid, TOKEN_A))

        self.assertEqual([TOKEN_B], self.tokens_in_engine())

    def test_revoking_twice_is_idempotent(self):
        self.call("revoke_participant", revoke_payload(self.sid, TOKEN_A))

        status, body = self.call("revoke_participant", revoke_payload(self.sid, TOKEN_A))

        self.assertEqual(200, status, body)
        self.assertIs(True, body["result"]["alreadyAbsent"], "引擎里已经没有这个码，结论同样是已撤销")
        self.assertEqual([TOKEN_B], self.tokens_in_engine())

    def test_a_token_the_engine_never_had_is_already_absent(self):
        status, body = self.call("revoke_participant", revoke_payload(self.sid, "never-issued"))

        self.assertEqual(200, status, body)
        self.assertIs(True, body["result"]["alreadyAbsent"])
        self.assertEqual([TOKEN_A, TOKEN_B], self.tokens_in_engine())

    def test_the_result_names_the_survey_and_the_deleted_row(self):
        _, body = self.call("revoke_participant", revoke_payload(self.sid, TOKEN_A))

        self.assertEqual(self.sid, body["result"]["surveyId"])
        self.assertIsInstance(body["result"]["tokenId"], int)

    # -------------------------------------------------------------- 失败

    def test_a_deletion_that_does_not_take_is_a_failure(self):
        self.engine.refuse_participant_delete = True

        status, body = self.call("revoke_participant", revoke_payload(self.sid, TOKEN_A))

        self.assertEqual(502, status, body)
        self.assertEqual("E_REVOKE_NOT_APPLIED", body["error"])
        self.assertEqual([TOKEN_A, TOKEN_B], self.tokens_in_engine())

    def test_an_unknown_survey_is_a_failure_not_an_absence(self):
        """实例 id 配错时 sid 当然查不到——那时真正的令牌还在另一台引擎上活着。"""
        status, body = self.call("revoke_participant", revoke_payload(987654, TOKEN_A))

        self.assertEqual(502, status, body)
        self.assertEqual("E_SURVEY_MISSING", body["error"])

    def test_a_survey_without_a_participant_table_is_a_failure(self):
        engine = new_engine()
        service = make_service(engine, tempfile.mkdtemp())
        body = encode({"requestId": str(uuid.uuid4()), "engineInstanceId": INSTANCE,
                       "definition": sample_payload()})
        published = json.loads(service.publish(signed_headers(body), body).body.decode("utf-8"))
        sid = published["result"]["binding"]["surveyId"]

        payload = encode(revoke_payload(sid, TOKEN_A))
        response = service.revoke_participant(signed_headers(payload), payload)
        answer = json.loads(response.body.decode("utf-8"))

        self.assertEqual(502, response.status, answer)
        self.assertEqual("E_NO_PARTICIPANT_TABLE", answer["error"])

    def test_an_unknown_instance_is_404(self):
        status, body = self.call("revoke_participant", revoke_payload(self.sid, TOKEN_A, "nowhere-01"))

        self.assertEqual(404, status, body)
        self.assertEqual("unknown_engine_instance", body["error"])

    # -------------------------------------------------------------- 请求校验

    def test_an_unsigned_request_never_touches_the_engine(self):
        payload = encode(revoke_payload(self.sid, TOKEN_A))
        before = len(self.engine.calls)

        response = self.service.revoke_participant({"Content-Type": "application/json"}, payload)

        self.assertEqual(401, response.status)
        self.assertEqual(before, len(self.engine.calls))
        self.assertEqual([TOKEN_A, TOKEN_B], self.tokens_in_engine())

    def test_malformed_bodies_are_400(self):
        good = revoke_payload(self.sid, TOKEN_A)
        broken = [
            {key: value for key, value in good.items() if key != "participantToken"},
            dict(good, extra="no"),
            dict(good, requestId="not-a-uuid"),
            dict(good, surveyId=0),
            dict(good, surveyId=True),
            dict(good, participantToken="abc"),
            dict(good, participantToken="has space"),
            dict(good, participantToken=123),
        ]
        for payload in broken:
            with self.subTest(payload=sorted(payload)):
                self.assertEqual((400, {"error": "invalid_request"}),
                                 self.call("revoke_participant", payload))

    def test_the_rejection_never_echoes_the_token(self):
        payload = dict(revoke_payload(self.sid), participantToken="secret token with spaces")

        _, body = self.call("revoke_participant", payload)

        self.assertNotIn("secret", json.dumps(body))

    # -------------------------------------------------------------- 白名单

    def test_revocation_only_needs_two_allowlisted_methods(self):
        before = len(self.engine.calls)
        self.call("revoke_participant", revoke_payload(self.sid, TOKEN_A))

        used = {method for method, _ in self.engine.calls[before:]}
        self.assertLessEqual(used, ALLOWED_METHODS)
        self.assertIn("delete_participants", used)
        self.assertIn("get_participant_properties", used)


if __name__ == "__main__":
    unittest.main()
