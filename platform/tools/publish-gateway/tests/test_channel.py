"""网关→插件鉴权通道的客户端（契约 platform/contracts/plugin-channel-v1.md，ADR 0018）。

固定签名向量与插件侧 MjyChannelAuthTest 用的是同一组值：任一端改了派生或签名算法，
两端都会红。向量本身由契约算出（HMAC-SHA256），不是从实现里抄的。
"""

import unittest

from pubgw.channel import (
    CHANNEL_FUNCTION,
    MAX_QUESTION_CODES,
    MAX_RESPONSE_IDS,
    PLUGIN_NAME,
    ChannelError,
    ExtensionAnswerClient,
    InvalidChannelRequest,
    canonical_query,
    derive_channel_secret,
    sign,
)

INSTANCE = "hd-engine-01"
INSTANCE_SECRET = "0123456789abcdef0123456789abcdef-instance"
CHANNEL_SECRET = "204745a72bcb796d18bf2df097af7efea324f61077971e41b50189196036ff31"
NOW = 1_800_000_000
INDEX_URL = "http://engine-01/index.php"
RPC_URL = "http://engine-01/index.php/admin/remotecontrol"

CANONICAL = (
    "function=extensionAnswers&generation=gen-1&plugin=MjyQuestionExtensions"
    "&questionCodes=TABLE1&responseIds=1001%2C1002&sid=42&ts=1800000000"
)
SIGNATURE = "5c9b53726ed822c5ce91ea2586453ce85e1698b043e421a20527d5e0e82be7f4"

GOOD_BODY = (
    b'{"plugin":"MjyQuestionExtensions","engineInstanceId":"hd-engine-01","surveyId":42,'
    b'"generation":"gen-1","answers":{"1001":{"TABLE1":{"structureVersion":"v3",'
    b'"isValid":true,"rows":[{"item":"\\u7532","qty":"2"},{"item":"\\u4e59","qty":"3"}]}}}}'
)


def client(fetch, secret=CHANNEL_SECRET, now=NOW):
    return ExtensionAnswerClient(INDEX_URL, INSTANCE, secret, fetch=fetch, now=lambda: now)


def collecting(body=GOOD_BODY):
    """返回 (urls, fetch)：记录请求过的 URL，并回一段固定应答。"""
    urls = []

    def fetch(url):
        urls.append(url)
        return body

    return urls, fetch


class DerivationTest(unittest.TestCase):
    """通道密钥从实例密钥再派生一层（ADR 0018 决定 2）。"""

    def test_derives_the_contract_vector(self):
        self.assertEqual(CHANNEL_SECRET, derive_channel_secret(INSTANCE_SECRET, INSTANCE))

    def test_each_instance_gets_its_own_secret(self):
        other = derive_channel_secret(INSTANCE_SECRET, "hd-engine-02")
        self.assertNotEqual(CHANNEL_SECRET, other)

    def test_channel_secret_is_not_the_instance_secret(self):
        # 单向：拿到通道密钥推不出实例密钥，网关被攻破也伪造不了引擎事件。
        self.assertNotIn(INSTANCE_SECRET, derive_channel_secret(INSTANCE_SECRET, INSTANCE))

    def test_rejects_a_short_instance_secret(self):
        with self.assertRaises(ValueError):
            derive_channel_secret("too-short", INSTANCE)


class CanonicalQueryTest(unittest.TestCase):
    """规范化查询串：排序 ＋ RFC 3986 编码，两端必须算出同一个串。"""

    def test_sorts_by_parameter_name(self):
        self.assertEqual("a=1&b=2&c=3", canonical_query({"c": "3", "a": "1", "b": "2"}))

    def test_percent_encodes_reserved_characters(self):
        self.assertEqual("ids=1%2C2", canonical_query({"ids": "1,2"}))

    def test_leaves_rfc3986_unreserved_characters_alone(self):
        self.assertEqual("v=A-z0.9_x~y", canonical_query({"v": "A-z0.9_x~y"}))

    def test_encodes_non_ascii_as_utf8(self):
        self.assertEqual("v=%E7%94%B2", canonical_query({"v": "甲"}))

    def test_matches_the_contract_vector(self):
        self.assertEqual(CANONICAL, canonical_query({
            "plugin": PLUGIN_NAME,
            "function": CHANNEL_FUNCTION,
            "sid": "42",
            "generation": "gen-1",
            "responseIds": "1001,1002",
            "questionCodes": "TABLE1",
            "ts": str(NOW),
        }))


