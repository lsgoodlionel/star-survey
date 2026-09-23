package cn.mjy.platform.survey;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.contacts.ContactDirectoryService;
import cn.mjy.platform.contacts.ContactDraft;
import cn.mjy.platform.contacts.ContactKind;
import cn.mjy.platform.contacts.ContactParticipationService;
import cn.mjy.platform.contacts.ContactView;
import cn.mjy.platform.contacts.ContactsFixture;
import cn.mjy.platform.contacts.DedupeKey;
import cn.mjy.platform.contacts.ParticipationView;
import cn.mjy.platform.contacts.SurveyAudienceService;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import cn.mjy.platform.survey.gateway.GatewayOutcome;
import cn.mjy.platform.survey.gateway.GatewayRequest;
import cn.mjy.platform.tenant.routing.SurveyRouteService;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * 联系人名单 → 发布定义里的 {@code participants} → 回执邀请码 → 联系人映射（WP-18 / WP-05 接缝，ADR 0016）。
 *
 * <p>钉住的是整条链，尤其是它断掉时的样子：邀请码配不齐、回执里没有邀请码、回执指向不认识的人——
 * 这三种情况都不许留下"路由已登记、却没人能进门"的问卷。
 */
@SpringBootTest
class SurveyParticipantsPublishTest {

    private static final String INVITATION_POLICY = """
            {"policyVersion": 1, "access": {"invitationRequired": true}}""";

    @Autowired
    private SurveyFixture fixture;

    @Autowired
    private ContactsFixture contactsFixture;

    @Autowired
    private ContactDirectoryService contacts;

    @Autowired
    private SurveyAudienceService audiences;

    @Autowired
    private ContactParticipationService participations;

    @Autowired
    private SurveyPublishService publisher;

    @Autowired
    private SurveyService surveys;

    @Autowired
    private FakePublishGateway gateway;

    @Autowired
    private SurveyRouteService routes;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private Workspace ws;
    private UUID list;
    private ContactView ann;
    private ContactView bob;

    @BeforeEach
    void aTenantWithTwoContacts() {
        ws = fixture.workspace();
        list = contactsFixture.list(ws.owner(), "受众 " + UUID.randomUUID(),
                ContactKind.RESPONDENT, DedupeKey.EMAIL).id();
        ann = contacts.create(ws.owner(), list, ContactKind.RESPONDENT,
                ContactDraft.named("甲").withEmail("ann@example.com"));
        bob = contacts.create(ws.owner(), list, ContactKind.RESPONDENT,
                ContactDraft.named("乙").withEmail("bob@example.com"));
    }

    /** 一份需要邀请码、已选定受众的草稿问卷。 */
    private UUID invitationSurvey() {
        UUID survey = fixture.newSurveyWithPolicy(ws, INVITATION_POLICY).id();
        audiences.set(ws.owner(), survey, list, null, true, null);
        return survey;
    }

    private JsonNode sentDefinition(UUID survey) {
        return fixture.parse(gateway.callsFor(survey).getLast().definitionJson());
    }

    private long mappingRows(UUID survey) {
        return tenantScope.call(ws.tenant(), () -> jdbc
                .sql("SELECT COUNT(*) FROM contact_participation WHERE survey_id = :survey")
                .param("survey", survey).query(Long.class).single());
    }

    @Test
    void anInvitationSurveyCarriesOneRefOnlyParticipantPerContact() {
        UUID survey = invitationSurvey();

        publisher.publish(ws.owner(), survey);

        JsonNode participants = sentDefinition(survey).get("participants");
        assertThat(participants).isNotNull();
        List<String> refs = new ArrayList<>();
        for (JsonNode participant : participants) {
            // 个人信息一律不出引擎：条目上只有平台自选的引用（联系人 id），别的键一个都没有。
            assertThat(participant.propertyNames()).containsExactly("ref");
            refs.add(participant.get("ref").asString());
        }
        assertThat(refs).containsExactlyInAnyOrder(ann.id().toString(), bob.id().toString());
    }

