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
import cn.mjy.platform.engine.EngineEventInbox;
import cn.mjy.platform.engine.ResponseProjectionRepository;
import cn.mjy.platform.support.TestTokens;
import cn.mjy.platform.survey.SurveyFixture;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import cn.mjy.platform.survey.SurveyService;
import cn.mjy.platform.survey.SurveyView;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.stream.IntStream;
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
    @Autowired EngineEventInbox inbox;

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
                .andExpect(jsonPath("$.previewUrl").value(org.hamcrest.Matchers.containsString("/v1/preview/access?")))
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

        tenantScope.run(ws.tenant(), () -> assertThat(inbox.record(ws.tenant(), new EngineEvent(
                UUID.randomUUID(), EngineEventType.SAVED, ws.engineInstanceId(), sid, generation, 1,
                "preview-test", Instant.now()))).isFalse());

        tenantScope.run(ws.tenant(), () -> {
            Integer versions = jdbc.sql("SELECT count(*) FROM survey_published_version WHERE survey_id = :survey")
                    .param("survey", survey.id()).query(Integer.class).single();
            Integer responses = jdbc.sql("SELECT count(*) FROM response_projection WHERE survey_id = :sid")
                    .param("sid", sid).query(Integer.class).single();
            Integer outbox = jdbc.sql("SELECT count(*) FROM engine_outbox WHERE survey_id = :sid")
                    .param("sid", sid).query(Integer.class).single();
            Integer received = jdbc.sql("SELECT count(*) FROM engine_event_inbox WHERE survey_id = :sid")
                    .param("sid", sid).query(Integer.class).single();
            assertThat(versions).isZero();
            assertThat(responses).isZero();
            assertThat(outbox).isZero();
            assertThat(received).isZero();
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
    void unknownGatewayResultStaysCreatingAndRetryReconcilesTheSameSid() throws Exception {
        gateway.failCreate = true;
        UUID request = UUID.randomUUID();

        create(ws.owner(), survey.id(), request)
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("creating"))
                .andExpect(jsonPath("$.engineSid").value(org.hamcrest.Matchers.nullValue()));
        gateway.failCreate = false;
        create(ws.owner(), survey.id(), request)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("ready"));
        assertThat(gateway.creates).hasSize(2);
    }

    @Test
    void staleCreatingSessionIsReconciledBySweeper() throws Exception {
        gateway.failCreate = true;
        String body = create(ws.owner(), survey.id(), UUID.randomUUID()).andExpect(status().isAccepted())
                .andReturn().getResponse().getContentAsString();
        UUID id = UUID.fromString(json.readTree(body).get("id").asString());
        gateway.failCreate = false;
        tenantScope.run(ws.tenant(), () -> jdbc.sql(
                "UPDATE survey_preview_session SET updated_at = now() - interval '31 seconds' WHERE id = :id")
                .param("id", id).update());

        previewService.cleanupExpired(ws.tenant());

        mvc.perform(get("/v1/preview-sessions/" + id).header("Authorization", bearer(ws.owner())))
                .andExpect(status().isOk()).andExpect(jsonPath("$.status").value("ready"));
    }

    @Test
    void concurrentDeleteHasOneGatewayOwner() throws Exception {
        String body = create(ws.owner(), survey.id(), UUID.randomUUID()).andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();
        String id = json.readTree(body).get("id").asString();
        gateway.blockClose = true;
        var pool = Executors.newFixedThreadPool(2);
        try {
            var first = pool.submit(() -> mvc.perform(delete("/v1/preview-sessions/" + id)
                    .header("Authorization", bearer(ws.owner()))).andReturn().getResponse().getStatus());
            assertThat(gateway.closeEntered.await(5, TimeUnit.SECONDS)).isTrue();
            var second = pool.submit(() -> mvc.perform(delete("/v1/preview-sessions/" + id)
                    .header("Authorization", bearer(ws.owner()))).andReturn().getResponse().getStatus());
            assertThat(second.get(5, TimeUnit.SECONDS)).isEqualTo(202);
            gateway.closeRelease.countDown();
            assertThat(first.get(5, TimeUnit.SECONDS)).isEqualTo(200);
            assertThat(gateway.closeCalls.get()).isEqualTo(1);
        } finally {
            pool.shutdownNow();
        }
    }

    @Test
    void concurrentCreateFollowersReturnTheSameReadySession() throws Exception {
        UUID request = UUID.randomUUID();
        gateway.blockPrepare = true;
        var pool = Executors.newFixedThreadPool(6);
        try {
            var first = pool.submit(() -> createResponse(request));
            assertThat(gateway.prepareEntered.await(5, TimeUnit.SECONDS)).isTrue();
            var followers = IntStream.range(0, 4).mapToObj(ignored -> pool.submit(() -> createResponse(request))).toList();
            Thread.sleep(150);
            gateway.prepareRelease.countDown();
            var results = new java.util.ArrayList<String>();
            results.add(first.get(5, TimeUnit.SECONDS));
            for (var follower : followers) results.add(follower.get(5, TimeUnit.SECONDS));

            assertThat(results).allSatisfy(raw -> {
                try { assertThat(json.readTree(raw).get("status").asString()).isEqualTo("ready"); }
                catch (Exception e) { throw new AssertionError(e); }
            });
            assertThat(results.stream().map(raw -> json.readTree(raw).get("id").asString()).distinct()).hasSize(1);
            assertThat(results.stream().map(raw -> json.readTree(raw).get("engineSid").intValue()).distinct()).hasSize(1);
        } finally {
            pool.shutdownNow();
        }
    }

    @Test
    void closeLeaseOutlivesTheOneHundredTwentySecondGatewayTimeout() throws Exception {
        String body = create(ws.owner(), survey.id(), UUID.randomUUID()).andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();
        UUID id = UUID.fromString(json.readTree(body).get("id").asString());
        gateway.blockClose = true;
        var pool = Executors.newFixedThreadPool(2);
        try {
            var first = pool.submit(() -> mvc.perform(delete("/v1/preview-sessions/" + id)
                    .header("Authorization", bearer(ws.owner()))).andReturn().getResponse().getStatus());
            assertThat(gateway.closeEntered.await(5, TimeUnit.SECONDS)).isTrue();
            tenantScope.run(ws.tenant(), () -> {
                Integer remaining = jdbc.sql("""
                        SELECT floor(extract(epoch FROM (close_lease_until - now())))::integer
                          FROM survey_preview_session WHERE id = :id
                        """).param("id", id).query(Integer.class).single();
                assertThat(remaining).isGreaterThan(120);
                jdbc.sql("UPDATE survey_preview_session SET updated_at = now() - interval '120 seconds' WHERE id = :id")
                        .param("id", id).update();
            });

            int follower = mvc.perform(delete("/v1/preview-sessions/" + id)
                    .header("Authorization", bearer(ws.owner()))).andReturn().getResponse().getStatus();
            assertThat(follower).isEqualTo(202);
            assertThat(gateway.closeCalls.get()).isEqualTo(1);
            gateway.closeRelease.countDown();
            assertThat(first.get(5, TimeUnit.SECONDS)).isEqualTo(200);
        } finally {
            pool.shutdownNow();
        }
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

    private String createResponse(UUID request) throws Exception {
        var response = create(ws.owner(), survey.id(), request).andReturn().getResponse();
        assertThat(response.getStatus()).isIn(200, 201);
        return response.getContentAsString();
    }

    @TestConfiguration(proxyBeanMethods = false)
    static class GatewayConfig {
        @Bean @Primary FakePreviewGateway fakePreviewGateway() { return new FakePreviewGateway(); }
    }

    static final class FakePreviewGateway implements PreviewGatewayClient {
        final List<CreateRequest> creates = new CopyOnWriteArrayList<>();
        final AtomicInteger sid = new AtomicInteger(880000);
        final AtomicInteger closeCalls = new AtomicInteger();
        final AtomicInteger activateCalls = new AtomicInteger();
        final Map<UUID, CreateRequest> sessions = new ConcurrentHashMap<>();
        final Map<UUID, Integer> sessionSids = new ConcurrentHashMap<>();
        volatile boolean failClose;
        volatile boolean failCreate;
        volatile boolean blockClose;
        volatile boolean blockPrepare;
        volatile CountDownLatch prepareEntered = new CountDownLatch(1);
        volatile CountDownLatch prepareRelease = new CountDownLatch(1);
        volatile CountDownLatch closeEntered = new CountDownLatch(1);
        volatile CountDownLatch closeRelease = new CountDownLatch(1);

        void reset() { creates.clear(); sessions.clear(); sessionSids.clear(); closeCalls.set(0); failClose = false; failCreate = false;
            blockClose = false; blockPrepare = false; prepareEntered = new CountDownLatch(1);
            prepareRelease = new CountDownLatch(1); closeEntered = new CountDownLatch(1);
            closeRelease = new CountDownLatch(1); activateCalls.set(0); }

        @Override public boolean isConfigured() { return true; }

        @Override public CreateOutcome prepare(CreateRequest request) {
            creates.add(request);
            prepareEntered.countDown();
            if (blockPrepare) try { prepareRelease.await(5, TimeUnit.SECONDS); }
            catch (InterruptedException e) { Thread.currentThread().interrupt(); }
            if (failCreate) return new CreateOutcome.Unknown("network error");
            sessions.putIfAbsent(request.sessionId(), request);
            int value = sessionSids.computeIfAbsent(request.sessionId(), ignored -> sid.incrementAndGet());
            return new CreateOutcome.Prepared(value);
        }

        @Override public CreateOutcome activate(Identity request) {
            activateCalls.incrementAndGet();
            CreateRequest created = sessions.get(request.sessionId());
            int value = sessionSids.get(request.sessionId());
            return new CreateOutcome.Ready(value, created.engineInstanceId(), created.generation(),
                    created.expiresAt(), "https://gateway.invalid/v1/preview/access?sig=signed");
        }

        @Override public CloseOutcome close(CloseRequest request) {
            closeCalls.incrementAndGet();
            closeEntered.countDown();
            if (blockClose) try { closeRelease.await(5, TimeUnit.SECONDS); }
            catch (InterruptedException e) { Thread.currentThread().interrupt(); }
            return failClose ? new CloseOutcome.Failed("engine_error") : new CloseOutcome.Closed();
        }
    }
}
