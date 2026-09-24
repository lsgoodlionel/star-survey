"""``POST /v1/responses/uploads``：把作答者上传从插件拉到平台（ADR 0019 决定 8）。

要钉住的三件事：

* 清单与字节都只来自**插件的上传会话表**——网关自己不认识答卷字段里那张文件清单；
* 插件的应答是**外部数据**，形状不对就拒绝，绝不猜（与 channel.py 同一条自律）；
* 失败一律关闭：通道不可达、密钥没配、应答形状不对，都不能变成"这份答卷没有上传"。
"""

import base64
import json
import unittest

from pubgw import channel, uploads
from pubgw.auth import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign
from pubgw.engines import EngineConfig

SECRET = b"0123456789abcdef0123456789abcdef"
INSTANCE = "hd-engine-01"
INSTANCE_SECRET = "0123456789abcdef0123456789abcdef-instance"
GENERATION = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
TOKEN = "11111111-2222-4333-8444-555555555555"
NOW = 1800000000.0


def engine(channel_secret: str = "") -> EngineConfig:
    return EngineConfig(
        instance_id=INSTANCE,
        rpc_url="https://engine.example/index.php/admin/remotecontrol",
        user="u",
        password="p",
        channel_secret=channel_secret or channel.derive_channel_secret(INSTANCE_SECRET, INSTANCE),
    )


def manifest_body(uploads_payload):
    return json.dumps({
        "plugin": "MjyQuestionExtensions",
        "engineInstanceId": INSTANCE,
        "surveyId": 4242,
        "generation": GENERATION,
        "uploads": uploads_payload,
    }).encode("utf-8")


def content_body(raw: bytes, token: str = TOKEN):
    return json.dumps({
        "plugin": "MjyQuestionExtensions",
        "engineInstanceId": INSTANCE,
        "surveyId": 4242,
        "generation": GENERATION,
        "uploadToken": token,
        "questionCode": "REC1",
        "responseId": 7,
        "originalName": "录音.webm",
        "extension": "webm",
        "sizeBytes": len(raw),
        "contentBase64": base64.b64encode(raw).decode("ascii"),
    }).encode("utf-8")


