"""发布后回读邀请码（ADR 0016 待办）。

平台自己发邀请，但 token 是引擎生成的：网关的 ``add_participants`` 固定
``create_token=true``，定义里写的 token 会被替换。在这一片之前平台拿不到生成的码，
端到端只能直接查 ``lime_tokens_<sid>`` 绕过去。现在发布回执带回来。

对应关系只能按位置：引擎 ``foreach ($aParticipantData as &$aParticipant)`` 逐条原地
替换（remotecontrol_handle.php:2123-2136），返回列表与提交列表严格同序；而成功的条目
会被整条换成 token 行属性，平台传的非列字段被 ``array_intersect_key`` 丢掉，姓名邮箱
在开了字段加密的问卷上还是密文——按 firstname 对不回来。
"""

import unittest

from pubgw.model import DefinitionError, SurveyDefinition

from .fakes import FakeEngine
from .fixtures import sample_payload
from .test_publish import publisher_for


def definition_with(participants):
    payload = sample_payload()
    payload["participants"] = participants
    return SurveyDefinition.from_dict(payload)


TWO = [
    {"ref": "contact-7", "email": "p0@example.invalid", "lastname": "P0"},
    {"ref": "contact-9", "email": "p1@example.invalid", "lastname": "P1"},
]


class ReceiptCarriesTheCodesTest(unittest.TestCase):
    def test_every_participant_comes_back_with_the_engine_generated_code(self):
        definition = definition_with(TWO)
        engine = FakeEngine(definition)

        result = publisher_for(engine).publish(definition)

        self.assertTrue(result.ok, result.failures)
        invitations = result.to_dict()["invitations"]
        self.assertEqual(["contact-7", "contact-9"], [item["ref"] for item in invitations])
        self.assertEqual([0, 1], [item["index"] for item in invitations])
        self.assertTrue(all(item["token"] for item in invitations))
        self.assertEqual(2, len({item["token"] for item in invitations}))

    def test_a_participant_without_a_reference_is_still_addressable_by_index(self):
        definition = definition_with([{"email": "p0@example.invalid"}])
        engine = FakeEngine(definition)

        result = publisher_for(engine).publish(definition)

        self.assertTrue(result.ok, result.failures)
        self.assertEqual([None], [item["ref"] for item in result.to_dict()["invitations"]])

    def test_a_definition_without_participants_keeps_the_v1_receipt_shape(self):
        definition = SurveyDefinition.from_dict(sample_payload())
        engine = FakeEngine(definition)

        result = publisher_for(engine).publish(definition)

        self.assertTrue(result.ok, result.failures)
        self.assertNotIn("invitations", result.to_dict())

    def test_the_reference_is_the_platforms_own_and_never_reaches_the_engine(self):
        definition = definition_with(TWO)
        engine = FakeEngine(definition)

        publisher_for(engine).publish(definition)

        sent = engine.participants[definition_survey_id(engine)]
        self.assertTrue(all("ref" not in entry for entry in sent), sent)


def definition_survey_id(engine):
    return next(iter(engine.participants))


class TheCodesMustBeUsableOrThePublishFailsTest(unittest.TestCase):
    """拿不到码却报成功最糟：平台会登记路由、发出一批空邀请，且无从察觉。"""

    def test_a_short_list_from_the_engine_rolls_back(self):
        definition = definition_with(TWO)
        engine = FakeEngine(definition, participants_returned=1)

        result = publisher_for(engine).publish(definition)

        self.assertFalse(result.ok)
        self.assertEqual("activate", result.failed_stage)
        self.assertTrue(result.rolled_back)

    def test_a_row_without_a_token_rolls_back(self):
        definition = definition_with(TWO)
        engine = FakeEngine(definition, participant_tokens=["tok1", ""])

        result = publisher_for(engine).publish(definition)

        self.assertFalse(result.ok)
        self.assertEqual("activate", result.failed_stage)
        self.assertTrue(result.rolled_back)

    def test_two_participants_sharing_one_token_rolls_back(self):
        definition = definition_with(TWO)
        engine = FakeEngine(definition, participant_tokens=["tok1", "tok1"])

        result = publisher_for(engine).publish(definition)

        self.assertFalse(result.ok)
        self.assertEqual("activate", result.failed_stage)
        self.assertTrue(result.rolled_back)


class ReferencesAreCheckedBeforeTheEngineTest(unittest.TestCase):
    def test_two_participants_cannot_share_a_reference(self):
        with self.assertRaises(DefinitionError):
            definition_with([
                {"ref": "same", "email": "p0@example.invalid"},
                {"ref": "same", "email": "p1@example.invalid"},
            ])

    def test_a_reference_must_be_a_non_empty_string(self):
        with self.assertRaises(DefinitionError):
            definition_with([{"ref": "", "email": "p0@example.invalid"}])