class SignatureTest(unittest.TestCase):

    def test_matches_the_contract_vector(self):
        self.assertEqual(SIGNATURE, sign(CHANNEL_SECRET, str(NOW), CANONICAL))

    def test_a_different_timestamp_changes_the_signature(self):
        self.assertNotEqual(SIGNATURE, sign(CHANNEL_SECRET, str(NOW + 1), CANONICAL))

    def test_a_different_query_changes_the_signature(self):
        # 覆盖整个查询串：改 sid 就签不过，签好的请求改投不到别人的答卷。
        self.assertNotEqual(SIGNATURE, sign(CHANNEL_SECRET, str(NOW), CANONICAL.replace("sid=42", "sid=43")))


class RequestTest(unittest.TestCase):

    def test_builds_the_signed_url(self):
        urls, fetch = collecting()
        client(fetch).read(42, "gen-1", [1001, 1002], ["TABLE1"])
        self.assertEqual(
            ["{}/plugins/direct?{}&sig={}".format(INDEX_URL, CANONICAL, SIGNATURE)], urls)

    def test_sends_response_ids_in_ascending_order(self):
        # 严格升序让签名串规范：同一组答卷号只有一种写法。
        urls, fetch = collecting()
        client(fetch).read(42, "gen-1", [1002, 1001], ["TABLE1"])
        self.assertIn("responseIds=1001%2C1002", urls[0])

    def test_never_puts_the_secret_in_the_url(self):
        urls, fetch = collecting()
        client(fetch).read(42, "gen-1", [1001, 1002], ["TABLE1"])
        self.assertNotIn(CHANNEL_SECRET, urls[0])

    def test_from_rpc_url_strips_the_remotecontrol_suffix(self):
        urls, fetch = collecting()
        built = ExtensionAnswerClient.from_rpc_url(
            RPC_URL, INSTANCE, CHANNEL_SECRET, fetch=fetch, now=lambda: NOW)
        built.read(42, "gen-1", [1001, 1002], ["TABLE1"])
        self.assertTrue(urls[0].startswith(INDEX_URL + "/plugins/direct?"), urls[0])

    def test_from_rpc_url_returns_none_for_an_unknown_shape(self):
        self.assertIsNone(ExtensionAnswerClient.from_rpc_url(
            "http://engine-01/somewhere-else", INSTANCE, CHANNEL_SECRET))


class RequestGuardTest(unittest.TestCase):
    """越界的请求在发出之前就被挡住，不浪费引擎的一次读取。"""

    def unreachable(self, url):
        raise AssertionError("must not reach the engine: " + url)

    def read(self, **kwargs):
        call = {"survey_id": 42, "generation": "gen-1",
                "response_ids": [1001], "question_codes": ["TABLE1"]}
        call.update(kwargs)
        return client(self.unreachable).read(**call)

    def test_rejects_too_many_response_ids(self):
        with self.assertRaises(InvalidChannelRequest):
            self.read(response_ids=list(range(1, MAX_RESPONSE_IDS + 2)))

    def test_rejects_too_many_question_codes(self):
        with self.assertRaises(InvalidChannelRequest):
            self.read(question_codes=["Q%d" % i for i in range(MAX_QUESTION_CODES + 1)])

    def test_rejects_an_empty_page(self):
        with self.assertRaises(InvalidChannelRequest):
            self.read(response_ids=[])

    def test_rejects_duplicate_response_ids(self):
        with self.assertRaises(InvalidChannelRequest):
            self.read(response_ids=[1001, 1001])

    def test_rejects_a_non_positive_response_id(self):
        with self.assertRaises(InvalidChannelRequest):
            self.read(response_ids=[0])

    def test_rejects_a_question_code_outside_the_grammar(self):
        with self.assertRaises(InvalidChannelRequest):
            self.read(question_codes=["TAB LE"])

    def test_rejects_a_generation_outside_the_grammar(self):
        with self.assertRaises(InvalidChannelRequest):
            self.read(generation="gen 1")


