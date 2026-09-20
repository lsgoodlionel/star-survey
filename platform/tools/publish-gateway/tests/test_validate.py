"""发布前校验：重点是引擎自己不做的那几项检查。"""

import unittest

from pubgw.model import SurveyDefinition
from pubgw.validate import REQUIRED_EXPLICIT_SETTINGS, validate_definition


def explicit_settings(**overrides):
    """把每一项行为关键设置都写成显式值，符合 ADR 0005 决定 2。"""
    settings = {name: "N" for name in REQUIRED_EXPLICIT_SETTINGS}
    settings.update({"format": "G", "questionindex": "0"})
    settings.update(overrides)
    return settings


def single_choice(**overrides):
    question = {
        "uuid": "q1",
        "code": "Q1",
        "type": "L",
        "text": "选一个",
        "answers": [{"code": "A1", "text": "甲"}],
    }
    question.update(overrides)
    return question


def definition_with(question, settings=None, extra_groups=(), **survey_overrides):
    payload = {
        "definitionVersion": 1,
        "uuid": "survey-uuid",
        "title": "P0",
        "language": "en",
        "settings": explicit_settings() if settings is None else settings,
        "groups": [{"uuid": "g1", "title": "G1", "questions": [question]}] + list(extra_groups),
    }
    payload.update(survey_overrides)
    return SurveyDefinition.from_dict(payload)


def codes(report):
    return [issue.code for issue in report.issues]


class EngineGapChecksTest(unittest.TestCase):
    """activate_survey 不跑 checkQuestions()，这些洞只能平台自己堵。"""

    def test_accepts_a_well_formed_definition(self):
        report = validate_definition(definition_with(single_choice()))

        self.assertTrue(report.is_valid, codes(report))

    def test_rejects_a_single_choice_question_without_answer_options(self):
        report = validate_definition(definition_with(single_choice(answers=[])))

        self.assertIn("E_MISSING_ANSWERS", codes(report))

    def test_rejects_an_array_question_without_subquestions(self):
        report = validate_definition(definition_with(single_choice(type="F", subquestions=[])))

        self.assertIn("E_MISSING_SUBQUESTIONS", codes(report))

    def test_rejects_a_dual_scale_question_missing_the_second_answer_scale(self):
        question = single_choice(
            type="1",
            answers=[{"code": "A1", "text": "甲", "scale": 0}],
            subquestions=[{"uuid": "sq1", "code": "SQ001", "text": "行一"}],
        )
        report = validate_definition(definition_with(question))

        self.assertIn("E_MISSING_ANSWER_SCALE", codes(report))

    def test_rejects_answer_options_on_a_text_question(self):
        report = validate_definition(definition_with(single_choice(type="S")))

        self.assertIn("E_UNEXPECTED_ANSWERS", codes(report))

    def test_rejects_subquestions_on_a_single_choice_question(self):
        question = single_choice(subquestions=[{"uuid": "sq1", "code": "SQ001", "text": "行一"}])
        report = validate_definition(definition_with(question))

        self.assertIn("E_UNEXPECTED_SUBQUESTIONS", codes(report))

    def test_rejects_an_unsupported_question_type(self):
        report = validate_definition(definition_with(single_choice(type="Z")))

        self.assertIn("E_UNSUPPORTED_TYPE", codes(report))


class InheritedSettingsTest(unittest.TestCase):
    """继承值不随 LSS 走：同一份包在不同实例上行为会不同。"""

    def test_rejects_an_inherited_char_setting(self):
        report = validate_definition(definition_with(single_choice(), settings=explicit_settings(datestamp="I")))

        self.assertIn("E_INHERITED_SETTING", codes(report))

    def test_rejects_an_inherited_integer_setting(self):
        report = validate_definition(definition_with(single_choice(), settings=explicit_settings(tokenlength="-1")))

        self.assertIn("E_INHERITED_SETTING", codes(report))

    def test_rejects_an_inherited_text_setting(self):
        report = validate_definition(definition_with(single_choice(), settings=explicit_settings(admin="inherit")))

        self.assertIn("E_INHERITED_SETTING", codes(report))

    def test_rejects_the_inherited_captcha_marker(self):
        report = validate_definition(definition_with(single_choice(), settings=explicit_settings(usecaptcha="E")))

        self.assertIn("E_INHERITED_SETTING", codes(report))

    def test_rejects_an_inherited_theme(self):
        report = validate_definition(definition_with(single_choice(), theme="inherit"))

        self.assertIn("E_INHERITED_THEME", codes(report))

    def test_requires_every_behaviour_critical_setting_to_be_present(self):
        report = validate_definition(definition_with(single_choice(), settings={"datestamp": "Y"}))

        self.assertIn("E_SETTING_NOT_EXPLICIT", codes(report))


