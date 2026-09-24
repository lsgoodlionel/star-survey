package cn.mjy.platform.survey;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.access.DecisionReason;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import cn.mjy.platform.survey.SurveyImportPreview.PreviewOption;
import cn.mjy.platform.survey.SurveyImportPreview.PreviewQuestion;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import tools.jackson.databind.JsonNode;

/**
 * 批量文本导入（WP-01 01.1，需求 R01-02）：先预览后落库。
 * 预览把原文切成题目、标出识别到的题型与选项，坏行只记问题不中断整批；
 * 确认时按预览里的序号取舍，题号、顺序与代码与预览逐项一致。
 */
@SpringBootTest
class SurveyTextImportTest {

    private static final String THREE_QUESTIONS = """
            1. 您的性别？[单选]
            A. 男
            B. 女

            2. 您平时使用哪些功能？【多选，必答】
            A. 搜索
            B. 收藏
            C. 分享

            3. 请写下您的建议 [多行文本]
            """;

    @Autowired
    private SurveyFixture fixture;

    @Autowired
    private SurveyService surveys;

    @Autowired
    private SurveyPublishService publisher;

    private Workspace ws;
    private UUID survey;

    @BeforeEach
    void aDraftSurvey() {
        ws = fixture.workspace();
        survey = fixture.newSurvey(ws).id();
    }

    private SurveyImportPreview preview(String text) {
        return surveys.previewImport(ws.owner(), survey, text);
    }

    private JsonNode draftQuestions() {
        return surveys.draft(ws.owner(), survey).definition().get("groups");
    }

    private List<String> draftQuestionCodes() {
        return draftQuestions().valueStream()
                .flatMap(group -> group.get("questions").valueStream())
                .map(question -> question.get("code").asString())
                .toList();
    }

    @Test
    void previewSplitsTheTextIntoQuestionsWithTheirRecognisedTypeOptionsAndLineNumbers() {
        SurveyImportPreview preview = preview(THREE_QUESTIONS);

        assertThat(preview.problems()).isEmpty();
        assertThat(preview.questions()).hasSize(3);
        PreviewQuestion first = preview.questions().get(0);
        assertThat(first.index()).isZero();
        assertThat(first.line()).isEqualTo(1);
        assertThat(first.text()).isEqualTo("您的性别？");
        assertThat(first.type()).isEqualTo("L");
        assertThat(first.typeName()).isEqualTo("单选");
        assertThat(first.typeInferred()).isFalse();
        assertThat(first.mandatory()).isFalse();
        assertThat(first.importable()).isTrue();
        assertThat(first.options()).extracting(PreviewOption::text).containsExactly("男", "女");
        assertThat(first.options()).extracting(PreviewOption::code).containsExactly("A1", "A2");

        PreviewQuestion second = preview.questions().get(1);
        assertThat(second.line()).isEqualTo(5);
        assertThat(second.type()).isEqualTo("M");
        assertThat(second.mandatory()).isTrue();
        assertThat(second.options()).hasSize(3);

        PreviewQuestion third = preview.questions().get(2);
        assertThat(third.type()).isEqualTo("T");
        assertThat(third.typeName()).isEqualTo("多行文本");
        assertThat(third.options()).isEmpty();
    }

    @Test
    void aTypeThatIsNotWrittenDownIsInferredAndSaidToBeInferred() {
        SurveyImportPreview preview = preview("""
                1. 没有写题型但有选项
                A. 甲
                B. 乙
                2. 没有写题型也没有选项
                """);

        assertThat(preview.questions()).extracting(PreviewQuestion::type).containsExactly("L", "S");
        assertThat(preview.questions()).extracting(PreviewQuestion::typeInferred).containsExactly(true, true);
    }

    @Test
    void aQuestionStemCanWrapOntoTheNextLines() {
        SurveyImportPreview preview = preview("""
                1. 第一行题干
                仍然是题干的第二行
                A. 甲
                B. 乙
                """);

        assertThat(preview.questions()).singleElement()
                .extracting(PreviewQuestion::text).isEqualTo("第一行题干\n仍然是题干的第二行");
    }

    @Test
    void aBadLineIsReportedWithItsLineNumberAndDoesNotStopTheRestOfTheBatch() {
        SurveyImportPreview preview = preview("""
                这一行在任何题目之前，认不出来
                1. 第一题[单选]
                A. 甲
                B. 乙
                2. 第二题[单选]
                A. 丙
                B. 丁
                """);

        assertThat(preview.problems()).singleElement().satisfies(problem -> {
            assertThat(problem.line()).isEqualTo(1);
            assertThat(problem.code()).isEqualTo("unrecognized_line");
            assertThat(problem.message()).isNotBlank();
        });
        assertThat(preview.questions()).extracting(PreviewQuestion::text).containsExactly("第一题", "第二题");
        assertThat(preview.questions()).allMatch(PreviewQuestion::importable);
    }