class ParseTest(unittest.TestCase):

    def read(self, body):
        _, fetch = collecting(body)
        return client(fetch).read(42, "gen-1", [1001, 1002], ["TABLE1"])

    def test_returns_rows_keyed_by_response_id_and_question_code(self):
        answers = self.read(GOOD_BODY)
        self.assertEqual([1001], list(answers))
        answer = answers[1001]["TABLE1"]
        self.assertEqual("v3", answer.structure_version)
        self.assertTrue(answer.is_valid)
        self.assertEqual(({"item": "甲", "qty": "2"}, {"item": "乙", "qty": "3"}), answer.rows)

    def test_an_empty_page_is_not_an_error(self):
        # sid 不存在或代次不匹配是正常的空（ADR 0013 决定 3），不是失败。
        self.assertEqual({}, self.read(b'{"plugin":"MjyQuestionExtensions","answers":{}}'))

    def test_rejects_a_body_that_is_not_json(self):
        with self.assertRaises(ChannelError):
            self.read(b"<html>gateway timeout</html>")

    def test_rejects_an_error_body(self):
        # 401 的体绝不能被当成「没有数据」——那会让一次密钥配错变成一次静默的数据缺失。
        with self.assertRaises(ChannelError):
            self.read(b'{"error":"unauthorized"}')

    def test_rejects_answers_that_are_not_an_object(self):
        with self.assertRaises(ChannelError):
            self.read(b'{"answers":[]}')

    def test_rejects_a_response_id_that_was_not_asked_for(self):
        with self.assertRaises(ChannelError):
            self.read(b'{"answers":{"9999":{"TABLE1":{"structureVersion":"v3",'
                      b'"isValid":true,"rows":[]}}}}')

    def test_rejects_a_question_code_that_was_not_asked_for(self):
        with self.assertRaises(ChannelError):
            self.read(b'{"answers":{"1001":{"OTHER":{"structureVersion":"v3",'
                      b'"isValid":true,"rows":[]}}}}')

    def test_rejects_rows_that_are_not_a_list(self):
        with self.assertRaises(ChannelError):
            self.read(b'{"answers":{"1001":{"TABLE1":{"structureVersion":"v3",'
                      b'"isValid":true,"rows":{}}}}}')

    def test_rejects_a_cell_value_that_is_not_a_string(self):
        with self.assertRaises(ChannelError):
            self.read(b'{"answers":{"1001":{"TABLE1":{"structureVersion":"v3",'
                      b'"isValid":true,"rows":[{"qty":2}]}}}}')

    def test_rejects_a_missing_structure_version(self):
        with self.assertRaises(ChannelError):
            self.read(b'{"answers":{"1001":{"TABLE1":{"isValid":true,"rows":[]}}}}')


class FailClosedTest(unittest.TestCase):
    """引擎不可达 / 非 200 一律抛错，调用方据此整页失败关闭（契约「网关侧必须做到」）。"""

    def test_transport_failure_becomes_a_channel_error(self):
        def fetch(url):
            raise OSError("connection refused")

        with self.assertRaises(ChannelError):
            client(fetch).read(42, "gen-1", [1001], ["TABLE1"])

    def test_the_secret_never_reaches_the_error_message(self):
        def fetch(url):
            raise OSError("connection refused to " + url)

        with self.assertRaises(ChannelError) as caught:
            client(fetch).read(42, "gen-1", [1001], ["TABLE1"])
        self.assertNotIn(CHANNEL_SECRET, str(caught.exception))

    def test_the_answer_values_never_reach_the_error_message(self):
        # 作答是个人数据：解析失败的异常里不能带回值本身。
        body = (b'{"answers":{"1001":{"TABLE1":{"structureVersion":"v3","isValid":true,'
                b'"rows":[{"secret-answer-value":12345}]}}}}')
        _, fetch = collecting(body)
        with self.assertRaises(ChannelError) as caught:
            client(fetch).read(42, "gen-1", [1001], ["TABLE1"])
        self.assertNotIn("secret-answer-value", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
