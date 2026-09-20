"""题型形状表：决定每道题在 get_fieldmap 里应该出现哪些行。"""

import unittest

from pubgw.model import SurveyDefinition
from pubgw.qtypes import expected_rows, shape_of


def question(**overrides):
    payload = {"uuid": "q1", "code": "Q1", "type": "L", "text": "选一个"}
    payload.update(overrides)
    return SurveyDefinition.from_dict(
        {
            "definitionVersion": 1,
            "uuid": "s",
            "title": "P0",
            "language": "en",
            "settings": {},
            "groups": [{"uuid": "g1", "title": "G1", "questions": [payload]}],
        }
    ).groups[0].questions[0]


class ShapeTest(unittest.TestCase):
    def test_knows_the_supported_types(self):
        self.assertTrue(shape_of("L").needs_answers)
        self.assertFalse(shape_of("S").needs_answers)
        self.assertIsNone(shape_of("Z"))

    def test_dual_scale_declares_two_answer_scales(self):
        self.assertEqual((0, 1), shape_of("1").answer_scales)
        self.assertEqual((0,), shape_of("F").answer_scales)


class ExpectedRowsTest(unittest.TestCase):
    def test_a_plain_question_produces_one_row(self):
        self.assertEqual((("Q1", "", 0),), expected_rows(question(type="S")))

    def test_a_display_question_still_gets_a_column(self):
        """引擎给说明文字题也建一列，平台必须照实记账。"""
        self.assertEqual((("Q1", "", 0),), expected_rows(question(type="X")))

    def test_other_adds_a_dedicated_row(self):
        rows = expected_rows(question(type="L", other=True, answers=[{"code": "A1", "text": "甲"}]))

        self.assertIn(("Q1", "other", 0), rows)

    def test_multiple_choice_produces_one_row_per_subquestion(self):
        rows = expected_rows(
            question(
                type="M",
                subquestions=[
                    {"uuid": "a", "code": "SQ001", "text": "甲"},
                    {"uuid": "b", "code": "SQ002", "text": "乙"},
                ],
            )
        )

        self.assertEqual((("Q1", "SQ001", 0), ("Q1", "SQ002", 0)), rows)

    def test_multiple_choice_with_comments_adds_a_comment_row_per_subquestion(self):
        rows = expected_rows(
            question(type="P", subquestions=[{"uuid": "a", "code": "SQ001", "text": "甲"}])
        )

        self.assertEqual((("Q1", "SQ001", 0), ("Q1", "SQ001comment", 0)), rows)

    def test_multiple_choice_with_comments_and_other_adds_othercomment(self):
        rows = expected_rows(
            question(type="P", other=True, subquestions=[{"uuid": "a", "code": "SQ001", "text": "甲"}])
        )

        self.assertIn(("Q1", "other", 0), rows)
        self.assertIn(("Q1", "othercomment", 0), rows)

    def test_dual_scale_produces_two_rows_per_subquestion(self):
        rows = expected_rows(
            question(
                type="1",
                subquestions=[{"uuid": "a", "code": "SQ001", "text": "甲"}],
                answers=[
                    {"code": "A1", "text": "一", "scale": 0},
                    {"code": "B1", "text": "二", "scale": 1},
                ],
            )
        )

        self.assertEqual((("Q1", "SQ001", 0), ("Q1", "SQ001", 1)), rows)


if __name__ == "__main__":
    unittest.main()
