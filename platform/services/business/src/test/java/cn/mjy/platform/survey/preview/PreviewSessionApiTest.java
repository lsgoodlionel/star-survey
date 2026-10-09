package cn.mjy.platform.survey.preview;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.engine.EngineEvent;
import cn.mjy.platform.engine.EngineEventType;
import cn.mjy.platform.engine.ResponseProjectionRepository;
import cn.mjy.platform.support.TestTokens;
import cn.mjy.platform.survey.SurveyFixture;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import cn.mjy.platform.survey.SurveyService;
import cn.mjy.platform.survey.SurveyView;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.context.annotation.Primary;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.web.servlet.MockMvc;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@SpringBootTest
@AutoConfigureMockMvc
@Import(PreviewSessionApiTest.GatewayConfig.class)
class PreviewSessionApiTest {

    @Autowired MockMvc mvc;
    @Autowired SurveyFixture fixture;
    @Autowired SurveyService surveys;
    @Autowired TestTokens tokens;
    @Autowired JsonMapper json;
    @Autowired FakePreviewGateway gateway;
    @Autowired TenantScope tenantScope;
    @Autowired JdbcClient jdbc;
    @Autowired PreviewSessionService previewService;
    @Autowired ResponseProjectionRepository projections;

    private Workspace ws;
    private SurveyView survey;

    @BeforeEach
    void setUp() {
        gateway.reset();
        ws = fixture.workspace();
        survey = fixture.newSurvey(ws);
    }

