"""扩展表作答进入 ``POST /v1/responses/read``（ADR 0018 决定 9，契约 plugin-channel-v1）。

副表作答是行×列且行数逐份答卷不同，压不进 ``answers`` 的扁平字段模型，
所以单列一段 ``extensionAnswers``。``responses`` 一段的形状与语义完全不变。

两个来源都失败关闭：通道读不到就整页报错，绝不返回「看着完整、其实缺了扩展题」的一页。
"""

import json
import unittest

from pubgw.channel import ChannelError
from pubgw.engines import EngineConfig
from pubgw.responses import ResponseReadService
from tests.gateway_support import ENGINE_PASSWORD, INSTANCE, NOW, SECRET, signed_headers
from tests.test_respondent import PropertiesEngine
from tests.test_responses import FakeExportEngine

CHANNEL_SECRET = "204745a72bcb796d18bf2df097af7efea324f61077971e41b50189196036ff31"
GENERATION = "gen-1"
SURVEY_ID = 42


def config(channel_secret=CHANNEL_SECRET):
    return EngineConfig(
        instance_id=INSTANCE,
        rpc_url="http://engine.invalid/index.php/admin/remotecontrol",
        user="admin",
        password=ENGINE_PASSWORD,
        channel_secret=channel_secret,
    )


class FakeChannel:
    """替身通道：记录调用并回一段固定的扩展表作答。"""

    def __init__(self, answers=None, error=None):
        self._answers = answers if answers is not None else {}
        self._error = error
        self.calls = []

    def read(self, survey_id, generation, response_ids, question_codes):
        self.calls.append((survey_id, generation, tuple(response_ids), tuple(question_codes)))
        if self._error is not None:
            raise self._error
        return self._answers


def body(**overrides):
    payload = {
        "engineInstanceId": INSTANCE,
        "surveyId": SURVEY_ID,
        "responseIds": [1, 2],
        "fields": ["Q1"],
    }
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


class ExtensionReadTestCase(unittest.TestCase):

    def service(self, engine=None, channel=None, engine_config=None):
        # FakeExportEngine 的 rows 是「答卷号 → {列名: 值}」。
        engine = engine or FakeExportEngine(rows={1: {"Q1": "a"}, 2: {"Q1": "b"}}, codes={"Q1": "Q1"})
        self.channel = channel if channel is not None else FakeChannel()
        return ResponseReadService(
            engines={INSTANCE: engine_config or config()},
            secret=SECRET,
            transport_factory=lambda cfg: engine.transport,
            now=lambda: NOW,
            channel_factory=lambda cfg: self.channel,
        )

    def read(self, service, request_body):
        response = service.read(signed_headers(request_body), request_body)
        text = response.body.decode("utf-8")
        # 每次读取都核一遍：引擎口令不得出现在任何应答里。
        self.assertNotIn(ENGINE_PASSWORD, text)
        self.assertNotIn(CHANNEL_SECRET, text)
        return response.status, json.loads(text)


