"""附件取件：网关侧的客户端与端点（契约 response-read-v1「附件取件」、plugin-channel-v1）。

三件事是这条链路的全部要点，测试按它们分组：

1. **内存有界**：应答是字节流，客户端与端点都只逐块转手，从不把整份文件读进内存；
2. **永久与暂时的分界**：404／413 是永久结论（平台据此记缺失、不重试），
   其余一律失败关闭成 502（平台据此重试）——把它们混起来就会让一次网络抖动
   变成一份悄悄缺失的附件；
3. **不记值**：日志与异常里只有实例、sid、答卷号与存储名，从不带文件内容、
   不带密钥、不带含 sig 的完整 URL。
"""

import http.client
import json
import threading
import time
import unittest

from pubgw.attachments import (
    ATTACHMENT_PATH,
    AttachmentReadService,
    AttachmentStream,
    InvalidAttachmentRequest,
    parse_attachment_request,
)
from pubgw.auth import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign as sign_body
from pubgw.channel import (
    ATTACHMENT_FUNCTION,
    AttachmentClient,
    AttachmentTooLarge,
    ChannelError,
    InvalidChannelRequest,
    canonical_query,
    sign,
)
from pubgw.engines import EngineConfig
from pubgw.server import build_server

INSTANCE = "hd-engine-01"
CHANNEL_SECRET = "204745a72bcb796d18bf2df097af7efea324f61077971e41b50189196036ff31"
SHARED_SECRET = b"0123456789abcdef0123456789abcdef"
NOW = 1_800_000_000
INDEX_URL = "http://engine-01/index.php"
RPC_URL = "http://engine-01/index.php/admin/remotecontrol"
STORED_NAME = "fu_1a2b3c"
FIELD = "123456X7X8"
GENERATION = "gen-1"
CONTENT = b"\x89PNG\r\n\x1a\n" + b"pixels" * 100


def body(**overrides):
    payload = {
        "engineInstanceId": INSTANCE,
        "surveyId": 42,
        "generation": GENERATION,
        "responseId": 1001,
        "field": FIELD,
        "storedName": STORED_NAME,
        "maxBytes": 1024 * 1024,
    }
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


def signed_headers(raw):
    timestamp = str(NOW)
    return {
        "Content-Type": "application/json",
        TIMESTAMP_HEADER: timestamp,
        SIGNATURE_HEADER: sign_body(SHARED_SECRET, timestamp, raw),
    }


def engine(secret=CHANNEL_SECRET):
    return EngineConfig(
        instance_id=INSTANCE,
        rpc_url=RPC_URL,
        user="u",
        password="p",
        channel_secret=secret,
    )


class FakeOpener:
    """记录请求过的 URL，按配置回一段流、404、413 或传输错误。"""

    def __init__(self, content=CONTENT, status=200, chunk=16):
        self.urls = []
        self.content = content
        self.status = status
        self.chunk = chunk
        self.closed = False

    def __call__(self, url, timeout=None):
        self.urls.append(url)
        if self.status == 404:
            raise FakeHttpError(404)
        if self.status == 413:
            raise FakeHttpError(413)
        if self.status == 500:
            raise FakeHttpError(500)
        return FakeHttpResponse(self.content, self.chunk, self)


class FakeHttpError(Exception):
    def __init__(self, code):
        super().__init__("http {}".format(code))
        self.code = code


class FakeHttpResponse:
    def __init__(self, content, chunk, owner):
        self._content = content
        self._offset = 0
        self._chunk = chunk
        self._owner = owner

    def headers_get(self, name, default=None):
        return self.headers.get(name, default)

    @property
    def headers(self):
        return {"Content-Length": str(len(self._content))}

    def read(self, size=-1):
        # 像真 socket 一样只给一小块：调用方必须循环读，不能指望一次 read 拿全。
        if size is None or size < 0:
            raise AssertionError("the client must not read the whole attachment at once")
        piece = self._content[self._offset:self._offset + min(size, self._chunk)]
        self._offset += len(piece)
        return piece

    def close(self):
        self._owner.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()
        return False


def client(opener, secret=CHANNEL_SECRET):
    return AttachmentClient(INDEX_URL, INSTANCE, secret, opener=opener, now=lambda: NOW)


def drain(stream):
    return b"".join(stream.chunks())


