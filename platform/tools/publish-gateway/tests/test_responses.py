"""POST /v1/responses/read：逐条对照 platform/contracts/response-read-v1.md。

假引擎按 LimeSurvey 7.1.2 ``export_responses``（JSON 格式）的真实形状作答：
表头是题目代码（重复的加上列名后缀）、记录按请求列的顺序输出、区间内没有答卷时输出一条全空记录、
没有任何答卷时返回 ``ERR_NO_DATA``。
"""

import base64
import http.client
import json
import threading
import time
import unittest

from pubgw.engines import EngineConfig
from pubgw.responses import MAX_ID_SPAN, MAX_RESPONSE_IDS, ResponseReadService
from pubgw.server import build_server

from .gateway_support import ENGINE_PASSWORD, INSTANCE, NOW, SECRET, signed_headers

SID = 511001
F1, F2, F3 = "511001X1X1", "511001X1X2", "511001X1X3other"


class FakeExportEngine:
    def __init__(self, rows=None, codes=None, error=None, drop_column=False):
        #: 答卷号 → {列名: 值}
        self.rows = rows if rows is not None else {}
        #: 列名 → 表头代码；缺省时每列用同一个代码，故意制造重复表头
        self.codes = codes or {}
        self.error = error
        self.drop_column = drop_column
        self.exports = []
        self.logged_out = False

    def transport(self, payload):
        request = json.loads(payload.decode("utf-8"))
        method, params = request["method"], request["params"]
        if method == "get_session_key":
            result = "sess" if params[1] == ENGINE_PASSWORD else {"status": "Invalid user name or password"}
        elif method == "release_session_key":
            self.logged_out = True
            result = "OK"
        elif method == "export_responses":
            result = self._export(*params)
        else:
            result = {"status": "Method not supported", "error_code": 1}
        return json.dumps({"id": 1, "result": result, "error": None}).encode("utf-8")

    def _export(self, key, sid, doc, lang, completion, heading, answers, lo, hi, fields, *rest):
        self.exports.append({"sid": sid, "doc": doc, "completion": completion, "heading": heading,
                             "answers": answers, "from": lo, "to": hi, "fields": list(fields)})
        if self.error is not None:
            return self.error
        if not self.rows:
            return {"status": "No Data, could not get max id.", "error_code": "ERR_NO_DATA"}
        headers = []
        for column in fields:
            code = "id" if column == "id" else self.codes.get(column, "QSAME")
            headers.append(code if code not in headers else "{} ({})".format(code, column))
        records = []
        for rid in sorted(self.rows):
            if lo <= rid <= hi:
                values = [rid if c == "id" else self.rows[rid].get(c) for c in fields]
                if self.drop_column:
                    values = values[:-1]
                records.append(dict(zip(headers, values)))
        if not records:
            records.append({h: "" for h in headers})
        document = json.dumps({"responses": records}, ensure_ascii=False)
        return base64.b64encode(document.encode("utf-8")).decode("ascii")


def config(instance=INSTANCE):
    return EngineConfig(instance_id=instance, rpc_url="http://engine.invalid/rc", user="admin",
                        password=ENGINE_PASSWORD)


def make_service(engine, clock=None):
    return ResponseReadService(
        engines={INSTANCE: config()},
        secret=SECRET,
        transport_factory=lambda cfg: engine.transport,
        now=clock or (lambda: NOW),
    )


def request_body(ids=(1, 2, 3), fields=(F1, F2), **overrides):
    payload = {"engineInstanceId": INSTANCE, "surveyId": SID, "responseIds": list(ids), "fields": list(fields)}
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


class ReadTestCase(unittest.TestCase):
    def read(self, engine, body=None, headers=None):
        body = request_body() if body is None else body
        response = make_service(engine).read(signed_headers(body) if headers is None else headers, body)
        text = response.body.decode("utf-8")
        self.assertNotIn(ENGINE_PASSWORD, text)
        return response.status, json.loads(text)


