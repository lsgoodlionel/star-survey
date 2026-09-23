package cn.mjy.platform.survey.template;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.survey.SurveyFixture;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import cn.mjy.platform.survey.SurveyService;
import cn.mjy.platform.survey.SurveyStatus;
import cn.mjy.platform.survey.SurveyView;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * 企业模板库的闭环（R19-05）：从问卷建模板并剥掉生产数据、版本、审核、复制、下架。
 */
@SpringBootTest
class SurveyTemplateLifecycleTest {

    @Autowired
    private SurveyFixture fixture;

    @Autowired
    private SurveyService surveys;

    @Autowired
    private SurveyTemplateService templates;

    private Workspace ws;
    private TenantContext reviewer;
    private UUID surveyId;

    @BeforeEach
    void aWorkspaceWithASurvey() {
        ws = fixture.workspace();
        reviewer = fixture.member(ws, "sub-reviewer", "publish_reviewer", null);
        surveyId = fixture.newSurvey(ws).id();
    }

    private UUID publishedTemplate() {
        UUID id = templates.createFromSurvey(ws.owner(), surveyId, "标准满意度", "给新项目用").id();
        templates.submitForReview(ws.owner(), id);
        templates.approve(reviewer, id);
        return id;
    }

    // ------------------------------------------------------------ 生产数据剥离

    @Test
    void aTemplateCarriesNoParticipantsNoAccessPasswordAndNoAdminContacts() {
        ObjectNode production = fixture.definition();
        production.putArray("participants")
                .addObject().put("token", "tok-secret-1").put("firstname", "张三");
        ObjectNode policy = production.putObject("policy");
        policy.put("policyVersion", 1);
        policy.putObject("access").put("password", "Open-Sesame-7").put("captcha", true);
        ObjectNode settings = (ObjectNode) production.get("settings");
        settings.put("adminemail", "ops@tenant.example");
        settings.put("admin", "运维小组");
        settings.put("emailnotificationto", "ops@tenant.example");
        SurveyView survey = surveys.create(ws.owner(), ws.project(), production);
        // 草稿里确实有这些东西——否则下面的断言证明不了任何事。参与者是例外：它由发布时按问卷受众物化
        // （WP-18），客户端写进草稿的那一份在保存时就被丢掉，模板剥离因此是第二道防线而不是唯一一道。
        String draft = surveys.draft(ws.owner(), survey.id()).definition().toString();
        assertThat(draft).contains("passwordHash").contains("ops@tenant.example");
        assertThat(draft).as("草稿不收客户端自带的参与者").doesNotContain("tok-secret-1");

        UUID templateId = templates.createFromSurvey(ws.owner(), survey.id(), "带生产数据的问卷", null).id();
        JsonNode definition = templates.definition(ws.owner(), templateId, 1);
        String text = definition.toString();

        assertThat(definition.has("participants")).as("参与者 token").isFalse();
        assertThat(definition.has("policy")).as("访问策略（含口令哈希）").isFalse();
        assertThat(text).doesNotContain("tok-secret-1");
        assertThat(text).doesNotContain("passwordHash");
        assertThat(text).doesNotContain("Open-Sesame-7");
        assertThat(text).doesNotContain("ops@tenant.example");
        assertThat(text).doesNotContain("运维小组");
        assertThat(definition.get("settings").has("adminemail")).isFalse();
        assertThat(definition.get("settings").has("admin")).isFalse();
        assertThat(definition.get("settings").has("emailnotificationto")).isFalse();
        // 题目仍然完整：剥掉的只是生产数据。
        assertThat(definition.get("groups").size()).isPositive();
        assertThat(definition.get("title").asString()).isEqualTo(production.get("title").asString());
    }

    @Test
    void aTemplateDoesNotShareEntityIdsWithTheSurveyItCameFrom() {
        UUID templateId = templates.createFromSurvey(ws.owner(), surveyId, "标准满意度", null).id();

        assertThat(questionUuids(templates.definition(ws.owner(), templateId, 1)))
                .doesNotContainAnyElementsOf(
                        questionUuids(surveys.draft(ws.owner(), surveyId).definition()));
    }

    // ------------------------------------------------------------ 审核

