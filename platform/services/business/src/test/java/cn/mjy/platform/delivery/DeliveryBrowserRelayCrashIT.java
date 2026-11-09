package cn.mjy.platform.delivery;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doAnswer;

import cn.mjy.platform.delivery.DeliveryTaskService.NewRecipient;
import cn.mjy.platform.delivery.DeliveryTaskService.NewTask;
import cn.mjy.platform.engine.EngineEventType;
import cn.mjy.platform.engine.ResponseProjectionQuery.Position;
import cn.mjy.platform.response.HttpResponseAnswerSource;
import cn.mjy.platform.response.ResponseFixture;
import cn.mjy.platform.response.ResponseFixture.Published;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.shared.TenantId;
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
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.context.annotation.Primary;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoSpyBean;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** Explicit isolated fixture; a browser and real cron relay create the projection; platform publication binding is synthetic. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.DEFINED_PORT, properties = {
        "server.port=8080", "PLATFORM_DELIVERY_SECRET=" + DeliveryIntegrationTest.MASTER_SECRET,
        "platform.delivery.public-base-url=https://survey.example", "platform.delivery.rate-window=PT1S",
        "platform.delivery.send-lease=PT10M", "PLATFORM_ENGINE_EVENTS_SECRET=synthetic-engine-master-secret-0123456789abcdef"
})
@Import(DeliveryBrowserRelayCrashIT.RealResolverConfiguration.class)
@TestPropertySource(properties = {
        "platform.delivery.enabled=false", "platform.delivery.email.enabled=false",
        "platform.pubgw.url=http://fault-proxy:8080",
        "platform.pubgw.secret=synthetic-gateway-secret-0123456789abcdef"
})
@EnabledIfEnvironmentVariable(named = "SURVEY_BROWSER_RELAY_FIXTURE", matches = "/fixture/evidence.json")
class DeliveryBrowserRelayCrashIT {
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
    @MockitoSpyBean FakeEmailProvider email;
    @Autowired TenantScope tenantScope;
    @Autowired JdbcClient jdbc;
    @Autowired JsonMapper json;

    @Test
    void browserCompletionRelaysOnceAndSuppressesReminderAfterReadOutage() throws Exception {
        if ("second".equals(System.getenv("SURVEY_CRASH_PHASE"))) {
            verifyAfterRestart();
            return;
        }
        assertThat(System.getenv("SURVEY_CRASH_PHASE")).isEqualTo("first");
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
        Files.writeString(Path.of("/fixture/ready"), p.tenant().toString());
        Path completed = Path.of("/fixture/browser-relay-complete");
        long deadline = System.nanoTime() + Duration.ofMinutes(3).toNanos();
        while (!Files.exists(completed) && System.nanoTime() < deadline) Thread.sleep(200);
        assertThat(Files.exists(completed)).as("browser and actual relay fixture finished").isTrue();
        String generation = tenantScope.call(p.tenant(), () -> jdbc.sql("""
                SELECT generation FROM response_projection
                WHERE engine_instance_id = 'isolated-engine' AND survey_id = :sid
                  AND response_id = 1 AND state = 'engine_completed'
                """).param("sid", sid).query(String.class).single());
        long inboxCount = tenantScope.call(p.tenant(), () -> jdbc.sql("""
                SELECT count(*) FROM engine_event_inbox WHERE engine_instance_id = 'isolated-engine'
                  AND survey_id = :sid AND response_id = 1
                """).param("sid", sid).query(Long.class).single());
        assertThat(inboxCount).isPositive();

        // The local fault proxy rejects the first real gateway read with 503.
        worker.runOnce();
        assertThat(tenantScope.call(p.tenant(), () -> cursors.find(survey))).isEmpty();
        assertThat(fixture.reminderTasks(p.tenant(), invite.id())).isEmpty();
        assertThat(email.delivered()).isEmpty();
        assertThat(completions.isCompleted(p.owner(), survey, completedToken)).isFalse();

        // Capture synthetic provider acceptance, then wait for the host to kill this container.
        Files.writeString(Path.of("/fixture/crash-identity"), p.tenant() + "\n" + survey + "\n" + invite.id() + "\n");
        doAnswer(call -> {
            DeliveryMessage message = call.getArgument(0);
            assertThat(message.address()).isEqualTo(pendingAddress);
            Files.writeString(Path.of("/fixture/captured-key"), message.idempotencyKey());
            Files.writeString(Path.of("/fixture/provider-accepted"), "accepted");
            Thread.sleep(180_000);
            throw new AssertionError("fixture controller did not kill the process");
        }).when(email).send(any());
        worker.runOnce();
        throw new AssertionError("fixture process unexpectedly survived");
    }

    private void verifyAfterRestart() throws Exception {
        var ids = Files.readAllLines(Path.of("/fixture/crash-identity"));
        TenantId tenant = TenantId.of(ids.get(0));
        UUID survey = UUID.fromString(ids.get(1));
        UUID invite = UUID.fromString(ids.get(2));
        String key = Files.readString(Path.of("/fixture/captured-key"));
        List<UUID> pending = fixture.reminderTasks(tenant, invite);
        assertThat(pending).hasSize(1);
        UUID task = pending.getFirst();
        assertThat(tenantScope.call(tenant, () -> jdbc.sql("SELECT state FROM delivery_recipient WHERE task_id=:task")
                .param("task", task).query(String.class).single())).isEqualTo("sending");
        assertThat(tenantScope.call(tenant, () -> jdbc.sql("SELECT count(*) FROM response_projection WHERE state='engine_completed'")
                .query(Long.class).single())).isEqualTo(1);
        assertThat(tenantScope.call(tenant, () -> cursors.find(survey))).isPresent();
        doAnswer(call -> {
            DeliveryMessage message = call.getArgument(0);
            assertThat(message.address()).isEqualTo("pending@example.invalid");
            assertThat(message.idempotencyKey()).isEqualTo(key);
            Files.writeString(Path.of("/fixture/retry"), "same-key");
            return SendResult.accepted("synthetic-acceptance");
        }).when(email).send(any());
        assertThat(worker.sendPending(tenant, task, 1)).isZero();
        assertThat(Files.exists(Path.of("/fixture/retry"))).isFalse();
        tenantScope.run(tenant, () -> jdbc.sql("UPDATE delivery_recipient SET updated_at=now()-interval '11 minutes' WHERE task_id=:task")
                .param("task", task).update());
        assertThat(worker.sendPending(tenant, task, 1)).isEqualTo(1);
        assertThat(Files.readString(Path.of("/fixture/retry"))).isEqualTo("same-key");
        assertThat(tenantScope.call(tenant, () -> jdbc.sql("SELECT state FROM delivery_recipient WHERE task_id=:task")
                .param("task", task).query(String.class).single())).isEqualTo("sent");
        assertThat(tenantScope.call(tenant, () -> jdbc.sql("SELECT count(*) FROM delivery_send WHERE task_id=:task")
                .param("task", task).query(Long.class).single())).isEqualTo(1);
        assertThat(tenantScope.call(tenant, () -> jdbc.sql("SELECT count(*) FROM audit_log WHERE action='delivery.reminder.send'")
                .query(Long.class).single())).isEqualTo(1);
    }

    @TestConfiguration(proxyBeanMethods = false)
    static class RealResolverConfiguration {
        @Bean @Primary
        RespondentIdentityResolver actualGatewayResolver(HttpResponseAnswerSource source) {
            return new GatewayRespondentIdentityResolver(source);
        }
    }
}