class ExtensionAnswersTest(ExtensionReadTestCase):

    def test_extension_answers_are_returned_in_their_own_section(self):
        channel = FakeChannel({1: {"TABLE1": _answer([{"item": "甲"}])}})
        service = self.service(channel=channel)

        status, payload = self.read(service, body(generation=GENERATION, extensionQuestions=["TABLE1"]))

        self.assertEqual(200, status)
        self.assertEqual(
            {"1": {"TABLE1": {"structureVersion": "v3", "isValid": True, "rows": [{"item": "甲"}]}}},
            payload["extensionAnswers"])

    def test_the_flat_answers_section_is_unchanged(self):
        channel = FakeChannel({1: {"TABLE1": _answer([{"item": "甲"}])}})
        service = self.service(channel=channel)

        _, payload = self.read(service, body(generation=GENERATION, extensionQuestions=["TABLE1"]))

        self.assertEqual([{"id": 1, "values": {"Q1": "a"}}, {"id": 2, "values": {"Q1": "b"}}],
                         payload["responses"])

    def test_the_channel_is_asked_for_the_same_page(self):
        service = self.service()

        self.read(service, body(generation=GENERATION, extensionQuestions=["TABLE1", "TABLE2"]))

        self.assertEqual([(SURVEY_ID, GENERATION, (1, 2), ("TABLE1", "TABLE2"))], self.channel.calls)

    def test_an_empty_channel_result_still_yields_a_section(self):
        _, payload = self.read(self.service(), body(generation=GENERATION, extensionQuestions=["TABLE1"]))

        self.assertEqual({}, payload["extensionAnswers"])

    def test_the_channel_is_not_used_when_no_extension_questions_are_asked_for(self):
        service = self.service()

        status, payload = self.read(service, body())

        self.assertEqual(200, status)
        self.assertEqual([], self.channel.calls)
        self.assertNotIn("extensionAnswers", payload)

    def test_the_channel_is_not_used_without_a_generation(self):
        # 代次是副表自然键的一段；没有它就无法保证不串代次，因此宁可不读。
        service = self.service()

        status, payload = self.read(service, body(extensionQuestions=["TABLE1"]))

        self.assertEqual(400, status)
        self.assertEqual([], self.channel.calls)


class CrossLaneTest(ExtensionReadTestCase):
    """两条车道在同一个端点上的真实交叉：邀请码回读（ADR 0016 缺口 b）与扩展表作答（ADR 0018）。

    两组可选字段互相正交，同时出现时各自照常给出，谁也不吞掉谁。
    """

    def engine_with_token(self):
        # 复用邀请码车道自己的假引擎：它会应答 get_survey_properties 的匿名判定。
        return PropertiesEngine(
            rows={1: {"Q1": "a", "token": "tok-1"}, 2: {"Q1": "b", "token": "tok-2"}},
            codes={"Q1": "Q1", "token": "token"},
            anonymized="N")

    def test_tokens_and_extension_answers_both_come_back(self):
        channel = FakeChannel({1: {"TABLE1": _answer([{"item": "甲"}])}})
        service = self.service(engine=self.engine_with_token(), channel=channel)

        status, payload = self.read(service, body(
            includeRespondent=True, generation=GENERATION, extensionQuestions=["TABLE1"]))

        self.assertEqual(200, status)
        self.assertEqual("tok-1", payload["responses"][0]["token"])
        self.assertEqual([{"item": "甲"}], payload["extensionAnswers"]["1"]["TABLE1"]["rows"])

    def test_the_channel_is_still_asked_for_the_same_page(self):
        service = self.service(engine=self.engine_with_token())

        self.read(service, body(includeRespondent=True, generation=GENERATION,
                                extensionQuestions=["TABLE1"]))

        self.assertEqual([(SURVEY_ID, GENERATION, (1, 2), ("TABLE1",))], self.channel.calls)

    def test_extension_answers_alone_still_omit_the_token_key(self):
        # 没要邀请码就一个 token 键都不该多出来（该车道的既有约定）。
        service = self.service(engine=self.engine_with_token())

        _, payload = self.read(service, body(generation=GENERATION, extensionQuestions=["TABLE1"]))

        self.assertNotIn("token", payload["responses"][0])

    def test_a_channel_failure_fails_the_page_even_when_tokens_were_asked_for(self):
        # 失败关闭优先于「至少把令牌给出去」：半份应答比没有应答更危险。
        service = self.service(engine=self.engine_with_token(),
                               channel=FakeChannel(error=ChannelError("unreachable")))

        status, payload = self.read(service, body(
            includeRespondent=True, generation=GENERATION, extensionQuestions=["TABLE1"]))

        self.assertEqual(502, status)
        self.assertNotIn("responses", payload)

    def test_the_token_column_is_still_refused_as_a_plain_field(self):
        # 另一条车道修掉过的匿名判定绕过：token 只能经 includeRespondent 拿。
        # 加了扩展表字段也不能把这条放松掉。
        service = self.service(engine=self.engine_with_token())
        request_body = body(fields=["Q1", "token"], generation=GENERATION,
                            extensionQuestions=["TABLE1"])

        status, payload = self.read(service, request_body)

        self.assertEqual(400, status)
        self.assertEqual({"error": "invalid_request"}, payload)

    def test_fields_may_be_empty_when_only_extension_answers_are_wanted(self):
        channel = FakeChannel({1: {"TABLE1": _answer([{"item": "甲"}])}})
        service = self.service(channel=channel)

        status, payload = self.read(service, body(
            fields=[], generation=GENERATION, extensionQuestions=["TABLE1"]))

        self.assertEqual(200, status)
        self.assertEqual([{"item": "甲"}], payload["extensionAnswers"]["1"]["TABLE1"]["rows"])


