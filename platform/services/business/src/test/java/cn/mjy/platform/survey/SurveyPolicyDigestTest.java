package cn.mjy.platform.survey;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.survey.SurveyFixture.Workspace;
import cn.mjy.platform.tenant.routing.SurveyRouteService;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * ADR 0016 的缺口：平台必须核对网关回执里的 {@code policyDigest}。
 * 旧网关会忽略不认识的顶层键，于是"带访问策略的定义"可能被发布成一份毫无防护的问卷——
 * 回执没有摘要、或摘要与平台自己算出来的不一致时，发布必须失败：不登记路由、不写已发布版本。
 */
@SpringBootTest
class SurveyPolicyDigestTest {

    private static final String PLUGIN_POLICY = """
            {"policyVersion":1,
             "window":{"opensAt":"2026-10-01T09:00","closesAt":"2026-10-31T18:00","timezone":"Asia/Shanghai"},
             "access":{"password":"s3cret-pass","captcha":true},
             "limits":{"responses":[{"by":"ip","max":3}],"maxDurationSeconds":1800},
             "network":{"denyIps":["10.0.0.0/8"],"regionUnknown":"deny"}}
            """;
    /** 只有验证码：引擎自己就能执行，网关不写插件载荷，也就没有摘要可回。 */
    private static final String NATIVE_ONLY_POLICY = """
            {"policyVersion":1,"access":{"captcha":true}}
            """;

    @Autowired
    private SurveyFixture fixture;

    @Autowired
    private SurveyPublishService publisher;

    @Autowired
    private SurveyService surveys;

    @Autowired
    private FakePublishGateway gateway;

    @Autowired
    private SurveyRouteService routes;

    private Workspace ws;

    @BeforeEach
    void aTenantWithAnEngine() {
        ws = fixture.workspace();
    }

    private void assertRefused(UUID survey, PublishOutcome outcome) {
        assertThat(outcome.survey().status()).isEqualTo(SurveyStatus.PUBLISH_FAILED);
        assertThat(outcome.version()).isNull();
        assertThat(surveys.versions(ws.owner(), survey)).isEmpty();
        assertThat(routes.findByPublicId(ws.tenant(), survey)).isEmpty();
    }

    @Test
    void aGatewayThatSilentlyDropsThePolicyCannotPublish() {
        UUID survey = fixture.newSurveyWithPolicy(ws, PLUGIN_POLICY).id();
        gateway.script(survey, gateway::publishedWithoutPolicyDigest);

        assertRefused(survey, publisher.publish(ws.owner(), survey));
        assertThat(surveys.get(ws.owner(), survey).lastPublish().failures())
                .anySatisfy(failure -> assertThat(failure).contains("policyDigest"));
    }

    @Test
    void aGatewayThatReportsADifferentPolicyCannotPublish() {
        UUID survey = fixture.newSurveyWithPolicy(ws, PLUGIN_POLICY).id();
        gateway.script(survey, request -> gateway.publishedWithPolicyDigest(request, "0".repeat(64)));

        assertRefused(survey, publisher.publish(ws.owner(), survey));
    }

    @Test
    void aMatchingDigestPublishesNormally() {
        UUID survey = fixture.newSurveyWithPolicy(ws, PLUGIN_POLICY).id();
        // 缺省的替身就是当前网关：按定义编译出摘要再回执。

        PublishOutcome outcome = publisher.publish(ws.owner(), survey);

        assertThat(outcome.survey().status()).isEqualTo(SurveyStatus.PUBLISHED);
        assertThat(routes.findByPublicId(ws.tenant(), survey)).isPresent();
    }

    @Test
    void aDefinitionWithoutAPolicyNeedsNoDigest() {
        UUID survey = fixture.newSurvey(ws).id();

        assertThat(publisher.publish(ws.owner(), survey).survey().status()).isEqualTo(SurveyStatus.PUBLISHED);
    }

    @Test
    void aPolicyTheEngineEnforcesOnItsOwnNeedsNoDigest() {
        UUID survey = fixture.newSurveyWithPolicy(ws, NATIVE_ONLY_POLICY).id();

        assertThat(publisher.publish(ws.owner(), survey).survey().status()).isEqualTo(SurveyStatus.PUBLISHED);
    }

    @Test
    void anUnexpectedDigestOnAPolicyFreeDefinitionIsRefused() {
        UUID survey = fixture.newSurvey(ws).id();
        gateway.script(survey, request -> gateway.publishedWithPolicyDigest(request, "a".repeat(64)));

        assertRefused(survey, publisher.publish(ws.owner(), survey));
    }

    @Test
    void anUnreadableDigestIsRefusedRatherThanTrusted() {
        UUID survey = fixture.newSurveyWithPolicy(ws, PLUGIN_POLICY).id();
        gateway.script(survey, request -> gateway.publishedWithPolicyDigest(request, "not-a-digest"));

        assertRefused(survey, publisher.publish(ws.owner(), survey));
    }

    @Test
    void theEngineSurveyLeftBehindIsRecordedAsAnOrphan() {
        UUID survey = fixture.newSurveyWithPolicy(ws, PLUGIN_POLICY).id();
        gateway.script(survey, gateway::publishedWithoutPolicyDigest);

        publisher.publish(ws.owner(), survey);

        assertThat(surveys.get(ws.owner(), survey).lastPublish().orphanEngineSid()).isNotNull();
    }
}
