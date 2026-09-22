"""WP-02 题型：列形状（fieldmap 该有哪些行）与发布前校验（422 的错误码与路径）。"""

import unittest

from pubgw.model import DefinitionError, SurveyDefinition
from pubgw.qtypes import DEFAULT_THEMES, SUPPORTED_TYPES, expected_rows, shape_of
from pubgw.validate import validate_definition

from .qtype_fixtures import answer, definition_with, first_question, fixture_definition, payload_with, question, sub

NEW_TYPES = ("O", "5", "Y", "G", "H", "A", "B", "C", "E", "K", "Q", ":", ";", "R", "|")


def issues(*questions, version=1):
    return [(issue.code, issue.path) for issue in validate_definition(definition_with(*questions, version=version)).issues]


def issue_codes(*questions, version=1):
    return [code for code, _ in issues(*questions, version=version)]


class ShapeTableTest(unittest.TestCase):
    def test_every_batch_type_is_supported_and_has_a_core_theme(self):
        for qtype in NEW_TYPES:
            with self.subTest(qtype=qtype):
                self.assertIn(qtype, SUPPORTED_TYPES)
                self.assertTrue(DEFAULT_THEMES[qtype])

    def test_language_switch_is_deliberately_not_supported(self):
        self.assertIsNone(shape_of("I"))

    def test_fixed_option_types_declare_the_engine_codes(self):
        self.assertEqual(("1", "2", "3", "4", "5"), shape_of("5").fixed_options)
        self.assertEqual(10, len(shape_of("B").fixed_options))
        self.assertEqual(("Y", "U", "N"), shape_of("C").fixed_options)
        self.assertEqual(("I", "S", "D"), shape_of("E").fixed_options)


class ExpectedRowsTest(unittest.TestCase):
    def rows(self, **payload):
        return expected_rows(first_question(question(**payload)))

    def test_single_column_types(self):
        for qtype in ("5", "Y", "G"):
            with self.subTest(qtype=qtype):
                self.assertEqual((("Q1", "", 0),), self.rows(qtype=qtype))

    def test_list_with_comment_adds_a_comment_column(self):
        rows = self.rows(qtype="O", answers=[answer("A1")])
        self.assertEqual((("Q1", "", 0), ("Q1", "comment", 0)), rows)

    def test_file_upload_adds_a_file_count_column(self):
        rows = self.rows(qtype="|", attributes={"allowed_filetypes": "pdf"})
        self.assertEqual((("Q1", "", 0), ("Q1", "filecount", 0)), rows)

    def test_arrays_and_multi_inputs_have_one_column_per_subquestion(self):
        for qtype in ("A", "B", "C", "E", "K", "Q"):
            with self.subTest(qtype=qtype):
                rows = self.rows(qtype=qtype, subquestions=[sub("R1"), sub("R2")])
                self.assertEqual((("Q1", "R1", 0), ("Q1", "R2", 0)), rows)

    def test_array_by_column_uses_subquestions_and_answers(self):
        rows = self.rows(qtype="H", subquestions=[sub("R1")], answers=[answer("A1")])
        self.assertEqual((("Q1", "R1", 0),), rows)

    def test_matrix_types_produce_row_by_column_cells(self):
        for qtype in (":", ";"):
            with self.subTest(qtype=qtype):
                rows = self.rows(
                    qtype=qtype,
                    subquestions=[sub("R1"), sub("C1", scale=1), sub("R2"), sub("C2", scale=1)],
                )
                self.assertEqual(
                    (("Q1", "R1_C1", 0), ("Q1", "R1_C2", 0), ("Q1", "R2_C1", 0), ("Q1", "R2_C2", 0)), rows
                )

    def test_ranking_has_a_json_column_and_one_virtual_row_per_rank(self):
        rows = self.rows(qtype="R", subquestions=[sub("I1"), sub("I2"), sub("I3")])
        self.assertEqual((("Q1", "", 0), ("Q1", "1", 0), ("Q1", "2", 0), ("Q1", "3", 0)), rows)

    def test_ranking_rank_rows_are_capped_by_max_subquestions(self):
        rows = self.rows(
            qtype="R", subquestions=[sub("I1"), sub("I2"), sub("I3")], attributes={"max_subquestions": "2"}
        )
        self.assertEqual((("Q1", "", 0), ("Q1", "1", 0), ("Q1", "2", 0)), rows)

    def test_existing_types_keep_their_rows(self):
        rows = self.rows(qtype="P", other=True, subquestions=[sub("S1")])
        self.assertEqual((("Q1", "S1", 0), ("Q1", "S1comment", 0), ("Q1", "other", 0), ("Q1", "othercomment", 0)), rows)


