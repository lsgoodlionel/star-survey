"""考试答案键的发布前校验（WP-09.1，契约 survey-exam-v1 §2、§3）。

两类规则，性质不同：

- 普通的结构与取值校验，与访问策略同一路数（未知键一律报错，半个答案键比没有更危险）；
- **不下发**的守门规则（§4）：答案键一旦被写进计分表或逻辑条件，就会被编译成引擎的
  ExpressionScript，而引擎的 ExpressionManager 会把它翻成页面 JS
  （em_manager_helper.php:4351 GetJavaScriptEquivalentOfExpression）。那是答案泄漏的
  真实通道，所以在这里就拒绝，而不是指望作者自觉。
"""

import copy
import unittest

from pubgw.model import SurveyDefinition
from pubgw.validate import validate_definition

from .exam_fixtures import TEXT_SENTINEL, exam_definition, full_exam, single_key
from .fixtures import sample_payload


def issue_codes(definition):
    return [issue.code for issue in validate_definition(definition).issues]


def issues_for(exam, **settings):
    return issue_codes(exam_definition(exam, **settings))


class AcceptedExamTest(unittest.TestCase):
    def test_full_answer_key_is_valid(self):
        self.assertEqual([], issues_for(full_exam()))

    def test_definition_without_exam_has_none(self):
        self.assertIsNone(SurveyDefinition.from_dict(sample_payload()).exam)

    def test_exam_is_allowed_in_logic_definitions_too(self):
        payload = copy.deepcopy(sample_payload())
        payload["definitionVersion"] = 2
        payload["exam"] = single_key()
        self.assertEqual([], issue_codes(SurveyDefinition.from_dict(payload)))

    def test_question_may_be_referenced_by_uuid(self):
        self.assertEqual([], issues_for(single_key(question="q-single")))


class StructureTest(unittest.TestCase):
    def test_exam_must_be_an_object(self):
        self.assertIn("E_EXAM_INVALID", issues_for([{"question": "QSINGLE"}]))

    def test_version_must_be_one(self):
        self.assertIn("E_EXAM_VERSION", issues_for({"examVersion": 2, "answerKey": []}))

    def test_version_is_required(self):
        self.assertIn("E_EXAM_VERSION", issues_for({"answerKey": []}))

    def test_unknown_top_level_key_is_rejected(self):
        exam = single_key()
        exam["answerkey"] = []
        self.assertIn("E_EXAM_UNKNOWN_KEY", issues_for(exam))

    def test_unknown_entry_key_is_rejected(self):
        self.assertIn("E_EXAM_UNKNOWN_KEY", issues_for(single_key(ignorecase=True)))

    def test_answer_key_must_not_be_empty(self):
        self.assertIn("E_EXAM_EMPTY", issues_for({"examVersion": 1, "answerKey": []}))

    def test_answer_key_must_be_a_list(self):
        self.assertIn("E_EXAM_TYPE", issues_for({"examVersion": 1, "answerKey": {}}))

    def test_correct_must_be_a_non_empty_list_of_strings(self):
        self.assertIn("E_EXAM_TYPE", issues_for(single_key(correct="A2")))
        self.assertIn("E_EXAM_EMPTY", issues_for(single_key(correct=[])))
        self.assertIn("E_EXAM_TYPE", issues_for(single_key(correct=[2])))

    def test_flags_must_be_boolean(self):
        self.assertIn("E_EXAM_TYPE", issues_for(
            single_key(question="QTEXT", correct=["x"], ignoreCase="yes")))


class PointsTest(unittest.TestCase):
    def test_points_are_required(self):
        entry = {"question": "QSINGLE", "correct": ["A2"]}
        self.assertIn("E_EXAM_TYPE", issues_for({"examVersion": 1, "answerKey": [entry]}))

    def test_points_must_be_positive(self):
        self.assertIn("E_EXAM_RANGE", issues_for(single_key(points=0)))
        self.assertIn("E_EXAM_RANGE", issues_for(single_key(points=-1)))

    def test_points_have_an_upper_bound(self):
        self.assertIn("E_EXAM_RANGE", issues_for(single_key(points=100000)))

    def test_points_may_be_fractional(self):
        self.assertEqual([], issues_for(single_key(points=2.5)))


class QuestionReferenceTest(unittest.TestCase):
    def test_unknown_question_is_rejected(self):
        self.assertIn("E_EXAM_QUESTION", issues_for(single_key(question="QNOPE")))

    def test_a_question_may_be_keyed_only_once(self):
        exam = single_key()
        exam["answerKey"].append({"question": "QSINGLE", "correct": ["A1"], "points": 1})
        self.assertIn("E_EXAM_DUPLICATE", issues_for(exam))

    def test_uuid_and_code_for_the_same_question_still_collide(self):
        exam = single_key()
        exam["answerKey"].append({"question": "q-single", "correct": ["A1"], "points": 1})
        self.assertIn("E_EXAM_DUPLICATE", issues_for(exam))

    def test_array_questions_cannot_be_keyed(self):
        """双尺度数组不是客观题：一道题有多行多尺度，没有单一的「正确答案」。"""
        self.assertIn("E_EXAM_QUESTION_TYPE", issues_for(single_key(question="QDUAL", correct=["L1"])))