class UploadSessionClientTest(unittest.TestCase):
    """通道客户端：签名覆盖整个查询串，应答严格按契约解析。"""

    def client(self, body: bytes, recorder=None):
        def fetch(url):
            if recorder is not None:
                recorder.append(url)
            return body

        return uploads.UploadSessionClient(
            "https://engine.example/index.php", INSTANCE,
            channel.derive_channel_secret(INSTANCE_SECRET, INSTANCE),
            fetch=fetch, now=lambda: NOW)

    def test_lists_the_uploads_of_a_page_of_responses(self):
        body = manifest_body({"7": [{
            "uploadToken": TOKEN, "questionCode": "REC1",
            "originalName": "录音.webm", "extension": "webm", "sizeBytes": 12,
        }]})

        found = self.client(body).list(4242, GENERATION, [7, 9])

        self.assertEqual(list(found), [7])
        self.assertEqual(found[7][0].upload_token, TOKEN)
        self.assertEqual(found[7][0].question_code, "REC1")
        self.assertEqual(found[7][0].size_bytes, 12)

    def test_the_request_is_signed_and_names_the_plugin_function(self):
        urls = []
        self.client(manifest_body({}), urls).list(4242, GENERATION, [7])

        self.assertEqual(len(urls), 1)
        self.assertIn("function=uploadSessions", urls[0])
        self.assertIn("plugin=MjyQuestionExtensions", urls[0])
        self.assertIn("sig=", urls[0])

    def test_an_upload_for_a_response_that_was_not_asked_for_is_refused(self):
        body = manifest_body({"99": [{
            "uploadToken": TOKEN, "questionCode": "REC1",
            "originalName": "a", "extension": "webm", "sizeBytes": 1,
        }]})

        with self.assertRaises(channel.ChannelError):
            self.client(body).list(4242, GENERATION, [7])

    def test_a_refusal_body_is_never_read_as_no_uploads(self):
        """401 的体绝不能被当成「没有上传」——那会让一次密钥配错变成静默的数据缺失。"""
        with self.assertRaises(channel.ChannelError):
            self.client(b'{"error":"unauthorized"}').list(4242, GENERATION, [7])

    def test_a_body_that_is_not_json_is_refused(self):
        with self.assertRaises(channel.ChannelError):
            self.client(b"<html>oops</html>").list(4242, GENERATION, [7])

    def test_fetches_the_bytes_of_one_upload(self):
        raw = b"\x1a\x45\xdf\xa3recording"

        found = self.client(content_body(raw)).content(4242, GENERATION, TOKEN)

        self.assertEqual(found.content, raw)
        self.assertEqual(found.upload_token, TOKEN)
        self.assertEqual(found.question_code, "REC1")
        self.assertEqual(found.response_id, 7)

    def test_a_content_reply_about_another_token_is_refused(self):
        with self.assertRaises(channel.ChannelError):
            self.client(content_body(b"x", token="99999999-2222-4333-8444-555555555555")).content(
                4242, GENERATION, TOKEN)

    def test_a_content_reply_whose_size_disagrees_with_its_bytes_is_refused(self):
        payload = json.loads(content_body(b"abc"))
        payload["sizeBytes"] = 99
        with self.assertRaises(channel.ChannelError):
            self.client(json.dumps(payload).encode("utf-8")).content(4242, GENERATION, TOKEN)

    def test_a_content_reply_that_is_not_base64_is_refused(self):
        payload = json.loads(content_body(b"abc"))
        payload["contentBase64"] = "not base64!!!"
        with self.assertRaises(channel.ChannelError):
            self.client(json.dumps(payload).encode("utf-8")).content(4242, GENERATION, TOKEN)

    def test_an_oversized_upload_is_refused_before_it_is_decoded(self):
        payload = json.loads(content_body(b"abc"))
        payload["sizeBytes"] = uploads.MAX_CONTENT_BYTES + 1
        with self.assertRaises(channel.ChannelError):
            self.client(json.dumps(payload).encode("utf-8")).content(4242, GENERATION, TOKEN)

    def test_the_default_client_can_be_constructed_without_injecting_fetch(self):
        """生产路径不注入 ``fetch``，所以默认分支必须真的存在。

        每条用例都注入假 ``fetch``，默认分支因此一次都没被执行过——
        本类里 ``_urlopen`` 曾经根本没定义，整个拉取功能在生产上一调即 ``NameError``，
        而测试全绿（独立安全审查抓到的 HIGH）。这一条专门守住那个分支。
        """
        client = uploads.UploadSessionClient.from_rpc_url(
            "https://engine.example/index.php/admin/remotecontrol", INSTANCE, "x" * 32)

        self.assertIsNotNone(client)
        self.assertTrue(callable(client._fetch))

    def test_a_malformed_token_is_refused_before_the_request_goes_out(self):
        urls = []
        with self.assertRaises(channel.InvalidChannelRequest):
            self.client(manifest_body({}), urls).content(4242, GENERATION, "not-a-uuid")
        self.assertEqual(urls, [])


