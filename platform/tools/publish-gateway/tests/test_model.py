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


class UuidCharsetTest(unittest.TestCase):
    """UUID 是系统边界上的外部数据，字符集必须在解析期就钉死。

    理由不是「防提权」——生成的文本会被标准 v2 解析器重新解析，走同样的类型检查、
    引用检查与环检测，而作者本来就能直接写 v2 DSL 表达式，注入者拿不到新能力。
    真正的问题有两个：

    1. **正确性**：校验时看的是原始引用，渲染出来的文本却可能指向另一道题；
    2. **不变式**：契约 §6「作者文本的安全性」声称生成的表达式里没有一处作者自由文本。
       只要 UUID 能带任意字符，这句话就是假的——而且是潜伏的：将来只要有一个插值点
       不再被重新解析，它立刻变成真注入。

    取值范围要同时容纳现存的两种写法：标准 UUID 与平台的 slug。
    """

    def definition_with_uuid(self, where, value):
        payload = minimal_payload()
        if where == "definition":
            payload["uuid"] = value
        elif where == "group":
            payload["groups"][0]["uuid"] = value
        else:
            payload["groups"][0]["questions"][0]["uuid"] = value
        return payload

    def test_standard_uuids_are_accepted(self):
        payload = self.definition_with_uuid("question", "11111111-1111-4111-8111-111111111111")
        self.assertEqual(
            SurveyDefinition.from_dict(payload).groups[0].questions[0].uuid,
            "11111111-1111-4111-8111-111111111111",
        )

    def test_platform_slugs_are_accepted(self):
        for slug in ("q-single", "sq-m2", "logic-0001", "q1", "def_0001", "q-"):
            payload = self.definition_with_uuid("question", slug)
            self.assertEqual(SurveyDefinition.from_dict(payload).groups[0].questions[0].uuid, slug)

    def test_expression_syntax_in_a_uuid_is_rejected(self):
        # 复现：这个 UUID 曾能从 scoring 生成的 q("…") 字符串字面量里逃出来。
        hostile = 'X") + 999, 0) * 1, sum(1'
        for where in ("definition", "group", "question"):
            with self.assertRaises(DefinitionError, msg=where):
                SurveyDefinition.from_dict(self.definition_with_uuid(where, hostile))

    def test_other_unsafe_shapes_are_rejected(self):
        for value in ('a"b', "a b", "a{b}", "-lead", "_lead", "a\\b", "a\nb", "a" * 129):
            with self.assertRaises(DefinitionError, msg=repr(value)):
                SurveyDefinition.from_dict(self.definition_with_uuid("question", value))

    def test_subquestion_uuids_are_checked_too(self):
        payload = minimal_payload()
        payload["definitionVersion"] = 2
        payload["groups"][0]["questions"][0]["subquestions"] = [
            {"uuid": 'X") or (1', "code": "SQ001", "text": "一"}
        ]
        with self.assertRaises(DefinitionError):
            SurveyDefinition.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