    @Test
    void aNewTemplateIsNotVisibleToTheTenantUntilItIsApproved() {
        UUID id = templates.createFromSurvey(ws.owner(), surveyId, "待审模板", null).id();
        assertThat(templates.get(ws.owner(), id).template().status()).isEqualTo(TemplateStatus.DRAFT);
        assertThat(publishedIds(ws.owner())).doesNotContain(id);

        templates.submitForReview(ws.owner(), id);
        assertThat(templates.get(ws.owner(), id).template().status()).isEqualTo(TemplateStatus.PENDING);
        assertThat(publishedIds(ws.owner())).doesNotContain(id);

        templates.approve(reviewer, id);
        assertThat(templates.get(ws.owner(), id).template().status()).isEqualTo(TemplateStatus.PUBLISHED);
        assertThat(publishedIds(ws.owner())).contains(id);
    }

    @Test
    void anUnapprovedTemplateCannotBeCopied() {
        UUID id = templates.createFromSurvey(ws.owner(), surveyId, "待审模板", null).id();

        assertThatThrownBy(() -> templates.copyToSurvey(ws.owner(), id, ws.project(), "抄一份"))
                .isInstanceOf(TemplateConflictException.class)
                .hasMessageContaining("template_not_published");
    }

    @Test
    void aRejectionNeedsAReasonAndKeepsTheTemplateOutOfTheLibrary() {
        UUID id = templates.createFromSurvey(ws.owner(), surveyId, "待审模板", null).id();
        templates.submitForReview(ws.owner(), id);

        assertThatThrownBy(() -> templates.reject(reviewer, id, "  "))
                .isInstanceOf(InvalidTemplateRequestException.class);

        TemplateView rejected = templates.reject(reviewer, id, "题目顺序要改");
        assertThat(rejected.status()).isEqualTo(TemplateStatus.REJECTED);
        assertThat(publishedIds(ws.owner())).doesNotContain(id);
        assertThat(templates.get(ws.owner(), id).template().reviewReason()).isEqualTo("题目顺序要改");
    }

    @Test
    void anEditorWithoutTheReviewerRoleCannotApproveItsOwnTemplate() {
        TenantContext editor = fixture.member(ws, "sub-editor", "editor", ws.project());
        UUID id = templates.createFromSurvey(editor, surveyId, "编辑者的模板", null).id();
        templates.submitForReview(editor, id);

        assertThatThrownBy(() -> templates.approve(editor, id))
                .isInstanceOf(TemplateAccessDeniedException.class);
    }

    @Test
    void onlyOneReviewRoundIsOpenAtATime() {
        UUID id = templates.createFromSurvey(ws.owner(), surveyId, "待审模板", null).id();
        templates.submitForReview(ws.owner(), id);

        assertThatThrownBy(() -> templates.submitForReview(ws.owner(), id))
                .isInstanceOf(TemplateConflictException.class)
                .hasMessageContaining("template_not_submittable");
        assertThatThrownBy(() -> templates.approve(reviewer, UUID.randomUUID()))
                .isInstanceOf(TemplateNotFoundException.class);
    }

    // ------------------------------------------------------------ 复制的独立性

    @Test
    void aCopyIsAnIndependentDraftUnderTheChosenParent() {
        UUID templateId = publishedTemplate();

        SurveyView copy = templates.copyToSurvey(ws.owner(), templateId, ws.project(), "从模板新建");

        assertThat(copy.status()).isEqualTo(SurveyStatus.DRAFT);
        assertThat(copy.draftVersion()).isEqualTo(1);
        assertThat(copy.title()).isEqualTo("从模板新建");
        JsonNode definition = surveys.draft(ws.owner(), copy.id()).definition();
        assertThat(definition.get("uuid").asString()).isEqualTo(copy.id().toString());
        assertThat(definition.get("groups").size()).isPositive();
    }

    @Test
    void changingTheTemplateAfterwardsDoesNotChangeExistingCopies() {
        UUID templateId = publishedTemplate();
        SurveyView copy = templates.copyToSurvey(ws.owner(), templateId, ws.project(), "第一份");
        String before = surveys.draft(ws.owner(), copy.id()).definition().toString();

        UUID changedSource = surveys.create(ws.owner(), ws.project(),
                fixture.definitionTitled("模板第二版")).id();
        templates.addVersionFromSurvey(ws.owner(), templateId, changedSource);
        templates.submitForReview(ws.owner(), templateId);
        templates.approve(reviewer, templateId);

        assertThat(surveys.draft(ws.owner(), copy.id()).definition().toString()).isEqualTo(before);
        assertThat(templates.get(ws.owner(), templateId).template().publishedVersion()).isEqualTo(2);

        SurveyView second = templates.copyToSurvey(ws.owner(), templateId, ws.project(), "第二份");
        assertThat(surveys.draft(ws.owner(), second.id()).definition().get("title").asString())
                .isEqualTo("第二份");
        assertThat(questionUuids(surveys.draft(ws.owner(), second.id()).definition()))
                .doesNotContainAnyElementsOf(questionUuids(surveys.draft(ws.owner(), copy.id()).definition()));
    }

