"""答卷读取带回参与者令牌（ADR 0016 缺口 (b)）。

为什么要它：引擎事件与答卷投影里只有答卷号，没有令牌，平台因此无法判断"张三答完了没有"
（见 RespondentIdentityResolver）。发布回执已经给出"哪个码发给了谁"（invitations），
这里补上"一份答卷属于哪个码"，两半合起来催答才能按人生效。

匿名问卷永不给令牌。引擎只在非匿名时给答卷表建 token 列（SurveyActivator.php:253），
但问卷可以先以非匿名激活、把列和值都写好，之后再把 anonymized 改成 Y——列和数据都还在。
所以按问卷当前的 anonymized 判定，而不是按列在不在；判定不出来就不给（fail closed）。
"""

import json
import unittest

from .gateway_support import INSTANCE
from .test_responses import (
    F1,
    F2,
    SID,
    FakeExportEngine,
    ReadTestCase,
    request_body,
)

TOKEN_COLUMN = "token"


class PropertiesEngine(FakeExportEngine):
    """多答一个 get_survey_properties，用来说明问卷匿名与否。"""

    def __init__(self, *args, anonymized="N", properties_error=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.anonymized = anonymized
        self.properties_error = properties_error
        self.properties_calls = 0

    def transport(self, payload):
        request = json.loads(payload.decode("utf-8"))
        if request["method"] == "get_survey_properties":
            self.properties_calls += 1
            result = (self.properties_error if self.properties_error is not None
                      else {"sid": SID, "anonymized": self.anonymized})
            return json.dumps({"id": 1, "result": result, "error": None}).encode("utf-8")
        return super().transport(payload)


def with_respondent(**overrides):
    return request_body(ids=(1, 2), fields=(F1,), includeRespondent=True, **overrides)


class TokenComesBackTest(ReadTestCase):
    def test_a_named_survey_reports_the_token_each_response_was_answered_with(self):
        engine = PropertiesEngine(rows={
            1: {F1: "a", TOKEN_COLUMN: "tok-zhang"},
            2: {F1: "b", TOKEN_COLUMN: "tok-li"},
        }, anonymized="N")

        status, payload = self.read(engine, with_respondent())

        self.assertEqual(200, status, payload)
        self.assertEqual(["tok-zhang", "tok-li"], [item["token"] for item in payload["responses"]])

    def test_a_response_with_no_token_reports_null_rather_than_an_empty_string(self):
        engine = PropertiesEngine(rows={1: {F1: "a", TOKEN_COLUMN: ""}}, anonymized="N")

        status, payload = self.read(engine, request_body(ids=(1,), fields=(F1,), includeRespondent=True))

        self.assertEqual(200, status, payload)
        self.assertIsNone(payload["responses"][0]["token"])

    def test_without_the_flag_the_reply_keeps_the_v1_shape(self):
        engine = PropertiesEngine(rows={1: {F1: "a", TOKEN_COLUMN: "tok"}}, anonymized="N")

        status, payload = self.read(engine, request_body(ids=(1,), fields=(F1,)))

        self.assertEqual(200, status, payload)
        self.assertNotIn("token", payload["responses"][0])
        self.assertEqual(0, engine.properties_calls, "不要令牌就不该多问一次问卷属性")


class AnonymousSurveysNeverReportTokensTest(ReadTestCase):
    def test_an_anonymous_survey_reports_null_even_when_the_column_still_holds_one(self):
        # 先以非匿名激活、写好 token 列，之后把 anonymized 改成 Y：列和值都还在。
        engine = PropertiesEngine(rows={1: {F1: "a", TOKEN_COLUMN: "tok-leaked"}}, anonymized="Y")

        status, payload = self.read(engine, request_body(ids=(1,), fields=(F1,), includeRespondent=True))

        self.assertEqual(200, status, payload)
        self.assertIsNone(payload["responses"][0]["token"])

    def test_an_unreadable_anonymity_setting_withholds_the_token(self):
        engine = PropertiesEngine(rows={1: {F1: "a", TOKEN_COLUMN: "tok"}},
                                  properties_error={"status": "No permission"})

        status, payload = self.read(engine, request_body(ids=(1,), fields=(F1,), includeRespondent=True))

        # 判定不出来就不给，而不是照发。
        self.assertIn(status, (200, 502), payload)
        if status == 200:
            self.assertIsNone(payload["responses"][0]["token"])


class TheTokenColumnIsNotAnOrdinaryFieldTest(ReadTestCase):
    """否则匿名闸门一绕就过：直接把 token 当普通列请求。"""

    def test_asking_for_the_token_column_as_a_field_is_rejected(self):
        engine = PropertiesEngine(rows={1: {F1: "a", TOKEN_COLUMN: "tok"}}, anonymized="Y")

        status, payload = self.read(engine, request_body(ids=(1,), fields=(F1, TOKEN_COLUMN)))

        self.assertEqual(400, status, payload)
        self.assertEqual("invalid_request", payload["error"])

    def test_the_flag_must_be_a_boolean(self):
        engine = PropertiesEngine(rows={1: {F1: "a"}})

        status, payload = self.read(engine, request_body(ids=(1,), fields=(F1,), includeRespondent="yes"))

        self.assertEqual(400, status, payload)