    @Test
    void aSurveyThatDoesNotNeedInvitationCodesCarriesNoParticipants() {
        UUID survey = fixture.newSurvey(ws).id();
        audiences.set(ws.owner(), survey, list, null, true, null);

        publisher.publish(ws.owner(), survey);

        assertThat(sentDefinition(survey).has("participants")).isFalse();
        assertThat(mappingRows(survey)).isZero();
    }

    @Test
    void everyContactHoldsItsOwnInvitationCodeAfterPublishing() {
        UUID survey = invitationSurvey();

        publisher.publish(ws.owner(), survey);

        ParticipationView forAnn = participations.current(ws.owner(), ann.id(), survey).orElseThrow();
        ParticipationView forBob = participations.current(ws.owner(), bob.id(), survey).orElseThrow();
        assertThat(forAnn.participantToken()).isNotEqualTo(forBob.participantToken());
        assertThat(forAnn.versionNo()).isEqualTo(1);
        assertThat(forAnn.engineSid()).isEqualTo(forBob.engineSid()).isPositive();
    }

    @Test
    void anInvitationSurveyWithoutAnAudienceIsRefusedBeforeTheGatewayIsCalled() {
        UUID survey = fixture.newSurveyWithPolicy(ws, INVITATION_POLICY).id();

        assertThatThrownBy(() -> publisher.publish(ws.owner(), survey))
                .isInstanceOf(InvalidDefinitionException.class);

        assertThat(gateway.callsFor(survey)).isEmpty();
        assertThat(surveys.get(ws.owner(), survey).status()).isEqualTo(SurveyStatus.DRAFT);
    }

    @Test
    void whenTheEngineCannotIssueTheCodesTheSurveyIsNotPublishedAndNobodyIsMapped() {
        UUID survey = invitationSurvey();
        gateway.script(survey, request -> FakePublishGateway.engineFailed(0,
                "participant 1 came back without a token"));

        PublishOutcome outcome = publisher.publish(ws.owner(), survey);

        assertThat(outcome.survey().status()).isEqualTo(SurveyStatus.PUBLISH_FAILED);
        assertThat(routes.findByPublicId(ws.tenant(), survey)).isEmpty();
        assertThat(mappingRows(survey)).isZero();
    }

    @Test
    void aPublishedReceiptWithoutInvitationCodesIsNotAcceptedAsPublished() {
        UUID survey = invitationSurvey();
        gateway.script(survey, gateway::publishedWithoutInvitations);

        PublishOutcome outcome = publisher.publish(ws.owner(), survey);

        assertThat(outcome.survey().status()).isEqualTo(SurveyStatus.PUBLISH_FAILED);
        assertThat(routes.findByPublicId(ws.tenant(), survey)).isEmpty();
        assertThat(mappingRows(survey)).isZero();
    }

    /**
     * 认不出的 ref 是<b>确定失败</b>，不是结果未知：重试拿回的是同一份存档回执，判待核对会让这份问卷
     * 永远卡在那儿。与"回执缺邀请码""策略没落地"同一类。
     */
    @Test
    void anInvitationThatPointsAtNobodyWeKnowFailsThePublish() {
        UUID survey = invitationSurvey();
        List<String> strangers = List.of(UUID.randomUUID().toString(), UUID.randomUUID().toString());
        gateway.script(survey, request -> gateway.publishedWithInvitationRefs(request, strangers));

        PublishOutcome outcome = publisher.publish(ws.owner(), survey);

        assertThat(outcome.survey().status()).isEqualTo(SurveyStatus.PUBLISH_FAILED);
        assertThat(routes.findByPublicId(ws.tenant(), survey)).isEmpty();
        assertThat(mappingRows(survey)).isZero();
    }

    @Test
    void anInvitationWithoutAPlatformReferenceFailsThePublish() {
        UUID survey = invitationSurvey();
        List<String> blank = Arrays.asList(null, null);
        gateway.script(survey, request -> gateway.publishedWithInvitationRefs(request, blank));

        PublishOutcome outcome = publisher.publish(ws.owner(), survey);

        assertThat(outcome.survey().status()).isEqualTo(SurveyStatus.PUBLISH_FAILED);
        assertThat(mappingRows(survey)).isZero();
    }

