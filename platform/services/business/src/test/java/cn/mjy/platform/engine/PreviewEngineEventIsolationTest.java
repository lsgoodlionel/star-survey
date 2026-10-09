package cn.mjy.platform.engine;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.SurveyFixture;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

@SpringBootTest
class PreviewEngineEventIsolationTest {

    @Autowired SurveyFixture fixture;
    @Autowired TenantScope scope;
    @Autowired JdbcClient jdbc;
    @Autowired EngineEventApplier applier;

    @Test
    void registeredPreviewSidNeverReachesInboxProjectionOrOutboxWhileFormalEventsStillDo() {
        Workspace ws = fixture.workspace();
        var survey = fixture.newSurvey(ws);
        int previewSid = 981001;
        String previewGeneration = "preview-" + UUID.randomUUID().toString().replace("-", "");

        scope.run(ws.tenant(), () -> jdbc.sql("""
                INSERT INTO survey_preview_session
                    (tenant_id, id, request_id, survey_id, draft_version, definition, requested_by,
                     engine_instance_id, engine_sid, generation, expires_at, status)
                SELECT :tenant, :id, :request, id, draft_version, draft_definition, 'test',
                       :instance, :sid, :generation, :expires, 'ready'
                  FROM survey WHERE id = :survey
                """).param("tenant", ws.tenant().value()).param("id", UUID.randomUUID())
                .param("request", UUID.randomUUID()).param("instance", ws.engineInstanceId())
                .param("sid", previewSid).param("generation", previewGeneration)
                .param("expires", OffsetDateTime.now().plusMinutes(5)).param("survey", survey.id()).update());

        scope.run(ws.tenant(), () -> {
            int inboxBefore = count("engine_event_inbox");
            int projectionBefore = count("response_projection");
            int outboxBefore = count("engine_outbox");
            EngineEvent preview = event(ws, previewSid, previewGeneration, 1);

            assertThat(applier.applyAll(ws.tenant(), List.of(preview)).accepted()).isZero();
            assertThat(count("engine_event_inbox")).isEqualTo(inboxBefore);
            assertThat(count("response_projection")).isEqualTo(projectionBefore);
            assertThat(count("engine_outbox")).isEqualTo(outboxBefore);

            EngineEvent formal = event(ws, previewSid + 1, UUID.randomUUID().toString(), 2);
            assertThat(applier.applyAll(ws.tenant(), List.of(formal)).accepted()).isOne();
            assertThat(count("engine_event_inbox")).isEqualTo(inboxBefore + 1);
            assertThat(count("response_projection")).isEqualTo(projectionBefore + 1);
            assertThat(count("engine_outbox")).isEqualTo(outboxBefore + 1);
        });
    }

    private EngineEvent event(Workspace ws, int sid, String generation, long response) {
        return new EngineEvent(UUID.randomUUID(), EngineEventType.COMPLETED, ws.engineInstanceId(), sid,
                generation, response, "preview-isolation-test", Instant.now());
    }

    private int count(String table) {
        return jdbc.sql("SELECT count(*) FROM " + table).query(Integer.class).single();
    }
}
