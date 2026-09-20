"""发布编排：每个阶段失败都必须回滚，绝不留下半发布的问卷。"""

import unittest

from pubgw.compiler import LssCompiler
from pubgw.publish import STAGES, Publisher
from pubgw.rpc import RemoteControlClient

from .fakes import FakeEngine
from .fixtures import sample_definition, sample_payload
from pubgw.model import SurveyDefinition


def publisher_for(engine, compiler=None):
    client = RemoteControlClient(engine.transport)
    client.login("admin", "password")
    return Publisher(client, engine_instance="survey-test-web", compiler=compiler)


class TamperingCompiler(LssCompiler):
    """编译期正常、产出被改坏：用来证明回读校验是最后一道闸门。"""

    def __init__(self, replacements):
        self._replacements = replacements

    def compile(self, definition):
        compiled = LssCompiler().compile(definition)
        lss = compiled.lss
        for old, new in self._replacements.items():
            lss = lss.replace("[CDATA[{}]]".format(old), "[CDATA[{}]]".format(new))
        return type(compiled)(
            lss=lss,
            compiler_version=compiled.compiler_version,
            signature=compiled.signature,
            fingerprint=compiled.fingerprint,
            definition_uuid=compiled.definition_uuid,
        )


class HappyPathTest(unittest.TestCase):
    def setUp(self):
        self.definition = sample_definition()
        self.engine = FakeEngine(self.definition)
        self.result = publisher_for(self.engine).publish(self.definition)

    def test_publish_succeeds(self):
        self.assertTrue(self.result.ok, self.result.failures)

    def test_runs_every_stage_in_order(self):
        self.assertEqual(list(STAGES), [step.stage for step in self.result.steps])

    def test_imports_then_activates_then_reads_the_fieldmap_back(self):
        methods = self.engine.methods()

        self.assertLess(methods.index("import_survey"), methods.index("activate_survey"))
        self.assertLess(methods.index("activate_survey"), len(methods) - 1)
        self.assertIn("get_fieldmap", methods)

    def test_checks_the_codes_before_activating(self):
        """代码被改名时不应该已经建好了答卷表。"""
        methods = self.engine.methods()

        self.assertLess(methods.index("list_questions"), methods.index("activate_survey"))

    def test_nothing_is_rolled_back(self):
        self.assertFalse(self.result.rolled_back)
        self.assertEqual([], self.engine.deleted)

    def test_emits_a_binding_record(self):
        record = self.result.binding

        self.assertEqual("survey-test-web", record.engine_instance)
        self.assertEqual(self.result.survey_id, record.survey_id)
        self.assertEqual(LssCompiler.version, record.compiler_version)
        self.assertTrue(record.fingerprint.startswith("fm1:"))
        self.assertEqual("def-0001", record.definition_uuid)

    def test_the_binding_record_maps_every_uuid_to_a_code_and_field_names(self):
        by_uuid = {item.uuid: item for item in self.result.binding.questions}

        self.assertEqual({"q-single", "q-text", "q-multi", "q-dual"}, set(by_uuid))
        self.assertEqual("QDUAL", by_uuid["q-dual"].code)
        self.assertTrue(all(field.fieldname for field in by_uuid["q-dual"].fields))


class ParticipantTest(unittest.TestCase):
    def test_activates_the_participant_table_when_the_definition_has_a_list(self):
        payload = sample_payload()
        payload["participants"] = [{"email": "p0@example.invalid", "lastname": "P0"}]
        definition = SurveyDefinition.from_dict(payload)
        engine = FakeEngine(definition)

        result = publisher_for(engine).publish(definition)

        self.assertTrue(result.ok, result.failures)
        self.assertIn("activate_tokens", engine.methods())
        self.assertIn("add_participants", engine.methods())

    def test_skips_the_participant_table_when_there_is_no_list(self):
        definition = sample_definition()
        engine = FakeEngine(definition)

        publisher_for(engine).publish(definition)

        self.assertNotIn("activate_tokens", engine.methods())


class ValidationStopsBeforeTheEngineTest(unittest.TestCase):
    def test_an_invalid_definition_never_reaches_the_engine(self):
        payload = sample_payload()
        payload["groups"][0]["questions"][0]["answers"] = []
        definition = SurveyDefinition.from_dict(payload)
        engine = FakeEngine(definition)

        result = publisher_for(engine).publish(definition)

        self.assertFalse(result.ok)
        self.assertEqual("validate", result.failed_stage)
        self.assertEqual(["get_session_key"], engine.methods())
        self.assertIsNone(result.survey_id)

    def test_the_failure_names_the_offending_rule(self):
        payload = sample_payload()
        payload["groups"][0]["questions"][0]["answers"] = []
        definition = SurveyDefinition.from_dict(payload)

        result = publisher_for(FakeEngine(definition)).publish(definition)

        self.assertIn("E_MISSING_ANSWERS", " ".join(result.failures))