    @Test
    void createIsIdempotentAndPinsTheDraftVersion() throws Exception {
        UUID requestId = UUID.randomUUID();
        String first = create(ws.owner(), survey.id(), requestId).andExpect(status().isCreated())
                .andExpect(jsonPath("$.status").value("ready"))
                .andExpect(jsonPath("$.draftVersion").value(1))
                .andExpect(jsonPath("$.generation").value(org.hamcrest.Matchers.startsWith("preview-")))
                .andExpect(jsonPath("$.previewUrl").value(org.hamcrest.Matchers.containsString("preview=")))
                .andReturn().getResponse().getContentAsString();
        String second = create(ws.owner(), survey.id(), requestId).andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();

        ObjectNode changed = fixture.definitionTitled("changed after preview");
        surveys.saveDraft(ws.owner(), survey.id(), 1, changed);

        assertThat(json.readTree(second).get("id")).isEqualTo(json.readTree(first).get("id"));
        assertThat(gateway.creates).hasSize(1);
        mvc.perform(get("/v1/preview-sessions/" + json.readTree(first).get("id").asString())
                        .header("Authorization", bearer(ws.owner())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.draftVersion").value(1));
    }

    @Test
    void previewNeverCreatesAPublishedVersionOrResponseProjection() throws Exception {
        String body = create(ws.owner(), survey.id(), UUID.randomUUID()).andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();
        int sid = json.readTree(body).get("engineSid").intValue();
        String generation = json.readTree(body).get("generation").asString();

        tenantScope.run(ws.tenant(), () -> assertThat(projections.advance(ws.tenant(), new EngineEvent(
                UUID.randomUUID(), EngineEventType.SAVED, ws.engineInstanceId(), sid, generation, 1,
                "preview-test", Instant.now()))).isPresent());

        tenantScope.run(ws.tenant(), () -> {
            Integer versions = jdbc.sql("SELECT count(*) FROM survey_published_version WHERE survey_id = :survey")
                    .param("survey", survey.id()).query(Integer.class).single();
            Integer responses = jdbc.sql("SELECT count(*) FROM response_projection WHERE survey_id = :sid")
                    .param("sid", sid).query(Integer.class).single();
            assertThat(versions).isZero();
            assertThat(responses).isZero();
        });
    }

    @Test
    void anotherTenantCannotReadOrCloseTheSession() throws Exception {
        String body = create(ws.owner(), survey.id(), UUID.randomUUID()).andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();
        String id = json.readTree(body).get("id").asString();
        Workspace other = fixture.workspace();

        mvc.perform(get("/v1/preview-sessions/" + id).header("Authorization", bearer(other.owner())))
                .andExpect(status().isNotFound());
        mvc.perform(delete("/v1/preview-sessions/" + id).header("Authorization", bearer(other.owner())))
                .andExpect(status().isNotFound());
    }

    @Test
    void failedCleanupRemainsVisibleAndCanBeRetried() throws Exception {
        String body = create(ws.owner(), survey.id(), UUID.randomUUID()).andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();
        String id = json.readTree(body).get("id").asString();
        gateway.failClose = true;

        mvc.perform(delete("/v1/preview-sessions/" + id).header("Authorization", bearer(ws.owner())))
                .andExpect(status().isBadGateway())
                .andExpect(jsonPath("$.status").value("cleanup_failed"))
                .andExpect(jsonPath("$.cleanupAttempts").value(1));
        gateway.failClose = false;
        mvc.perform(delete("/v1/preview-sessions/" + id).header("Authorization", bearer(ws.owner())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("closed"))
                .andExpect(jsonPath("$.cleanupAttempts").value(2));
    }

    @Test
    void gatewayFailureIsRecordedAsFailed() throws Exception {
        gateway.failCreate = true;

        create(ws.owner(), survey.id(), UUID.randomUUID())
                .andExpect(status().isBadGateway())
                .andExpect(jsonPath("$.status").value("failed"))
                .andExpect(jsonPath("$.engineSid").value(org.hamcrest.Matchers.nullValue()));
    }

    @Test
    void expiredReadySessionIsClosedByTenantCleanup() throws Exception {
        String body = create(ws.owner(), survey.id(), UUID.randomUUID()).andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();
        UUID id = UUID.fromString(json.readTree(body).get("id").asString());
        tenantScope.run(ws.tenant(), () -> jdbc.sql(
                "UPDATE survey_preview_session SET expires_at = now() - interval '1 second' WHERE id = :id")
                .param("id", id).update());

        previewService.cleanupExpired(ws.tenant());

        mvc.perform(get("/v1/preview-sessions/" + id).header("Authorization", bearer(ws.owner())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("closed"))
                .andExpect(jsonPath("$.cleanupAttempts").value(1));
    }

    private org.springframework.test.web.servlet.ResultActions create(
            TenantContext ctx, UUID surveyId, UUID requestId) throws Exception {
        return mvc.perform(post("/v1/surveys/" + surveyId + "/preview-sessions")
                .header("Authorization", bearer(ctx)).contentType(MediaType.APPLICATION_JSON)
                .content(json.createObjectNode().put("requestId", requestId.toString())
                        .put("ttlSeconds", 1800).toString()));
    }

    private String bearer(TenantContext ctx) {
        return "Bearer " + tokens.issue(ctx.actorId(), ctx.tenantId().value(), List.of());
    }

    @TestConfiguration(proxyBeanMethods = false)
    static class GatewayConfig {
        @Bean @Primary FakePreviewGateway fakePreviewGateway() { return new FakePreviewGateway(); }
    }

    static final class FakePreviewGateway implements PreviewGatewayClient {
        final List<CreateRequest> creates = new CopyOnWriteArrayList<>();
        final AtomicInteger sid = new AtomicInteger(880000);
        volatile boolean failClose;
        volatile boolean failCreate;

        void reset() { creates.clear(); failClose = false; failCreate = false; }

        @Override public boolean isConfigured() { return true; }

        @Override public CreateOutcome create(CreateRequest request) {
            creates.add(request);
            if (failCreate) return new CreateOutcome.Failed("engine_error");
            int value = sid.incrementAndGet();
            return new CreateOutcome.Ready(value, request.engineInstanceId(), request.generation(),
                    request.expiresAt(), "https://engine.invalid/index.php/" + value + "?preview=signed");
        }

        @Override public CloseOutcome close(CloseRequest request) {
            return failClose ? new CloseOutcome.Failed("engine_error") : new CloseOutcome.Closed();
        }
    }
}