    @Test
    void twoInvitationsForTheSameContactFailThePublish() {
        UUID survey = invitationSurvey();
        List<String> twice = List.of(ann.id().toString(), ann.id().toString());
        gateway.script(survey, request -> gateway.publishedWithInvitationRefs(request, twice));

        PublishOutcome outcome = publisher.publish(ws.owner(), survey);

        assertThat(outcome.survey().status()).isEqualTo(SurveyStatus.PUBLISH_FAILED);
        assertThat(routes.findByPublicId(ws.tenant(), survey)).isEmpty();
        assertThat(mappingRows(survey)).isZero();
    }

    @Test
    void twoContactsSharingOneInvitationCodeFailThePublish() {
        UUID survey = invitationSurvey();
        gateway.script(survey, request -> gateway.publishedWithOneCodeForEveryone(request,
                List.of(ann.id().toString(), bob.id().toString())));

        PublishOutcome outcome = publisher.publish(ws.owner(), survey);

        assertThat(outcome.survey().status()).isEqualTo(SurveyStatus.PUBLISH_FAILED);
        assertThat(mappingRows(survey)).isZero();
    }

    @Test
    void republishingIssuesAFreshCodeAndLeavesTheOldVersionsMappingBehind() {
        UUID survey = invitationSurvey();
        publisher.publish(ws.owner(), survey);
        String first = participations.current(ws.owner(), ann.id(), survey).orElseThrow().participantToken();
        ObjectNode second = fixture.definitionTitled("第二版");
        second.set("policy", fixture.parse(INVITATION_POLICY));
        surveys.saveDraft(ws.owner(), survey, surveys.draft(ws.owner(), survey).version(), second);

        PublishOutcome outcome = publisher.publish(ws.owner(), survey);

        assertThat(outcome.version().version()).isEqualTo(2);
        ParticipationView now = participations.current(ws.owner(), ann.id(), survey).orElseThrow();
        assertThat(now.versionNo()).isEqualTo(2);
        assertThat(now.participantToken()).isNotEqualTo(first);
        // 旧版的映射原样留着：读取一律按当前在线版本过滤，认错人是不可能的。
        assertThat(mappingRows(survey)).isEqualTo(4);
    }

    @Test
    void aDraftNeverKeepsParticipantsTheClientWroteItself() {
        ObjectNode handWritten = fixture.definitionTitled("自带参与者");
        handWritten.putArray("participants").addObject().put("ref", "谁都行").put("email", "sneaky@example.com");

        UUID survey = surveys.create(ws.owner(), ws.project(), handWritten).id();

        assertThat(surveys.draft(ws.owner(), survey).definition().has("participants")).isFalse();
    }

    @Test
    void restoringAnOldVersionDoesNotBringItsParticipantsBack() {
        UUID survey = invitationSurvey();
        publisher.publish(ws.owner(), survey);

        surveys.restore(ws.owner(), survey, 1, surveys.draft(ws.owner(), survey).version());

        assertThat(surveys.draft(ws.owner(), survey).definition().has("participants")).isFalse();
    }

    @Test
    void anUnknownGatewayOutcomeMapsNobodyUntilTheRetrySettlesIt() {
        UUID survey = invitationSurvey();
        gateway.timeoutAfterPublishing(survey);

        PublishOutcome pending = publisher.publish(ws.owner(), survey);
        assertThat(pending.survey().status()).isEqualTo(SurveyStatus.PENDING_RECONCILIATION);
        assertThat(mappingRows(survey)).isZero();

        gateway.script(survey, SurveyParticipantsPublishTest::neverScripted);
        PublishOutcome settled = publisher.publish(ws.owner(), survey);

        assertThat(settled.survey().status()).isEqualTo(SurveyStatus.PUBLISHED);
        assertThat(mappingRows(survey)).isEqualTo(2);
    }

    /** 核对走的是网关的幂等存档，脚本不会被再次执行。 */
    private static GatewayOutcome neverScripted(GatewayRequest request) {
        throw new IllegalStateException("the reconciliation must reuse the archived result");
    }
}