class ModelTest(unittest.TestCase):
    def test_parses_the_extension_keys(self):
        parsed = first_question(
            question("S", format="cn_mobile", maxLength=11),
        )
        self.assertEqual("cn_mobile", parsed.format)
        self.assertEqual(11, parsed.max_length)

    def test_parses_exclusive_subquestions(self):
        parsed = first_question(question("M", subquestions=[sub("S1"), sub("SN", exclusive=True)]))
        self.assertEqual((False, True), tuple(item.exclusive for item in parsed.subquestions))

    def test_extension_keys_default_to_off(self):
        parsed = first_question(question("S"))
        self.assertEqual(("", None), (parsed.format, parsed.max_length))

    def test_rejects_non_integer_max_length(self):
        for value in ("10", 1.5, True):
            with self.subTest(value=value), self.assertRaises(DefinitionError):
                definition_with(question("S", maxLength=value))

    def test_rejects_non_boolean_exclusive(self):
        with self.assertRaises(DefinitionError):
            definition_with(question("M", subquestions=[sub("S1", exclusive="yes")]))

    def test_extension_keys_are_allowed_in_both_definition_versions(self):
        for version in (1, 2):
            with self.subTest(version=version):
                SurveyDefinition.from_dict(payload_with(question("S", format="email"), version=version))


class ValidFixtureTest(unittest.TestCase):
    def test_the_all_types_fixture_validates(self):
        report = validate_definition(fixture_definition())
        self.assertTrue(report.is_valid, report.to_dict())

    def test_the_fixture_covers_every_batch_type(self):
        types = {item.type for item in fixture_definition().questions()}
        self.assertTrue(set(NEW_TYPES) <= types, set(NEW_TYPES) - types)


class StructureValidationTest(unittest.TestCase):
    def test_matrix_needs_column_subquestions(self):
        self.assertIn(
            ("E_MISSING_SUBQUESTION_SCALE", "groups[0].questions[0].subquestions"),
            issues(question(":", subquestions=[sub("R1")])),
        )

    def test_matrix_needs_row_subquestions(self):
        self.assertIn("E_MISSING_SUBQUESTION_SCALE", issue_codes(question(";", subquestions=[sub("C1", scale=1)])))

    def test_matrix_rejects_a_third_scale(self):
        self.assertIn(
            ("E_SUBQUESTION_SCALE_INVALID", "groups[0].questions[0].subquestions[2].scale"),
            issues(question(":", subquestions=[sub("R1"), sub("C1", scale=1), sub("X1", scale=2)])),
        )

    def test_new_per_subquestion_types_reject_scaled_subquestions(self):
        self.assertIn(
            ("E_SUBQUESTION_SCALE_INVALID", "groups[0].questions[0].subquestions[1].scale"),
            issues(question("K", subquestions=[sub("R1"), sub("R2", scale=1)])),
        )

    def test_fixed_option_types_take_no_answers(self):
        for qtype in ("5", "Y", "G"):
            with self.subTest(qtype=qtype):
                self.assertIn("E_UNEXPECTED_ANSWERS", issue_codes(question(qtype, answers=[answer("A1")])))

    def test_ranking_items_are_subquestions_not_answers(self):
        codes = issue_codes(question("R", answers=[answer("A1")]))
        self.assertIn("E_UNEXPECTED_ANSWERS", codes)
        self.assertIn("E_MISSING_SUBQUESTIONS", codes)

    def test_ranking_needs_two_items(self):
        self.assertIn("E_RANKING_TOO_FEW_ITEMS", issue_codes(question("R", subquestions=[sub("I1")])))

    def test_array_by_column_needs_answers(self):
        self.assertIn("E_MISSING_ANSWERS", issue_codes(question("H", subquestions=[sub("R1")])))

    def test_other_is_only_for_types_that_have_it(self):
        self.assertIn("E_UNEXPECTED_OTHER", issue_codes(question("O", other=True, answers=[answer("A1")])))

    def test_ranking_subquestion_codes_follow_the_engine_rules(self):
        self.assertIn(
            "E_SUBQUESTION_CODE_INVALID", issue_codes(question("R", subquestions=[sub("I_1"), sub("I2")]))
        )