class CorrectAnswerTest(unittest.TestCase):
    def test_choice_answer_must_be_one_of_the_options(self):
        self.assertIn("E_EXAM_ANSWER", issues_for(single_key(correct=["A9"])))

    def test_single_choice_takes_exactly_one_correct_answer(self):
        self.assertIn("E_EXAM_ANSWER", issues_for(single_key(correct=["A1", "A2"])))

    def test_multiple_choice_answer_must_be_one_of_the_subquestions(self):
        self.assertIn("E_EXAM_ANSWER", issues_for(
            single_key(question="QMULTI", correct=["SQ001", "SQ009"])))

    def test_multiple_choice_correct_set_has_no_duplicates(self):
        self.assertIn("E_EXAM_ANSWER", issues_for(
            single_key(question="QMULTI", correct=["SQ001", "SQ001"])))

    def test_text_answer_is_free_text(self):
        self.assertEqual([], issues_for(single_key(question="QTEXT", correct=["anything at all"])))

    def test_text_answer_may_have_several_accepted_spellings(self):
        self.assertEqual([], issues_for(single_key(question="QTEXT", correct=["Paris", "巴黎"])))

    def test_blank_correct_answer_is_rejected(self):
        self.assertIn("E_EXAM_ANSWER", issues_for(single_key(question="QTEXT", correct=["   "])))

    def test_case_flags_only_apply_to_text_questions(self):
        self.assertIn("E_EXAM_MATCH", issues_for(single_key(ignoreCase=True)))


class NonDisclosureTest(unittest.TestCase):
    """§4：凡是会被编译进 ExpressionScript 的地方，都不许出现答案键。

    计分表与逻辑条件都会变成引擎表达式，而引擎会把表达式翻成页面 JS。所以
    「答案不下发」不是运行时才守的，发布期就得守住。
    """

    def scoring_definition(self, exam, scoring):
        payload = copy.deepcopy(sample_payload())
        payload["definitionVersion"] = 2
        payload["exam"] = copy.deepcopy(exam)
        payload["scoring"] = copy.deepcopy(scoring)
        return SurveyDefinition.from_dict(payload)

    def test_a_keyed_question_may_not_appear_in_a_scoring_item(self):
        scoring = [{
            "uuid": "score-1", "code": "STOTAL", "title": "总分",
            "items": [{"question": "QSINGLE", "points": {"A1": 0, "A2": 5}}],
        }]
        codes = issue_codes(self.scoring_definition(single_key(), scoring))
        self.assertIn("E_EXAM_KEY_IN_SCORING", codes)

    def test_scoring_on_an_unkeyed_question_is_fine(self):
        scoring = [{
            "uuid": "score-1", "code": "STOTAL", "title": "总分",
            "items": [{"question": "QMULTI", "points": {"SQ001": 1, "SQ002": 2}}],
        }]
        codes = issue_codes(self.scoring_definition(single_key(), scoring))
        self.assertNotIn("E_EXAM_KEY_IN_SCORING", codes)

    def logic_definition(self, exam, code, expression):
        payload = copy.deepcopy(sample_payload())
        payload["definitionVersion"] = 2
        payload["exam"] = copy.deepcopy(exam)
        for group in payload["groups"]:
            for question in group["questions"]:
                if question["code"] == code:
                    question["condition"] = expression
        return SurveyDefinition.from_dict(payload)

    def test_a_condition_comparing_a_keyed_question_to_its_answer_is_rejected(self):
        codes = issue_codes(self.logic_definition(single_key(), "QTEXT", 'QSINGLE == "A2"'))
        self.assertIn("E_EXAM_KEY_IN_LOGIC", codes)

    def test_a_condition_on_a_wrong_answer_is_allowed(self):
        """按「选了甲」分支是正常的问卷逻辑，只有拿正确答案做条件才是泄漏。"""
        codes = issue_codes(self.logic_definition(single_key(), "QTEXT", 'QSINGLE == "A1"'))
        self.assertNotIn("E_EXAM_KEY_IN_LOGIC", codes)

    def test_a_condition_on_the_text_answer_is_rejected(self):
        exam = single_key(question="QTEXT", correct=[TEXT_SENTINEL])
        codes = issue_codes(self.logic_definition(exam, "QSINGLE", 'QTEXT == "%s"' % TEXT_SENTINEL))
        self.assertIn("E_EXAM_KEY_IN_LOGIC", codes)


if __name__ == "__main__":
    unittest.main()