class QuestionCodeTest(unittest.TestCase):
    """引擎导入时会静默改名，平台必须先用引擎自己的规则挡住。"""

    def test_rejects_a_code_with_a_hyphen(self):
        report = validate_definition(definition_with(single_choice(code="Q-1")))

        self.assertIn("E_QUESTION_CODE_INVALID", codes(report))

    def test_rejects_a_code_that_does_not_start_with_a_letter(self):
        report = validate_definition(definition_with(single_choice(code="1Q")))

        self.assertIn("E_QUESTION_CODE_INVALID", codes(report))

    def test_rejects_a_leading_comma_even_though_the_engine_pattern_allows_it(self):
        report = validate_definition(definition_with(single_choice(code=",Q1")))

        self.assertIn("E_QUESTION_CODE_INVALID", codes(report))

    def test_rejects_a_reserved_word(self):
        report = validate_definition(definition_with(single_choice(code="SID")))

        self.assertIn("E_QUESTION_CODE_RESERVED", codes(report))

    def test_rejects_a_code_longer_than_twenty_characters(self):
        report = validate_definition(definition_with(single_choice(code="Q" * 21)))

        self.assertIn("E_QUESTION_CODE_TOO_LONG", codes(report))

    def test_rejects_duplicate_question_codes_across_groups(self):
        other = {"uuid": "g2", "title": "G2", "questions": [single_choice(uuid="q2")]}
        report = validate_definition(definition_with(single_choice(), extra_groups=[other]))

        self.assertIn("E_QUESTION_CODE_DUPLICATE", codes(report))

    def test_rejects_duplicate_uuids(self):
        other = {"uuid": "g2", "title": "G2", "questions": [single_choice(code="Q2")]}
        report = validate_definition(definition_with(single_choice(), extra_groups=[other]))

        self.assertIn("E_DUPLICATE_UUID", codes(report))


class SubQuestionAndAnswerCodeTest(unittest.TestCase):
    def test_rejects_a_subquestion_code_with_punctuation(self):
        question = single_choice(type="F", subquestions=[{"uuid": "sq1", "code": "SQ 1", "text": "行一"}])
        report = validate_definition(definition_with(question))

        self.assertIn("E_SUBQUESTION_CODE_INVALID", codes(report))

    def test_rejects_duplicate_subquestion_codes_case_insensitively(self):
        question = single_choice(
            type="F",
            subquestions=[
                {"uuid": "sq1", "code": "SQ001", "text": "行一"},
                {"uuid": "sq2", "code": "sq001", "text": "行二"},
            ],
        )
        report = validate_definition(definition_with(question))

        self.assertIn("E_SUBQUESTION_CODE_DUPLICATE", codes(report))

    def test_rejects_the_other_subquestion_code_when_other_is_enabled(self):
        question = single_choice(
            type="M",
            answers=[],
            other=True,
            subquestions=[{"uuid": "sq1", "code": "other", "text": "行一"}],
        )
        report = validate_definition(definition_with(question))

        self.assertIn("E_SUBQUESTION_CODE_RESERVED", codes(report))

    def test_rejects_a_comment_suffix_on_a_multiple_choice_with_comments(self):
        question = single_choice(
            type="P",
            answers=[],
            subquestions=[{"uuid": "sq1", "code": "SQ1comment", "text": "行一"}],
        )
        report = validate_definition(definition_with(question))

        self.assertIn("E_SUBQUESTION_CODE_RESERVED", codes(report))

    def test_rejects_the_time_subquestion_code(self):
        question = single_choice(type="F", subquestions=[{"uuid": "sq1", "code": "time", "text": "行一"}])
        report = validate_definition(definition_with(question))

        self.assertIn("E_SUBQUESTION_CODE_RESERVED", codes(report))

    def test_rejects_an_answer_code_longer_than_five_characters(self):
        report = validate_definition(definition_with(single_choice(answers=[{"code": "A00001", "text": "甲"}])))

        self.assertIn("E_ANSWER_CODE_TOO_LONG", codes(report))

    def test_rejects_a_non_alphanumeric_answer_code(self):
        report = validate_definition(definition_with(single_choice(answers=[{"code": "A-1", "text": "甲"}])))

        self.assertIn("E_ANSWER_CODE_INVALID", codes(report))

    def test_rejects_duplicate_answer_codes_within_one_scale(self):
        answers = [{"code": "A1", "text": "甲"}, {"code": "A1", "text": "乙"}]
        report = validate_definition(definition_with(single_choice(answers=answers)))

        self.assertIn("E_ANSWER_CODE_DUPLICATE", codes(report))

    def test_allows_the_same_answer_code_on_two_scales(self):
        question = single_choice(
            type="1",
            answers=[
                {"code": "A1", "text": "甲", "scale": 0},
                {"code": "A1", "text": "甲", "scale": 1},
            ],
            subquestions=[{"uuid": "sq1", "code": "SQ001", "text": "行一"}],
        )
        report = validate_definition(definition_with(question))

        self.assertNotIn("E_ANSWER_CODE_DUPLICATE", codes(report))


class StructureTest(unittest.TestCase):
    def test_rejects_an_empty_group(self):
        payload = {
            "definitionVersion": 1,
            "uuid": "s",
            "title": "P0",
            "language": "en",
            "settings": explicit_settings(),
            "groups": [{"uuid": "g1", "title": "G1", "questions": []}],
        }
        report = validate_definition(SurveyDefinition.from_dict(payload))

        self.assertIn("E_EMPTY_GROUP", codes(report))

    def test_report_lists_every_problem_not_just_the_first(self):
        report = validate_definition(definition_with(single_choice(code="Q-1", answers=[])))

        self.assertIn("E_QUESTION_CODE_INVALID", codes(report))
        self.assertIn("E_MISSING_ANSWERS", codes(report))


if __name__ == "__main__":
    unittest.main()