    @Test
    void questionsWithBlockingProblemsArePreviewedButMarkedNotImportable() {
        SurveyImportPreview preview = preview("""
                1. 题型写错了[拉杆箱]
                A. 甲
                2. 单选却没有选项[单选]
                3. 选项重了[多选]
                A. 甲
                B. 甲
                4. 好题[单选]
                A. 甲
                B. 乙
                """);

        assertThat(preview.questions()).hasSize(4);
        assertThat(preview.questions().get(0).importable()).isFalse();
        assertThat(preview.questions().get(0).problems()).extracting(ImportProblem::code)
                .contains("unknown_tag");
        assertThat(preview.questions().get(1).problems()).extracting(ImportProblem::code)
                .contains("too_few_options");
        assertThat(preview.questions().get(2).problems()).extracting(ImportProblem::code)
                .contains("duplicate_option");
        assertThat(preview.questions().get(3).importable()).isTrue();
    }

    @Test
    void aTextTypeThatCannotCarryOptionsSaysSoInsteadOfDroppingThemSilently() {
        SurveyImportPreview preview = preview("""
                1. 填空题不该带选项[填空]
                A. 甲
                B. 乙
                """);

        assertThat(preview.questions()).singleElement().satisfies(question -> {
            assertThat(question.importable()).isFalse();
            assertThat(question.options()).hasSize(2);
            assertThat(question.problems()).extracting(ImportProblem::code).contains("options_not_allowed");
        });
    }

    @Test
    void previewedCodesContinuePastTheCodesTheDraftAlreadyUses() {
        surveys.saveDraft(ws.owner(), survey, 1, fixture.definition());

        SurveyImportPreview first = preview(THREE_QUESTIONS);
        assertThat(first.questions()).extracting(PreviewQuestion::code).containsExactly("Q1", "Q2", "Q3");

        surveys.importQuestions(ws.owner(), survey, 2, THREE_QUESTIONS, List.of(0, 1, 2), null);

        assertThat(preview(THREE_QUESTIONS).questions()).extracting(PreviewQuestion::code)
                .containsExactly("Q4", "Q5", "Q6");
    }

    @Test
    void aPreviewWritesNothing() {
        preview(THREE_QUESTIONS);

        assertThat(surveys.draft(ws.owner(), survey).version()).isEqualTo(1);
        assertThat(draftQuestionCodes()).containsExactly("QSINGLE", "QTEXT", "QNOTE", "QMULTI", "QDUAL");
    }

    @Test
    void confirmingTheImportAppendsTheAcceptedQuestionsInPreviewOrderWithThePreviewedCodes() {
        SurveyImportPreview preview = preview(THREE_QUESTIONS);

        DraftView saved = surveys.importQuestions(ws.owner(), survey, 1, THREE_QUESTIONS, List.of(0, 1, 2), null);

        assertThat(saved.version()).isEqualTo(2);
        assertThat(draftQuestionCodes()).containsExactly("QSINGLE", "QTEXT", "QNOTE", "QMULTI", "QDUAL",
                "Q1", "Q2", "Q3");
        JsonNode imported = saved.definition().get("groups").get(2);
        assertThat(imported.get("questions").size()).isEqualTo(3);
        JsonNode first = imported.get("questions").get(0);
        assertThat(first.get("code").asString()).isEqualTo(preview.questions().get(0).code());
        assertThat(first.get("text").asString()).isEqualTo("您的性别？");
        assertThat(first.get("type").asString()).isEqualTo("L");
        assertThat(first.get("answers").get(0).get("text").asString()).isEqualTo("男");
        assertThat(imported.get("questions").get(1).get("mandatory").asBoolean()).isTrue();
        // 每道题拿到一个新的 UUID，互不相同。
        assertThat(draftQuestions().valueStream()
                .flatMap(group -> group.get("questions").valueStream())
                .map(question -> question.get("uuid").asString()).distinct().count()).isEqualTo(8);
    }

    @Test
    void onlyTheAcceptedQuestionsAreImported() {
        surveys.importQuestions(ws.owner(), survey, 1, THREE_QUESTIONS, List.of(2), null);

        assertThat(draftQuestionCodes()).containsExactly("QSINGLE", "QTEXT", "QNOTE", "QMULTI", "QDUAL", "Q3");
    }

    @Test
    void importedQuestionsCanBeImportedIntoAnExistingGroup() {
        String groupUuid = draftQuestions().get(0).get("uuid").asString();

        DraftView saved = surveys.importQuestions(ws.owner(), survey, 1, THREE_QUESTIONS, List.of(0), groupUuid);

        assertThat(saved.definition().get("groups").size()).isEqualTo(2);
        assertThat(saved.definition().get("groups").get(0).get("questions").size()).isEqualTo(4);
        assertThat(draftQuestionCodes()).containsExactly("QSINGLE", "QTEXT", "QNOTE", "Q1", "QMULTI", "QDUAL");
    }

    @Test
    void animportedDraftIsStillPublishable() {
        surveys.importQuestions(ws.owner(), survey, 1, THREE_QUESTIONS, List.of(0, 1, 2), null);

        PublishOutcome outcome = publisher.publish(ws.owner(), survey);

        assertThat(outcome.survey().status()).isEqualTo(SurveyStatus.PUBLISHED);
    }

