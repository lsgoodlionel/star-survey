"""`translations` 块的校验（契约 survey-branding-v1 §3）。"""

import copy
import unittest

from pubgw.validate import validate_definition

from .branding_fixtures import branded_definition, sample_translations


def codes(definition):
    return sorted({issue.code for issue in validate_definition(definition).issues})


def translated(mutate=None):
    block = sample_translations()
    if mutate is not None:
        mutate(block["zh-Hans"])
    return branded_definition(translations=block, theme="fruity_twentythree")


class AcceptedTranslationsTest(unittest.TestCase):
    def test_the_sample_translation_passes(self):
        self.assertEqual([], codes(translated()))

    def test_a_partial_translation_is_allowed(self):
        definition = branded_definition(
            translations={"zh-Hans": {"title": "只有标题"}}, theme="fruity_twentythree"
        )
        self.assertEqual([], codes(definition))

    def test_an_empty_language_object_is_allowed(self):
        definition = branded_definition(translations={"zh-Hans": {}}, theme="fruity_twentythree")
        self.assertEqual([], codes(definition))


class RejectedTranslationsTest(unittest.TestCase):
    def test_translations_must_be_an_object(self):
        definition = branded_definition(translations=[], theme="fruity_twentythree")
        self.assertIn("E_TRANSLATION_INVALID", codes(definition))

    def test_a_language_must_be_declared_in_additional_languages(self):
        definition = branded_definition(
            translations=sample_translations(), languages=(), theme="fruity_twentythree"
        )
        self.assertIn("E_TRANSLATION_LANGUAGE", codes(definition))

    def test_the_base_language_may_not_be_translated(self):
        definition = branded_definition(
            translations={"en": {"title": "x"}}, languages=("zh-Hans",), theme="fruity_twentythree"
        )
        self.assertIn("E_TRANSLATION_LANGUAGE", codes(definition))

    def test_an_unknown_key_in_a_language_is_rejected(self):
        self.assertIn(
            "E_TRANSLATION_UNKNOWN_KEY",
            codes(translated(lambda block: block.update({"welcome": "hi"}))),
        )

    def test_an_unknown_key_inside_a_question_is_rejected(self):
        def mutate(block):
            block["questions"]["q-single"]["mandatory"] = True

        self.assertIn("E_TRANSLATION_UNKNOWN_KEY", codes(translated(mutate)))

    def test_a_group_uuid_must_exist(self):
        def mutate(block):
            block["groups"]["grp-missing"] = {"title": "x"}

        self.assertIn("E_TRANSLATION_TARGET", codes(translated(mutate)))

    def test_a_question_uuid_must_exist(self):
        def mutate(block):
            block["questions"]["q-missing"] = {"text": "x"}

        self.assertIn("E_TRANSLATION_TARGET", codes(translated(mutate)))

    def test_a_subquestion_uuid_may_not_be_a_question_uuid(self):
        def mutate(block):
            block["subquestions"]["q-single"] = {"text": "x"}

        self.assertIn("E_TRANSLATION_TARGET", codes(translated(mutate)))

    def test_an_answer_must_name_an_existing_code_and_scale(self):
        def mutate(block):
            block["answers"].append({"question": "q-single", "code": "A9", "text": "x"})

        self.assertIn("E_TRANSLATION_TARGET", codes(translated(mutate)))

        def wrong_scale(block):
            block["answers"].append({"question": "q-single", "code": "A1", "scale": 1, "text": "x"})

        self.assertIn("E_TRANSLATION_TARGET", codes(translated(wrong_scale)))

    def test_the_same_answer_may_not_be_translated_twice_in_one_language(self):
        def mutate(block):
            block["answers"].append({"question": "q-single", "code": "A1", "text": "重复"})

        self.assertIn("E_TRANSLATION_DUPLICATE", codes(translated(mutate)))

    def test_a_text_must_be_a_string(self):
        self.assertIn("E_TRANSLATION_TYPE", codes(translated(lambda block: block.update({"title": 1}))))

    def test_the_answers_entry_must_be_an_object_with_the_required_keys(self):
        def mutate(block):
            block["answers"] = ["q-single"]

        self.assertIn("E_TRANSLATION_TYPE", codes(translated(mutate)))

        def missing_text(block):
            block["answers"] = [{"question": "q-single", "code": "A1"}]

        self.assertIn("E_TRANSLATION_TYPE", codes(translated(missing_text)))

    def test_groups_must_be_a_mapping_not_a_list(self):
        def mutate(block):
            block["groups"] = [{"uuid": "grp-1", "title": "x"}]

        self.assertIn("E_TRANSLATION_TYPE", codes(translated(mutate)))

    def test_a_definition_without_translations_reports_nothing(self):
        payload = copy.deepcopy(branded_definition(theme="fruity_twentythree"))
        self.assertEqual([], codes(payload))


if __name__ == "__main__":
    unittest.main()