class SuccessfulReadTest(ReadTestCase):
    def test_values_are_keyed_by_fieldname_and_absent_responses_are_missing(self):
        engine = FakeExportEngine(rows={1: {F1: "A1", F2: "你好"}, 3: {F1: "A2", F2: None}})

        status, body = self.read(engine)

        self.assertEqual(200, status)
        self.assertEqual(
            [{"id": 1, "values": {F1: "A1", F2: "你好"}}, {"id": 3, "values": {F1: "A2", F2: None}}],
            body["responses"],
        )
        self.assertEqual([2], body["missing"])
        self.assertTrue(engine.logged_out)

    def test_the_export_asks_for_short_json_by_code_with_id_first(self):
        engine = FakeExportEngine(rows={1: {F1: "A1"}})

        self.read(engine)

        export = engine.exports[0]
        self.assertEqual((SID, "json", "all", "code", "short"),
                         (export["sid"], export["doc"], export["completion"], export["heading"], export["answers"]))
        self.assertEqual(["id", F1, F2], export["fields"])
        self.assertEqual((1, 3), (export["from"], export["to"]))

    def test_duplicate_heading_codes_do_not_confuse_the_positional_mapping(self):
        engine = FakeExportEngine(rows={1: {F1: "x", F2: "y", F3: "z"}})

        status, body = self.read(engine, request_body(ids=[1], fields=[F1, F2, F3]))

        self.assertEqual(200, status)
        self.assertEqual({F1: "x", F2: "y", F3: "z"}, body["responses"][0]["values"])

    def test_numbers_from_the_engine_become_text(self):
        engine = FakeExportEngine(rows={1: {F1: 5, F2: 2.5}})

        status, body = self.read(engine, request_body(ids=[1]))

        self.assertEqual({F1: "5", F2: "2.5"}, body["responses"][0]["values"])

    def test_sparse_ids_are_read_in_bounded_ranges(self):
        far = 1 + MAX_ID_SPAN * 10
        engine = FakeExportEngine(rows={1: {F1: "a"}, 2: {F1: "b"}, far: {F1: "c"}})

        status, body = self.read(engine, request_body(ids=[far, 2, 1]))

        self.assertEqual(200, status)
        self.assertEqual([1, 2, far], [entry["id"] for entry in body["responses"]])
        self.assertEqual([(1, 2), (far, far)], [(e["from"], e["to"]) for e in engine.exports])
        for export in engine.exports:
            self.assertLess(export["to"] - export["from"], MAX_ID_SPAN)

    def test_rows_outside_the_request_are_never_returned(self):
        engine = FakeExportEngine(rows={1: {F1: "mine"}, 2: {F1: "not requested"}, 3: {F1: "mine too"}})

        status, body = self.read(engine, request_body(ids=[1, 3]))

        self.assertEqual([1, 3], [entry["id"] for entry in body["responses"]])

    def test_an_empty_range_yields_missing_not_an_error(self):
        engine = FakeExportEngine(rows={100: {F1: "elsewhere"}})

        status, body = self.read(engine, request_body(ids=[1, 2]))

        self.assertEqual(200, status)
        self.assertEqual([], body["responses"])
        self.assertEqual([1, 2], body["missing"])

    def test_a_survey_without_any_responses_yields_missing(self):
        status, body = self.read(FakeExportEngine(rows={}))

        self.assertEqual(200, status)
        self.assertEqual([1, 2, 3], body["missing"])