class ExtensionKeyValidationTest(unittest.TestCase):
    def test_unknown_format(self):
        self.assertIn(
            ("E_FORMAT_UNKNOWN", "groups[0].questions[0].format"), issues(question("S", format="cn_passport"))
        )

    def test_format_only_on_short_text(self):
        self.assertIn(
            ("E_FORMAT_UNSUPPORTED_TYPE", "groups[0].questions[0].format"), issues(question("N", format="cn_mobile"))
        )

    def test_max_length_range(self):
        for value in (0, 16001):
            with self.subTest(value=value):
                self.assertIn(
                    ("E_MAX_LENGTH_RANGE", "groups[0].questions[0].maxLength"), issues(question("T", maxLength=value))
                )

    def test_max_length_only_on_free_text(self):
        self.assertIn("E_MAX_LENGTH_UNSUPPORTED_TYPE", issue_codes(question("N", maxLength=5)))

    def test_max_length_conflicts_with_a_raw_maximum_chars(self):
        self.assertIn(
            ("E_MAX_LENGTH_CONFLICT", "groups[0].questions[0].attributes.maximum_chars"),
            issues(question("S", maxLength=5, attributes={"maximum_chars": "9"})),
        )

    def test_exclusive_only_on_multiple_choice(self):
        self.assertIn(
            ("E_EXCLUSIVE_UNSUPPORTED_TYPE", "groups[0].questions[0].subquestions[0].exclusive"),
            issues(question("F", subquestions=[sub("R1", exclusive=True)], answers=[answer("A1")])),
        )

    def test_exclusive_conflicts_with_a_raw_exclude_all_others(self):
        self.assertIn(
            ("E_EXCLUSIVE_CONFLICT", "groups[0].questions[0].attributes.exclude_all_others"),
            issues(question("M", subquestions=[sub("S1"), sub("SN", exclusive=True)],
                            attributes={"exclude_all_others": "SN"})),
        )


class AttributeValidationTest(unittest.TestCase):
    def assert_issue(self, code, attribute, payload):
        self.assertIn((code, "groups[0].questions[0].attributes." + attribute), issues(payload))

    def test_min_and_max_answers_must_be_non_negative_integers(self):
        self.assert_issue("E_ATTRIBUTE_VALUE", "max_answers",
                          question("M", subquestions=[sub("S1")], attributes={"max_answers": "two"}))

    def test_min_answers_cannot_exceed_max_answers(self):
        self.assert_issue("E_ATTRIBUTE_RANGE", "min_answers",
                          question("M", subquestions=[sub("S1"), sub("S2"), sub("S3")],
                                   attributes={"min_answers": "3", "max_answers": "2"}))

    def test_max_answers_cannot_exceed_the_options(self):
        self.assert_issue("E_ATTRIBUTE_RANGE", "max_answers",
                          question("M", subquestions=[sub("S1"), sub("S2")], attributes={"max_answers": "3"}))

    def test_other_counts_as_an_option(self):
        self.assertEqual([], issues(question("M", other=True, subquestions=[sub("S1"), sub("S2")],
                                             attributes={"max_answers": "3"})))

    def test_numeric_bounds_must_be_numbers_in_order(self):
        self.assert_issue("E_ATTRIBUTE_VALUE", "min_num_value_n",
                          question("N", attributes={"min_num_value_n": "abc"}))
        self.assert_issue("E_ATTRIBUTE_RANGE", "min_num_value_n",
                          question("N", attributes={"min_num_value_n": "10", "max_num_value_n": "1"}))

    def test_equals_num_value_is_a_number(self):
        self.assert_issue("E_ATTRIBUTE_VALUE", "equals_num_value",
                          question("K", subquestions=[sub("P1")], attributes={"equals_num_value": "QX + 1"}))

    def test_int_only_flag(self):
        self.assert_issue("E_ATTRIBUTE_VALUE", "num_value_int_only",
                          question("N", attributes={"num_value_int_only": "Y"}))

    def test_date_bounds_are_literal_dates_in_order(self):
        self.assert_issue("E_ATTRIBUTE_VALUE", "date_min", question("D", attributes={"date_min": "2020/01/01"}))
        self.assert_issue("E_ATTRIBUTE_VALUE", "date_max", question("D", attributes={"date_max": "2021-02-30"}))
        self.assert_issue("E_ATTRIBUTE_RANGE", "date_min",
                          question("D", attributes={"date_min": "2030-01-01", "date_max": "2020-01-01"}))

    def test_maximum_chars_range(self):
        self.assert_issue("E_ATTRIBUTE_RANGE", "maximum_chars", question("S", attributes={"maximum_chars": "0"}))

    def test_ranking_limit_is_within_the_items(self):
        self.assert_issue("E_ATTRIBUTE_RANGE", "max_subquestions",
                          question("R", subquestions=[sub("I1"), sub("I2")], attributes={"max_subquestions": "3"}))

    def test_upload_rejects_executable_extensions(self):
        self.assert_issue("E_UPLOAD_FILETYPE_FORBIDDEN", "allowed_filetypes",
                          question("|", attributes={"allowed_filetypes": "png, PHP, jpg"}))

    def test_upload_extensions_are_plain_tokens(self):
        self.assert_issue("E_ATTRIBUTE_VALUE", "allowed_filetypes",
                          question("|", attributes={"allowed_filetypes": "png, ../x"}))

    def test_upload_must_list_its_extensions(self):
        self.assert_issue("E_UPLOAD_FILETYPE_REQUIRED", "allowed_filetypes", question("|"))
        self.assert_issue("E_UPLOAD_FILETYPE_REQUIRED", "allowed_filetypes",
                          question("|", attributes={"allowed_filetypes": " "}))

    def test_upload_limits(self):
        pdf = {"allowed_filetypes": "pdf"}
        self.assert_issue("E_ATTRIBUTE_RANGE", "max_filesize", question("|", attributes=dict(pdf, max_filesize="0")))
        self.assert_issue("E_ATTRIBUTE_RANGE", "max_num_of_files",
                          question("|", attributes=dict(pdf, max_num_of_files="11")))
        self.assert_issue("E_ATTRIBUTE_RANGE", "min_num_of_files",
                          question("|", attributes=dict(pdf, min_num_of_files="3", max_num_of_files="2")))

    def test_star_rating_mode(self):
        self.assert_issue("E_ATTRIBUTE_VALUE", "slider_rating", question("5", attributes={"slider_rating": "9"}))

    def test_raw_exclude_all_others_must_name_subquestions(self):
        self.assert_issue("E_ATTRIBUTE_VALUE", "exclude_all_others",
                          question("M", subquestions=[sub("S1")], attributes={"exclude_all_others": "S9"}))

    def test_unknown_attributes_still_pass_through(self):
        self.assertEqual([], issues(question("S", attributes={"text_input_width": "6"})))

    def test_errors_are_collected_not_short_circuited(self):
        codes = issue_codes(
            question("N", attributes={"min_num_value_n": "x", "num_value_int_only": "2"}),
        )
        self.assertEqual(2, codes.count("E_ATTRIBUTE_VALUE"))