class ClientRequestTest(unittest.TestCase):
    """请求的形状与签名：签名覆盖整个查询串，所以改投不到别人的附件。"""

    def test_signs_the_whole_query_and_names_the_attachment_function(self):
        opener = FakeOpener()

        drain(client(opener).open(42, GENERATION, 1001, FIELD, STORED_NAME, 1024 * 1024))

        url = opener.urls[0]
        self.assertIn("function=" + ATTACHMENT_FUNCTION, url)
        query = url.split("?", 1)[1]
        canonical, _, signature = query.rpartition("&sig=")
        self.assertEqual(sign(CHANNEL_SECRET, str(NOW), canonical), signature)
        self.assertEqual(canonical_query(dict(
            part.split("=", 1) for part in canonical.split("&")
        )), canonical)

    def test_the_original_file_name_never_appears_in_the_url(self):
        opener = FakeOpener()

        drain(client(opener).open(42, GENERATION, 1001, FIELD, STORED_NAME, 1024))

        # 只有引擎生成的存储名进 URL；作答者起的名字根本不传给网关。
        self.assertIn(STORED_NAME, opener.urls[0])
        self.assertNotIn("%E7%94%B2", opener.urls[0])

    def test_rejects_a_stored_name_outside_the_grammar_before_sending(self):
        opener = FakeOpener()
        for bad in ["../etc/passwd", "fu_1/..", "", "a" * 256, "fu 1"]:
            with self.subTest(bad=bad):
                with self.assertRaises(InvalidChannelRequest):
                    client(opener).open(42, GENERATION, 1001, FIELD, bad, 1024)
        self.assertEqual([], opener.urls)

    def test_rejects_a_field_name_outside_the_grammar_before_sending(self):
        opener = FakeOpener()
        with self.assertRaises(InvalidChannelRequest):
            client(opener).open(42, GENERATION, 1001, "no spaces", STORED_NAME, 1024)
        self.assertEqual([], opener.urls)


class ClientStreamingTest(unittest.TestCase):
    """内存有界：一块一块转手，绝不 read() 整份。"""

    def test_streams_the_content_in_chunks(self):
        opener = FakeOpener(chunk=16)

        stream = client(opener).open(42, GENERATION, 1001, FIELD, STORED_NAME, 1024 * 1024)
        chunks = list(stream.chunks())

        self.assertEqual(CONTENT, b"".join(chunks))
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 16 for chunk in chunks))
        self.assertEqual(len(CONTENT), stream.length)

    def test_a_missing_attachment_is_a_normal_absence_not_an_error(self):
        self.assertIsNone(client(FakeOpener(status=404)).open(42, GENERATION, 1001, FIELD, STORED_NAME, 1024))

    def test_an_oversized_attachment_is_its_own_permanent_outcome(self):
        with self.assertRaises(AttachmentTooLarge):
            client(FakeOpener(status=413)).open(42, GENERATION, 1001, FIELD, STORED_NAME, 1024)

    def test_any_other_engine_failure_closes_the_call(self):
        with self.assertRaises(ChannelError):
            client(FakeOpener(status=500)).open(42, GENERATION, 1001, FIELD, STORED_NAME, 1024)

    def test_a_stream_longer_than_agreed_is_untrusted(self):
        # 插件本该先挡住超限的那一份。数到超出说明它没按契约办事——不可信，不是"太大"。
        opener = FakeOpener(content=b"x" * 100)
        stream = client(opener).open(42, GENERATION, 1001, FIELD, STORED_NAME, 10)
        with self.assertRaises(ChannelError):
            drain(stream)

    def test_a_missing_content_length_is_untrusted(self):
        opener = FakeOpener()
        original = FakeHttpResponse.headers
        try:
            FakeHttpResponse.headers = property(lambda self: {})
            with self.assertRaises(ChannelError):
                client(opener).open(42, GENERATION, 1001, FIELD, STORED_NAME, 1024)
        finally:
            FakeHttpResponse.headers = original


class RequestParsingTest(unittest.TestCase):

    def test_accepts_the_contract_body(self):
        request = parse_attachment_request(body())

        self.assertEqual(INSTANCE, request.engine_instance_id)
        self.assertEqual(42, request.survey_id)
        self.assertEqual(1001, request.response_id)
        self.assertEqual(STORED_NAME, request.stored_name)

    def test_rejects_bodies_outside_the_contract(self):
        for bad in [
            body(surveyId=0),
            body(responseId=-1),
            body(generation="bad generation"),
            body(maxBytes=0),
            body(storedName=".."),
            body(field="token"),
            body(field="ID"),
            body(field="submitdate"),
            json.dumps({"engineInstanceId": INSTANCE}).encode("utf-8"),
            b"not json",
        ]:
            with self.subTest(bad=bad):
                with self.assertRaises(InvalidAttachmentRequest):
                    parse_attachment_request(bad)


