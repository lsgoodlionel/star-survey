"""定义模型的边界校验：外部 JSON 不可信，缺项、类型错误都必须当场报错。"""

import unittest

from pubgw.model import DefinitionError, SurveyDefinition


def minimal_payload():
    return {
        "definitionVersion": 1,
        "uuid": "survey-uuid",
        "title": "P0 发布网关",
        "language": "en",
        "settings": {"datestamp": "Y"},
        "groups": [
            {
                "uuid": "group-uuid",
                "title": "G1",
                "questions": [
                    {
                        "uuid": "question-uuid",
                        "code": "Q1",
                        "type": "L",
                        "text": "选一个",
                        "answers": [{"code": "A1", "text": "甲"}],
                    }
                ],
            }
        ],
    }


class SurveyDefinitionParsingTest(unittest.TestCase):
    def test_parses_a_minimal_definition(self):
        definition = SurveyDefinition.from_dict(minimal_payload())

        self.assertEqual("survey-uuid", definition.uuid)
        self.assertEqual(1, len(definition.groups))
        question = definition.groups[0].questions[0]
        self.assertEqual("Q1", question.code)
        self.assertEqual("L", question.type)
        self.assertEqual(("A1",), tuple(answer.code for answer in question.answers))
        self.assertFalse(question.mandatory)

    def test_rejects_an_unknown_definition_version(self):
        payload = dict(minimal_payload(), definitionVersion=99)

        with self.assertRaises(DefinitionError) as caught:
            SurveyDefinition.from_dict(payload)

        self.assertIn("definitionVersion", str(caught.exception))

    def test_rejects_a_missing_required_field(self):
        payload = minimal_payload()
        payload.pop("language")

        with self.assertRaises(DefinitionError) as caught:
            SurveyDefinition.from_dict(payload)

        self.assertIn("language", str(caught.exception))

    def test_rejects_a_group_list_that_is_not_a_list(self):
        payload = dict(minimal_payload(), groups={"uuid": "x"})

        with self.assertRaises(DefinitionError):
            SurveyDefinition.from_dict(payload)

    def test_rejects_a_question_without_a_uuid(self):
        payload = minimal_payload()
        payload["groups"][0]["questions"][0].pop("uuid")

        with self.assertRaises(DefinitionError) as caught:
            SurveyDefinition.from_dict(payload)

        self.assertIn("uuid", str(caught.exception))

    def test_keeps_declared_order_and_defaults(self):
        payload = minimal_payload()
        payload["groups"][0]["questions"][0]["subquestions"] = [
            {"uuid": "sq-b", "code": "SQ002", "text": "乙"},
            {"uuid": "sq-a", "code": "SQ001", "text": "甲"},
        ]

        definition = SurveyDefinition.from_dict(payload)

        subquestions = definition.groups[0].questions[0].subquestions
        self.assertEqual(("SQ002", "SQ001"), tuple(item.code for item in subquestions))
        self.assertEqual((0, 0), tuple(item.scale for item in subquestions))

    def test_collects_every_question_in_document_order(self):
        payload = minimal_payload()
        payload["groups"].append(
            {
                "uuid": "group-2",
                "title": "G2",
                "questions": [
                    {"uuid": "q2", "code": "Q2", "type": "S", "text": "写点什么"},
                ],
            }
        )

        definition = SurveyDefinition.from_dict(payload)

        self.assertEqual(["Q1", "Q2"], [q.code for q in definition.questions()])

    def test_settings_must_be_strings(self):
        payload = dict(minimal_payload(), settings={"datestamp": True})

        with self.assertRaises(DefinitionError):
            SurveyDefinition.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