class RollbackTest(unittest.TestCase):
    def test_an_engine_rename_fails_verification_and_deletes_the_survey(self):
        definition = sample_definition()
        engine = FakeEngine(definition, rename={"QSINGLE": "r7q0"})

        result = publisher_for(engine).publish(definition)

        self.assertFalse(result.ok)
        self.assertTrue(result.rolled_back)
        self.assertEqual([result.survey_id], engine.deleted)

    def test_a_rename_is_caught_before_activation(self):
        definition = sample_definition()
        engine = FakeEngine(definition, rename={"QSINGLE": "r7q0"})

        result = publisher_for(engine).publish(definition)

        self.assertEqual("apply", result.failed_stage)
        self.assertNotIn("activate_survey", engine.methods())

    def test_a_tampered_compiler_output_is_still_caught_by_the_readback(self):
        definition = sample_definition()
        engine = FakeEngine(definition, rename={"QMULTI": "r3q1"})
        compiler = TamperingCompiler({"QMULTI": "Q-BAD"})

        result = publisher_for(engine, compiler).publish(definition)

        self.assertFalse(result.ok)
        self.assertTrue(result.rolled_back)
        self.assertIn("E_CODE_RENAMED", " ".join(result.failures))

    def test_a_failed_activation_rolls_back(self):
        definition = sample_definition()
        engine = FakeEngine(definition, fail_activate=True)

        result = publisher_for(engine).publish(definition)

        self.assertEqual("activate", result.failed_stage)
        self.assertTrue(result.rolled_back)
        self.assertEqual([result.survey_id], engine.deleted)

    def test_a_swapped_question_theme_rolls_back(self):
        """题型主题缺失时引擎静默降级，只有回读能发现。"""
        payload = sample_payload()
        payload["groups"][0]["questions"][0]["theme"] = "mjy-special"
        definition = SurveyDefinition.from_dict(payload)
        engine = FakeEngine(definition, theme_override={"QSINGLE": "listradio"})

        result = publisher_for(engine).publish(definition)

        self.assertEqual("apply", result.failed_stage)
        self.assertIn("E_THEME_CHANGED", " ".join(result.failures))
        self.assertTrue(result.rolled_back)

    def test_a_setting_that_came_back_inherited_rolls_back(self):
        definition = sample_definition()
        engine = FakeEngine(definition, inherited_settings={"datestamp": "I"})

        result = publisher_for(engine).publish(definition)

        self.assertEqual("apply", result.failed_stage)
        self.assertIn("E_SETTING_INHERITED_AFTER_IMPORT", " ".join(result.failures))
        self.assertTrue(result.rolled_back)

    def test_a_setting_the_engine_dropped_is_re_applied_instead_of_failing(self):
        definition = sample_definition()
        engine = FakeEngine(definition, inherited_settings={"allowprev": "X"})

        result = publisher_for(engine).publish(definition)

        self.assertTrue(result.ok, result.failures)
        self.assertIn("allowprev", engine.applied_settings[result.survey_id])

    def test_a_missing_column_rolls_back(self):
        definition = sample_definition()
        engine = FakeEngine(definition, drop_fields=(("QMULTI", "SQ002", 0),))

        result = publisher_for(engine).publish(definition)

        self.assertFalse(result.ok)
        self.assertTrue(result.rolled_back)
        self.assertIn("E_FIELD_MISSING", " ".join(result.failures))

    def test_a_participant_the_engine_refused_rolls_back(self):
        payload = sample_payload()
        payload["participants"] = [{"email": "not-an-email"}]
        definition = SurveyDefinition.from_dict(payload)
        engine = FakeEngine(definition, participant_errors=True)

        result = publisher_for(engine).publish(definition)

        self.assertEqual("activate", result.failed_stage)
        self.assertTrue(result.rolled_back)

    def test_an_import_result_that_is_not_a_survey_id_rolls_nothing_back_but_reports(self):
        definition = sample_definition()
        engine = FakeEngine(definition, import_result={"unexpected": "shape"})

        result = publisher_for(engine).publish(definition)

        self.assertEqual("import", result.failed_stage)
        self.assertIsNone(result.survey_id)

    def test_a_rollback_that_itself_fails_is_reported_loudly(self):
        definition = sample_definition()
        engine = FakeEngine(definition, fail_activate=True, fail_delete=True)

        result = publisher_for(engine).publish(definition)

        self.assertFalse(result.ok)
        self.assertFalse(result.rolled_back)
        self.assertIn("ROLLBACK FAILED", " ".join(result.failures))
        self.assertEqual(result.survey_id, result.orphan_survey_id)


class ResultShapeTest(unittest.TestCase):
    def test_the_result_serialises_to_json_friendly_data(self):
        definition = sample_definition()
        payload = publisher_for(FakeEngine(definition)).publish(definition).to_dict()

        self.assertTrue(payload["ok"])
        self.assertIn("binding", payload)
        self.assertIn("steps", payload)


if __name__ == "__main__":
    unittest.main()