class EndpointTest(unittest.TestCase):

    def service(self, opener, engines=None, secret=CHANNEL_SECRET):
        return AttachmentReadService(
            engines=engines if engines is not None else {INSTANCE: engine(secret)},
            secret=SHARED_SECRET,
            now=lambda: NOW,
            channel_factory=lambda config: client(opener, secret) if config.channel_secret else None,
        )

    def call(self, opener, raw=None, headers=None, **kwargs):
        raw = body() if raw is None else raw
        return self.service(opener, **kwargs).fetch(
            headers if headers is not None else signed_headers(raw), raw)

    def test_streams_the_attachment_back(self):
        response = self.call(FakeOpener())

        self.assertIsInstance(response, AttachmentStream)
        self.assertEqual(200, response.status)
        self.assertEqual(CONTENT, drain(response))
        self.assertEqual("application/octet-stream", response.content_type)

    def test_rejects_an_unsigned_call(self):
        response = self.call(FakeOpener(), headers={"Content-Type": "application/json"})

        self.assertEqual(401, response.status)

    def test_an_unknown_engine_instance_is_a_404_on_the_instance_not_on_the_file(self):
        response = self.call(FakeOpener(), engines={})

        self.assertEqual(404, response.status)
        self.assertEqual({"error": "unknown_engine_instance"}, json.loads(response.body))

    def test_a_missing_attachment_answers_404_not_found(self):
        response = self.call(FakeOpener(status=404))

        self.assertEqual(404, response.status)
        self.assertEqual({"error": "not_found"}, json.loads(response.body))

    def test_an_oversized_attachment_answers_413(self):
        response = self.call(FakeOpener(status=413))

        self.assertEqual(413, response.status)
        self.assertEqual({"error": "too_large"}, json.loads(response.body))

    def test_any_other_engine_failure_answers_502(self):
        response = self.call(FakeOpener(status=500))

        self.assertEqual(502, response.status)
        self.assertEqual({"error": "engine_error"}, json.loads(response.body))

    def test_an_engine_without_a_channel_secret_fails_closed(self):
        # 配置漏了通道密钥就回 404，等于把一次配置错误变成一份静默缺失的附件。
        response = self.call(FakeOpener(), secret="")

        self.assertEqual(502, response.status)
        self.assertEqual({"error": "engine_error"}, json.loads(response.body))

    def test_the_path_is_the_one_the_contract_names(self):
        self.assertEqual("/v1/responses/attachment", ATTACHMENT_PATH)


class ServerShellTest(unittest.TestCase):
    """HTTP 外壳：真起一个服务器、真走套接字，确认字节流原样出去且头是对的。"""

    def setUp(self):
        opener = FakeOpener()
        service = AttachmentReadService(
            engines={INSTANCE: engine()},
            secret=SHARED_SECRET,
            now=time.time,
            channel_factory=lambda config: AttachmentClient(
                INDEX_URL, INSTANCE, CHANNEL_SECRET, opener=opener, now=time.time),
        )
        self.httpd = build_server(_NoPublishService(), "127.0.0.1", 0, attachments=service)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def request(self, method, path, raw=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            connection.request(method, path, body=raw, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_streams_the_bytes_with_the_right_headers(self):
        raw = body()
        timestamp = str(int(time.time()))
        headers = {
            "Content-Type": "application/json",
            TIMESTAMP_HEADER: timestamp,
            SIGNATURE_HEADER: sign_body(SHARED_SECRET, timestamp, raw),
        }

        status, got, content = self.request("POST", ATTACHMENT_PATH, raw, headers)

        self.assertEqual(200, status)
        self.assertEqual(CONTENT, content)
        self.assertEqual("application/octet-stream", got["Content-Type"])
        self.assertEqual(str(len(CONTENT)), got["Content-Length"])
        self.assertEqual("no-store", got["Cache-Control"])
        self.assertEqual("nosniff", got["X-Content-Type-Options"])

    def test_get_is_not_allowed_on_the_attachment_path(self):
        status, headers, _ = self.request("GET", ATTACHMENT_PATH)

        self.assertEqual(405, status)
        self.assertEqual("POST", headers["Allow"])


class _NoPublishService:
    """只为把服务器立起来；本组测试不碰发布路径。"""


if __name__ == "__main__":
    unittest.main()
