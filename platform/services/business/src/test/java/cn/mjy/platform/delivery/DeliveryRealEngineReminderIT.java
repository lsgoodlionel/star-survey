package cn.mjy.platform.delivery;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.delivery.DeliveryTaskService.NewRecipient;
import cn.mjy.platform.delivery.DeliveryTaskService.NewTask;
import cn.mjy.platform.engine.EngineEventType;
import cn.mjy.platform.engine.ResponseProjectionQuery.Position;
import cn.mjy.platform.response.HttpResponseAnswerSource;
import cn.mjy.platform.response.ResponseFixture;
import cn.mjy.platform.response.ResponseFixture.Published;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.FakePublishGateway;
import cn.mjy.platform.survey.SurveyFixture;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import cn.mjy.platform.survey.SurveyPublishService;
import cn.mjy.platform.survey.SurveyService;
import cn.mjy.platform.survey.gateway.GatewayBinding;
import cn.mjy.platform.survey.gateway.GatewayOutcome;
import cn.mjy.platform.survey.gateway.GatewayResult;
import cn.mjy.platform.tenant.engine.EngineInstanceService;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.context.annotation.Primary;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.context.TestPropertySource;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** Explicit isolated fixture only; publication/projection are synthetic, respondent reads use the real HTTP gateway and engine. */
@DeliveryIntegrationTest
@Import(DeliveryRealEngineReminderIT.RealResolverConfiguration.class)
@TestPropertySource(properties = {
        "platform.delivery.enabled=false", "platform.delivery.email.enabled=false",
        "platform.pubgw.url=http://fault-proxy:8080",
        "platform.pubgw.secret=synthetic-gateway-secret-0123456789abcdef"
})
@EnabledIfEnvironmentVariable(named = "SURVEY_REAL_ENGINE_FIXTURE", matches = "/fixture/evidence.json")
class DeliveryRealEngineReminderIT {
    @Autowired SurveyFixture surveys;
    @Autowired SurveyService surveyService;
    @Autowired SurveyPublishService publisher;
    @Autowired EngineInstanceService engines;
    @Autowired FakePublishGateway publishFixture;
    @Autowired DeliveryFixture fixture;
    @Autowired DeliveryTaskService tasks;
    @Autowired DeliveryWorker worker;
    @Autowired ReminderService reminders;
    @Autowired CompletionService completions;
    @Autowired CompletionCursorRepository cursors;
    @Autowired DeliveryProviders providers;
    @Autowired FakeEmailProvider email;
    @Autowired TenantScope tenantScope;
    @Autowired JdbcClient jdbc;
    @Autowired JsonMapper json;

    @Test
    void outageStopsAllRemindersThenRealGatewayIdentifiesOnlyCompletedPerson() throws Exception {
        JsonNode data = json.readTree(Files.readString(Path.of("/fixture/evidence.json")));
        int sid = data.get("sid").asInt();
        String completedToken = data.get("completedToken").asString();
        String pendingToken = data.get("pendingToken").asString();
        assertThat(sid).isPositive();
        assertThat(completedToken).isNotBlank().isNotEqualTo(pendingToken);
        assertThat(providers.require(DeliveryChannel.EMAIL)).isSameAs(email);
        email.reset();
        Workspace bare = surveys.workspaceWithoutEngine();
        engines.register(bare.tenant(), "isolated-engine", "https://engine.example/fixture", "fixture", "trace");
        Workspace ws = new Workspace(bare.owner(), bare.project(), "isolated-engine");
        UUID survey = surveyService.create(ws.owner(), ws.project(), surveys.definition()).id();
        publishFixture.script(survey, request -> {
            GatewayResult old = ((GatewayOutcome.Published) publishFixture.success(request)).result();
            GatewayBinding b = old.binding();
            GatewayBinding realSidBinding = new GatewayBinding(b.engineInstance(), sid, b.definitionUuid(),
                    b.compilerVersion(), b.fingerprintVersion(), b.fingerprint(), b.language(), b.publishedAt(), b.questions());
            return new GatewayOutcome.Published(new GatewayResult(true, sid, null, List.of(), false, null,
                    realSidBinding, old.policyDigest(), old.invitations()));
        });
        Published p = new Published(ws, survey, publisher.publish(ws.owner(), survey).version());
        String doneAddress = "completed@example.invalid", pendingAddress = "pending@example.invalid";
        TaskView invite = tasks.create(p.owner(), new NewTask(survey, DeliveryChannel.EMAIL, null, "synthetic invite",
                DeliveryFixture.EMAIL_BODY, List.of(new NewRecipient(doneAddress, "Done", completedToken, Map.of()),
                        new NewRecipient(pendingAddress, "Pending", pendingToken, Map.of())), null));
        assertThat(fixture.run(p.tenant(), invite.id())).isEqualTo(2);
        email.reset();
        Duration delay = Duration.ofHours(24);
        reminders.create(p.owner(), survey, new ReminderService.NewRule(invite.id(), delay, delay, 3,
                "synthetic reminder", DeliveryFixture.REMINDER_BODY));
        fixture.ageSentAt(p.tenant(), invite.id(), delay.plusHours(1));
        fixture.responses().response(p.tenant(), p.instance(), p.sid(), ResponseFixture.GENERATION, 1L, EngineEventType.COMPLETED);

        // The local fault proxy rejects the first real gateway read with 503.
        worker.runOnce();
        assertThat(tenantScope.call(p.tenant(), () -> cursors.find(survey))).isEmpty();
        assertThat(fixture.reminderTasks(p.tenant(), invite.id())).isEmpty();
        assertThat(email.delivered()).isEmpty();
        assertThat(completions.isCompleted(p.owner(), survey, completedToken)).isFalse();

        // The next signed request crosses the proxy to the actual gateway and engine.
        worker.runOnce();
        assertThat(completions.isCompleted(p.owner(), survey, completedToken)).isTrue();
        assertThat(completions.isCompleted(p.owner(), survey, pendingToken)).isFalse();
        assertThat(tenantScope.call(p.tenant(), () -> cursors.find(survey)))
                .contains(new Position(ResponseFixture.GENERATION, 1L));
        assertThat(email.deliveredTo(doneAddress)).isEmpty();
        assertThat(email.deliveredTo(pendingAddress)).isPresent();
        assertThat(email.delivered()).hasSize(1);
        List<UUID> reminderTasks = fixture.reminderTasks(p.tenant(), invite.id());
        assertThat(reminderTasks).hasSize(1);
        assertThat(fixture.recipientViews(p.owner(), reminderTasks.getFirst())).singleElement()
                .satisfies(recipient -> {
                    assertThat(recipient.displayName()).isEqualTo("Pending");
                    assertThat(recipient.state()).isEqualTo("sent");
                });
        long auditCount = tenantScope.call(p.tenant(), () -> jdbc.sql("SELECT count(*) FROM audit_log WHERE action='delivery.reminder.send'")
                .query(Long.class).single());
        assertThat(auditCount).isEqualTo(1);
    }

    @TestConfiguration(proxyBeanMethods = false)
    static class RealResolverConfiguration {
        @Bean @Primary
        RespondentIdentityResolver actualGatewayResolver(HttpResponseAnswerSource source) {
            return new GatewayRespondentIdentityResolver(source);
        }
    }
}