    @Test
    void twoCopiesOfTheSameVersionDoNotShareEntityIds() {
        UUID templateId = publishedTemplate();

        SurveyView first = templates.copyToSurvey(ws.owner(), templateId, ws.project(), "甲");
        SurveyView second = templates.copyToSurvey(ws.owner(), templateId, ws.project(), "乙");

        assertThat(questionUuids(surveys.draft(ws.owner(), first.id()).definition()))
                .doesNotContainAnyElementsOf(questionUuids(surveys.draft(ws.owner(), second.id()).definition()));
    }

    // ------------------------------------------------------------ 下架

    @Test
    void unpublishingHidesTheTemplateFromNewCopiesButExistingCopiesKeepWorking() {
        UUID templateId = publishedTemplate();
        SurveyView copy = templates.copyToSurvey(ws.owner(), templateId, ws.project(), "下架前抄的");

        TemplateView unpublished = templates.unpublish(reviewer, templateId);

        assertThat(unpublished.status()).isEqualTo(TemplateStatus.UNPUBLISHED);
        assertThat(unpublished.publishedVersion()).isNull();
        assertThat(publishedIds(ws.owner())).doesNotContain(templateId);
        assertThatThrownBy(() -> templates.copyToSurvey(ws.owner(), templateId, ws.project(), "下架后抄的"))
                .isInstanceOf(TemplateConflictException.class)
                .hasMessageContaining("template_not_published");

        // 已经抄出去的问卷完全不受影响：还能读、还能改。
        assertThat(surveys.get(ws.owner(), copy.id()).status()).isEqualTo(SurveyStatus.DRAFT);
        assertThat(surveys.saveDraft(ws.owner(), copy.id(), 1,
                fixture.definitionTitled("下架之后继续改")).version()).isEqualTo(2);
    }

    @Test
    void anUnpublishedTemplateGoesBackToTheLibraryAfterAFreshReview() {
        UUID templateId = publishedTemplate();
        templates.unpublish(reviewer, templateId);

        templates.submitForReview(ws.owner(), templateId);
        templates.approve(reviewer, templateId);

        assertThat(publishedIds(ws.owner())).contains(templateId);
    }

    // ------------------------------------------------------------ 历史与跨租户

    @Test
    void theTemplateHistoryRecordsEveryDecisionAndEveryCopy() {
        UUID templateId = publishedTemplate();
        templates.copyToSurvey(ws.owner(), templateId, ws.project(), "抄一份");
        templates.unpublish(reviewer, templateId);

        List<String> events = templates.get(ws.owner(), templateId).history().stream()
                .map(TemplateEventView::event).toList();
        assertThat(events).containsExactly("created", "submitted", "approved", "copied", "unpublished");
    }

    @Test
    void anotherTenantCannotSeeOrTouchTheTemplate() {
        UUID templateId = publishedTemplate();
        Workspace other = fixture.workspace();

        assertThatThrownBy(() -> templates.get(other.owner(), templateId))
                .isInstanceOf(TemplateNotFoundException.class);
        assertThatThrownBy(() -> templates.definition(other.owner(), templateId, 1))
                .isInstanceOf(TemplateNotFoundException.class);
        assertThatThrownBy(() -> templates.copyToSurvey(other.owner(), templateId, other.project(), "偷一份"))
                .isInstanceOf(TemplateNotFoundException.class);
        assertThatThrownBy(() -> templates.unpublish(other.owner(), templateId))
                .isInstanceOf(TemplateNotFoundException.class);
        assertThat(publishedIds(other.owner())).doesNotContain(templateId);
    }

    @Test
    void aTemplateCannotBeBuiltFromAnotherTenantsSurvey() {
        Workspace other = fixture.workspace();

        assertThatThrownBy(() -> templates.createFromSurvey(other.owner(), surveyId, "偷一份", null))
                .isInstanceOf(TemplateNotFoundException.class);
    }

    // ------------------------------------------------------------ 零件

    private List<UUID> publishedIds(TenantContext ctx) {
        return templates.list(ctx, TemplateScope.TENANT).stream().map(TemplateView::id).toList();
    }

    private static List<String> questionUuids(JsonNode definition) {
        List<String> uuids = new ArrayList<>();
        for (JsonNode group : definition.get("groups")) {
            for (JsonNode question : group.get("questions")) {
                uuids.add(question.get("uuid").asString());
            }
        }
        return List.copyOf(uuids);
    }
}