class UploadReadServiceTest(unittest.TestCase):
    """HTTP 端点：与 /v1/responses/read 同一套平台↔网关鉴权。"""

    def service(self, channel_client=None, engines=None):
        return uploads.UploadReadService(
            engines=engines if engines is not None else {INSTANCE: engine()},
            secret=SECRET,
            now=lambda: NOW,
            channel_factory=lambda config: channel_client,
        )

    def call(self, payload, service=None, secret=SECRET):
        body = json.dumps(payload).encode("utf-8")
        timestamp = str(int(NOW))
        headers = {
            "Content-Type": "application/json",
            TIMESTAMP_HEADER: timestamp,
            SIGNATURE_HEADER: sign(secret, timestamp, body),
        }
        return (service or self.service()).read(headers, body)

    def test_an_unsigned_request_is_refused(self):
        body = json.dumps({"engineInstanceId": INSTANCE, "surveyId": 4242,
                           "generation": GENERATION, "responseIds": [7]}).encode("utf-8")

        response = self.service().read({"Content-Type": "application/json"}, body)

        self.assertEqual(response.status, 401)

    def test_a_request_signed_with_the_wrong_secret_is_refused(self):
        response = self.call(
            {"engineInstanceId": INSTANCE, "surveyId": 4242,
             "generation": GENERATION, "responseIds": [7]},
            secret=b"f" * 32)

        self.assertEqual(response.status, 401)

    def test_an_unknown_engine_instance_is_a_not_found(self):
        response = self.call({"engineInstanceId": "nope", "surveyId": 4242,
                              "generation": GENERATION, "responseIds": [7]})

        self.assertEqual(response.status, 404)

    def test_a_request_without_a_generation_is_refused(self):
        """代次是上传会话自然键的一段；没有它无法保证不串代次，宁可拒绝也不读。"""
        response = self.call({"engineInstanceId": INSTANCE, "surveyId": 4242, "responseIds": [7]})

        self.assertEqual(response.status, 400)

    def test_returns_the_manifest_for_a_page_of_responses(self):
        client = FakeChannel(manifest={7: [uploads.UploadSession(TOKEN, "REC1", "录音.webm", "webm", 12)]})

        response = self.call({"engineInstanceId": INSTANCE, "surveyId": 4242,
                              "generation": GENERATION, "responseIds": [7]},
                             service=self.service(client))

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["uploads"]["7"][0]["uploadToken"], TOKEN)
        self.assertNotIn("contentBase64", response.body.decode("utf-8"))

    def test_returns_the_bytes_when_a_token_is_named(self):
        raw = b"\x1a\x45\xdf\xa3rec"
        client = FakeChannel(content=uploads.UploadContent(TOKEN, "REC1", 7, "录音.webm", "webm", raw))

        response = self.call({"engineInstanceId": INSTANCE, "surveyId": 4242,
                              "generation": GENERATION, "uploadToken": TOKEN},
                             service=self.service(client))

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertEqual(base64.b64decode(payload["contentBase64"]), raw)
        self.assertEqual(payload["uploadToken"], TOKEN)

    def test_an_engine_without_a_channel_secret_fails_closed(self):
        """配置漏了通道密钥就安静地少返回上传，等于一次静默的数据缺失。"""
        response = self.call({"engineInstanceId": INSTANCE, "surveyId": 4242,
                              "generation": GENERATION, "responseIds": [7]},
                             service=self.service(None))

        self.assertEqual(response.status, 502)

    def test_a_channel_failure_fails_closed(self):
        response = self.call({"engineInstanceId": INSTANCE, "surveyId": 4242,
                              "generation": GENERATION, "responseIds": [7]},
                             service=self.service(ExplodingChannel()))

        self.assertEqual(response.status, 502)

    def test_naming_both_a_token_and_response_ids_is_refused(self):
        """两种模式互斥：一次调用要么问清单，要么取一件字节。"""
        response = self.call({"engineInstanceId": INSTANCE, "surveyId": 4242, "generation": GENERATION,
                              "responseIds": [7], "uploadToken": TOKEN})

        self.assertEqual(response.status, 400)

    def test_naming_neither_is_refused(self):
        response = self.call({"engineInstanceId": INSTANCE, "surveyId": 4242, "generation": GENERATION})

        self.assertEqual(response.status, 400)

    def test_an_unknown_field_is_refused(self):
        response = self.call({"engineInstanceId": INSTANCE, "surveyId": 4242, "generation": GENERATION,
                              "responseIds": [7], "fileNames": ["a.webm"]})

        self.assertEqual(response.status, 400)


class FakeChannel:
    def __init__(self, manifest=None, content=None):
        self._manifest = manifest or {}
        self._content = content

    def list(self, survey_id, generation, response_ids):
        return self._manifest

    def content(self, survey_id, generation, upload_token):
        return self._content


class ExplodingChannel:
    def list(self, survey_id, generation, response_ids):
        raise channel.ChannelError("plugin unreachable")

    def content(self, survey_id, generation, upload_token):
        raise channel.ChannelError("plugin unreachable")


if __name__ == "__main__":
    unittest.main()