    @Test
    void importingWithAStaleExpectedVersionIsAConflictAndWritesNothing() {
        surveys.saveDraft(ws.owner(), survey, 1, fixture.definitionTitled("另一个人先存了"));

        assertThatThrownBy(() -> surveys.importQuestions(ws.owner(), survey, 1, THREE_QUESTIONS, List.of(0), null))
                .isInstanceOfSatisfying(SurveyConflictException.class,
                        e -> assertThat(e.code()).isEqualTo("version_conflict"));
        assertThat(draftQuestionCodes()).containsExactly("QSINGLE", "QTEXT", "QNOTE", "QMULTI", "QDUAL");
    }

    @Test
    void aQuestionThePreviewRefusedCannotBeImported() {
        String text = "1. 单选却没有选项[单选]\n";

        assertThatThrownBy(() -> surveys.importQuestions(ws.owner(), survey, 1, text, List.of(0), null))
                .isInstanceOfSatisfying(InvalidImportException.class, e -> assertThat(e.problems())
                        .extracting(ImportProblem::code).contains("too_few_options"));
        assertThat(surveys.draft(ws.owner(), survey).version()).isEqualTo(1);
    }

    @Test
    void anIndexThatThePreviewNeverShowedIsRejected() {
        assertThatThrownBy(() -> surveys.importQuestions(ws.owner(), survey, 1, THREE_QUESTIONS, List.of(0, 9), null))
                .isInstanceOf(InvalidSurveyRequestException.class)
                .hasMessageContaining("9");
    }

    @Test
    void anEmptyAcceptListIsRejected() {
        assertThatThrownBy(() -> surveys.importQuestions(ws.owner(), survey, 1, THREE_QUESTIONS, List.of(), null))
                .isInstanceOf(InvalidSurveyRequestException.class);
    }

    @Test
    void anUnknownGroupIsRejected() {
        assertThatThrownBy(() -> surveys.importQuestions(ws.owner(), survey, 1, THREE_QUESTIONS, List.of(0),
                UUID.randomUUID().toString()))
                .isInstanceOf(InvalidSurveyRequestException.class);
    }

    @Test
    void blankTextIsRejected() {
        assertThatThrownBy(() -> preview("   \n\n  "))
                .isInstanceOf(InvalidSurveyRequestException.class);
        assertThatThrownBy(() -> preview(null))
                .isInstanceOf(InvalidSurveyRequestException.class);
    }

    @Test
    void textWithNoQuestionAtAllIsPreviewedAsProblemsOnly() {
        SurveyImportPreview preview = preview("随便写的一段话\n又一行\n");

        assertThat(preview.questions()).isEmpty();
        assertThat(preview.problems()).extracting(ImportProblem::line).containsExactly(1, 2);
    }

    @Test
    void absurdlyLargeInputIsRejectedAtTheBoundary() {
        String tooManyLines = "1. 题\n".repeat(6000);

        assertThatThrownBy(() -> preview(tooManyLines))
                .isInstanceOf(InvalidSurveyRequestException.class);
    }

    @Test
    void onlyTheBracketBlockAtTheEndOfTheStemIsReadAsTags() {
        SurveyImportPreview preview = preview("""
                1. 题干里也有[方括号][单选]
                A. 甲
                B. 乙
                """);

        assertThat(preview.questions()).singleElement().satisfies(question -> {
            assertThat(question.text()).isEqualTo("题干里也有[方括号]");
            assertThat(question.type()).isEqualTo("L");
            assertThat(question.typeInferred()).isFalse();
            assertThat(question.problems()).isEmpty();
        });
    }

    @Test
    void anAbsurdlyLongSingleLineIsReportedInsteadOfBeingParsed() {
        // 极长的行会让"结尾方括号"这类回溯型正则退化成平方复杂度，先按长度挡掉。
        String bomb = "[".repeat(200_000);

        SurveyImportPreview preview = preview("1. 正常题[填空]\n" + bomb + "\n");

        assertThat(preview.questions()).hasSize(1);
        assertThat(preview.problems()).singleElement().satisfies(problem -> {
            assertThat(problem.line()).isEqualTo(2);
            assertThat(problem.code()).isEqualTo("line_too_long");
        });
    }

    @Test
    void importingNeedsEditRightsOnTheSurvey() {
        TenantContext viewer = fixture.member(ws, "stats", "statistics_viewer", survey);

        assertThatThrownBy(() -> surveys.previewImport(viewer, survey, THREE_QUESTIONS))
                .isInstanceOfSatisfying(SurveyAccessDeniedException.class,
                        e -> assertThat(e.reason()).isEqualTo(DecisionReason.NO_MATCHING_GRANT));
        assertThatThrownBy(() -> surveys.importQuestions(viewer, survey, 1, THREE_QUESTIONS, List.of(0), null))
                .isInstanceOf(SurveyAccessDeniedException.class);
    }
}