class FailClosedTest(ExtensionReadTestCase):
    """通道读不到就整页失败，绝不返回缺了扩展题的一页（ADR 0013 决定 8 的同一条规矩）。"""

    def test_a_channel_failure_fails_the_whole_page(self):
        service = self.service(channel=FakeChannel(error=ChannelError("unreachable")))

        status, payload = self.read(service, body(generation=GENERATION, extensionQuestions=["TABLE1"]))

        self.assertEqual(502, status)
        self.assertEqual({"error": "engine_error"}, payload)

    def test_a_channel_failure_never_returns_partial_answers(self):
        service = self.service(channel=FakeChannel(error=ChannelError("unreachable")))

        _, payload = self.read(service, body(generation=GENERATION, extensionQuestions=["TABLE1"]))

        self.assertNotIn("responses", payload)
        self.assertNotIn("extensionAnswers", payload)

    def test_an_engine_without_a_channel_secret_fails_closed(self):
        # 配置漏了通道密钥，就绝不能安静地少返回扩展题。
        # 这里刻意用**真的**通道工厂：没配密钥时它返回 None，而且不会发出任何请求。
        engine = FakeExportEngine(rows={1: {"Q1": "a"}}, codes={"Q1": "Q1"})
        service = ResponseReadService(
            engines={INSTANCE: config(channel_secret="")},
            secret=SECRET,
            transport_factory=lambda cfg: engine.transport,
            now=lambda: NOW,
        )
        request_body = body(generation=GENERATION, extensionQuestions=["TABLE1"])

        response = service.read(signed_headers(request_body), request_body)

        self.assertEqual(502, response.status)
        self.assertEqual({"error": "engine_error"}, json.loads(response.body.decode("utf-8")))


class RequestValidationTest(ExtensionReadTestCase):

    def reject(self, **overrides):
        status, payload = self.read(self.service(), body(generation=GENERATION, **overrides))
        self.assertEqual(400, status)
        self.assertEqual({"error": "invalid_request"}, payload)

    def test_rejects_too_many_extension_questions(self):
        self.reject(extensionQuestions=["Q%d" % index for index in range(51)])

    def test_rejects_a_question_code_outside_the_grammar(self):
        self.reject(extensionQuestions=["TAB LE"])

    def test_rejects_duplicate_question_codes(self):
        self.reject(extensionQuestions=["TABLE1", "TABLE1"])

    def test_rejects_a_generation_outside_the_grammar(self):
        status, _ = self.read(self.service(), body(generation="gen 1", extensionQuestions=["TABLE1"]))
        self.assertEqual(400, status)

    def test_rejects_an_unknown_field(self):
        status, _ = self.read(self.service(), body(somethingElse=1))
        self.assertEqual(400, status)


def _answer(rows):
    from pubgw.channel import ExtensionAnswer

    return ExtensionAnswer("v3", True, tuple(rows))


if __name__ == "__main__":
    unittest.main()