class LogicReferenceTest(unittest.TestCase):
    """v2 条件引用新题型：能引用的编译成引擎变量，不能引用的发布前报错。"""

    QUESTIONS = (
        question("A", code="QA5", subquestions=[sub("R1")]),
        question("K", code="QALLOC", subquestions=[sub("P1")]),
        question("Y", code="QYES"),
        question("O", code="QCOM", answers=[answer("A1")]),
        question("R", code="QRANK", subquestions=[sub("I1"), sub("I2")]),
    )

    def compile(self, source):
        from pubgw.logic.lower import LogicCompiler

        definition = definition_with(*self.QUESTIONS, question("S", code="QEND"), version=2)
        return LogicCompiler(definition).expression(source, "condition", "q-QEND")

    def condition_issues(self, source):
        return issue_codes(*self.QUESTIONS, question("S", code="QEND", condition=source), version=2)

    def test_referenceable_new_types(self):
        self.assertEqual('(QA5_R1.NAOK == "5")', self.compile('QA5.R1 == "5"'))
        self.assertEqual("(QALLOC_P1.NAOK > 10)", self.compile("QALLOC.P1 > 10"))
        self.assertEqual('(QYES.NAOK == "Y")', self.compile('QYES == "Y"'))
        self.assertEqual('(QCOM.NAOK == "A1")', self.compile('QCOM == "A1"'))

    def test_fixed_domains_are_checked(self):
        self.assertIn("E_EXPR_UNKNOWN_ANSWER_CODE", self.condition_issues('QYES == "Q"'))
        self.assertIn("E_EXPR_UNKNOWN_ANSWER_CODE", self.condition_issues('QA5.R1 == "6"'))

    def test_rows_are_required(self):
        self.assertIn("E_EXPR_MEMBER_REQUIRED", self.condition_issues("QALLOC > 1"))

    def test_ranking_is_not_referenceable_yet(self):
        self.assertIn("E_EXPR_NOT_A_VALUE", self.condition_issues("answered(QRANK)"))


if __name__ == "__main__":
    unittest.main()