class EngineFailureTest(ReadTestCase):
    def test_an_engine_error_is_502_without_details(self):
        engine = FakeExportEngine(error={"status": "No permission " + ENGINE_PASSWORD, "error_code": "ERR_NO_PERMISSION"})

        status, body = self.read(engine)

        self.assertEqual(502, status)
        self.assertEqual({"error": "engine_error"}, body)
        self.assertTrue(engine.logged_out)

    def test_a_column_count_mismatch_is_502(self):
        engine = FakeExportEngine(rows={1: {F1: "a", F2: "b"}}, drop_column=True)

        status, body = self.read(engine)

        self.assertEqual(502, status)

    def test_a_result_that_is_not_base64_json_is_502(self):
        status, _ = self.read(FakeExportEngine(error="%%% not base64 %%%"))

        self.assertEqual(502, status)

    def test_a_rejected_login_is_502(self):
        engine = FakeExportEngine(rows={1: {F1: "a"}})
        service = ResponseReadService(
            engines={INSTANCE: EngineConfig(INSTANCE, "http://engine.invalid/rc", "admin", "wrong-password")},
            secret=SECRET, transport_factory=lambda cfg: engine.transport, now=lambda: NOW)
        body = request_body()

        response = service.read(signed_headers(body), body)

        self.assertEqual(502, response.status)
        self.assertEqual([], engine.exports)


class RequestValidationTest(ReadTestCase):
    def assert_rejected(self, body, status=400):
        engine = FakeExportEngine(rows={1: {F1: "a"}})
        got, payload = self.read(engine, body)
        self.assertEqual(status, got, body)
        self.assertEqual([], engine.exports)
        return payload

    def test_bad_signatures_are_401(self):
        engine = FakeExportEngine(rows={1: {F1: "a"}})
        body = request_body()
        headers = signed_headers(body)
        headers["X-Pubgw-Signature"] = "0" * 64

        status, payload = self.read(engine, body, headers)

        self.assertEqual(401, status)
        self.assertEqual("bad_signature", payload["error"])
        self.assertEqual([], engine.exports)

    def test_unknown_instances_are_404(self):
        payload = self.assert_rejected(request_body(engineInstanceId="elsewhere"), 404)
        self.assertEqual("unknown_engine_instance", payload["error"])

    def test_malformed_bodies_are_400(self):
        too_many = list(range(1, MAX_RESPONSE_IDS + 2))
        for body in (
            b"not json",
            b"[]",
            request_body(extra=1),
            request_body(ids=[]),
            request_body(ids=[0]),
            request_body(ids=[-1]),
            request_body(ids=["1"]),
            request_body(ids=[True]),
            request_body(ids=[1, 1]),
            request_body(ids=too_many),
            request_body(fields=[]),
            request_body(fields=["bad field"]),
            request_body(fields=["id"]),
            request_body(fields=[F1, F1]),
            request_body(surveyId="511001"),
            request_body(surveyId=0),
        ):
            with self.subTest(body=body[:80]):
                self.assert_rejected(body)

    def test_the_content_type_must_be_json(self):
        engine = FakeExportEngine(rows={1: {F1: "a"}})
        body = request_body()
        headers = signed_headers(body)
        headers["Content-Type"] = "text/plain"

        status, _ = self.read(engine, body, headers)

        self.assertEqual(400, status)


class ServerRouteTest(unittest.TestCase):
    def setUp(self):
        self.engine = FakeExportEngine(rows={1: {F1: "a"}})
        responses = make_service(self.engine, clock=time.time)
        self.httpd = build_server(None, "127.0.0.1", 0, responses=responses)
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05},
                                       daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def request(self, method, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=15)
        try:
            connection.request(method, "/v1/responses/read", body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_the_read_endpoint_is_served_over_http(self):
        body = request_body(ids=[1])

        status, headers, raw = self.request("POST", body, signed_headers(body, timestamp=int(time.time())))

        self.assertEqual(200, status)
        self.assertEqual("no-store", headers["Cache-Control"])
        self.assertEqual({F1: "a", F2: None}, json.loads(raw)["responses"][0]["values"])

    def test_get_is_not_allowed(self):
        status, headers, _ = self.request("GET")

        self.assertEqual(405, status)
        self.assertEqual("POST", headers["Allow"])


if __name__ == "__main__":
    unittest.main()
