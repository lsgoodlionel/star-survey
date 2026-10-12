package cn.mjy.platform.survey.preview;

import cn.mjy.platform.access.AccessDecisionService;
import cn.mjy.platform.access.Decision;
import cn.mjy.platform.access.DecisionReason;
import cn.mjy.platform.access.Permission;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.InvalidSurveyRequestException;
import cn.mjy.platform.survey.PublishUnavailableException;
import cn.mjy.platform.survey.SurveyAccessDeniedException;
import cn.mjy.platform.survey.SurveyConflictException;
import cn.mjy.platform.survey.SurveyNotFoundException;
import cn.mjy.platform.tenant.engine.EngineInstance;
import cn.mjy.platform.tenant.engine.EngineInstanceService;
import cn.mjy.platform.tenant.engine.EngineInstanceStatus;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.HttpTimeoutException;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@Service
@EnableScheduling
public class PreviewSessionService {

    static final int MIN_TTL_SECONDS = 60;
    static final int MAX_TTL_SECONDS = 3600;
    private static final Logger log = LoggerFactory.getLogger(PreviewSessionService.class);

    private final TenantScope tenantScope;
    private final AccessDecisionService access;
    private final PreviewSessionRepository sessions;
    private final EngineInstanceService engines;
    private final PreviewGatewayClient gateway;
    private final JdbcClient jdbc;
    private final Clock clock = Clock.systemUTC();

    PreviewSessionService(TenantScope tenantScope, AccessDecisionService access,
            PreviewSessionRepository sessions, EngineInstanceService engines, PreviewGatewayClient gateway,
            JdbcClient jdbc) {
        this.tenantScope = tenantScope;
        this.access = access;
        this.sessions = sessions;
        this.engines = engines;
        this.gateway = gateway;
        this.jdbc = jdbc;
    }

    public record Created(PreviewSessionView session, boolean fresh) {
    }

    private record Ticket(TenantId tenant, UUID id, UUID requestId, UUID surveyId, int draftVersion, String actor,
            String instance, String generation, OffsetDateTime expiresAt, String definition, boolean fresh) {
    }

    public Created create(TenantContext ctx, UUID surveyId, UUID requestId, int ttlSeconds) {
        if (ttlSeconds < MIN_TTL_SECONDS || ttlSeconds > MAX_TTL_SECONDS) {
            throw new InvalidSurveyRequestException("ttlSeconds must be between 60 and 3600");
        }
        if (!gateway.isConfigured()) {
            throw new PublishUnavailableException("preview gateway is not configured");
        }
        Object started = tenantScope.call(ctx.tenantId(), () -> begin(ctx, surveyId, requestId, ttlSeconds));
        if (started instanceof PreviewSessionView existing) {
            return new Created(existing, false);
        }
        Ticket ticket = (Ticket) started;
        return new Created(resume(ticket), ticket.fresh());
    }

    public PreviewSessionView get(TenantContext ctx, UUID id) {
        return tenantScope.call(ctx.tenantId(), () -> {
            PreviewSessionView session = require(id);
            require(ctx, Permission.VIEW, session.surveyId());
            return session;
        });
    }

    public PreviewSessionView close(TenantContext ctx, UUID id) {
        record Claim(PreviewSessionView session, UUID owner) {}
        Claim claim = tenantScope.call(ctx.tenantId(), () -> {
            PreviewSessionView current = require(id);
            require(ctx, Permission.EDIT, current.surveyId());
            if ("closed".equals(current.status()) || "failed".equals(current.status())) {
                return new Claim(current, null);
            }
            UUID owner = sessions.claimClosing(id).orElse(null);
            return new Claim(require(id), owner);
        });
        return claim.owner() == null ? claim.session() : closeClaimed(ctx.tenantId(), claim.session(), claim.owner());
    }

    @Scheduled(fixedDelayString = "${platform.survey.preview.cleanup-delay-ms:60000}")
    public void cleanupExpired() {
        List<TenantId> tenants = jdbc.sql("SELECT id FROM tenant WHERE status <> 'closed' ORDER BY id")
                .query((rs, row) -> new TenantId(rs.getObject(1, UUID.class))).list();
        tenants.forEach(this::cleanupExpired);
    }

    public void cleanupExpired(TenantId tenant) {
        List<UUID> stale = tenantScope.call(tenant, () -> sessions.staleCreating(100));
        for (UUID id : stale) {
            PreviewSessionView current = tenantScope.call(tenant, () -> require(id));
            if (current.expiresAt().isBefore(OffsetDateTime.now(clock)) && current.engineSid() != null) {
                UUID owner = UUID.randomUUID();
                boolean claimed = tenantScope.call(tenant, () -> sessions.claimCreatingForCleanup(id, owner));
                if (claimed) closeClaimed(tenant, tenantScope.call(tenant, () -> require(id)), owner);
            } else {
                Ticket pending = tenantScope.call(tenant, () -> ticket(tenant, current, false));
                resume(pending);
            }
        }
        List<UUID> expired = tenantScope.call(tenant, () -> sessions.expired(100));
        List<UUID> closing = tenantScope.call(tenant, () -> sessions.staleClosing(100));
        for (UUID id : java.util.stream.Stream.concat(expired.stream(), closing.stream()).distinct().toList()) {
            UUID owner = tenantScope.call(tenant, () -> sessions.claimClosing(id).orElse(null));
            if (owner != null) closeClaimed(tenant, tenantScope.call(tenant, () -> require(id)), owner);
        }
    }

    private Object begin(TenantContext ctx, UUID surveyId, UUID requestId, int ttlSeconds) {
        require(ctx, Permission.EDIT, surveyId);
        PreviewSessionView existing = sessions.findByRequest(requestId).orElse(null);
        if (existing != null) {
            if (!existing.surveyId().equals(surveyId)) {
                throw new SurveyConflictException("preview_request_reused",
                        "requestId belongs to another preview session");
            }
            return "creating".equals(existing.status()) ? ticket(ctx.tenantId(), existing, false) : existing;
        }
        PreviewSessionRepository.DraftSnapshot draft = sessions.draft(surveyId)
                .orElseThrow(() -> new SurveyNotFoundException("survey not found: " + surveyId));
        String instance = activeEngine(ctx.tenantId());
        UUID id = UUID.randomUUID();
        String generation = "preview-" + id.toString().replace("-", "");
        OffsetDateTime expires = OffsetDateTime.ofInstant(clock.instant().plusSeconds(ttlSeconds), ZoneOffset.UTC);
        boolean inserted = sessions.insert(ctx.tenantId(), id, requestId, surveyId, draft, ctx.actorId(),
                instance, generation, expires);
        if (!inserted) {
            PreviewSessionView concurrent = sessions.findByRequest(requestId).orElseThrow();
            return "creating".equals(concurrent.status())
                    ? ticket(ctx.tenantId(), concurrent, false) : concurrent;
        }
        return new Ticket(ctx.tenantId(), id, requestId, surveyId, draft.version(), ctx.actorId(), instance,
                generation, expires, draft.definition(), true);
    }

    private PreviewSessionView resume(Ticket ticket) {
        PreviewGatewayClient.CreateOutcome prepared = gateway.prepare(request(ticket));
        if (prepared instanceof PreviewGatewayClient.CreateOutcome.Unknown) {
            return tenantScope.call(ticket.tenant(), () -> require(ticket.id()));
        }
        if (prepared instanceof PreviewGatewayClient.CreateOutcome.Orphan orphan) {
            UUID owner = UUID.randomUUID();
            PreviewSessionView claimed = tenantScope.call(ticket.tenant(), () -> {
                sessions.bindSid(ticket.id(), orphan.engineSid());
                sessions.claimCreatingForCleanup(ticket.id(), owner);
                return require(ticket.id());
            });
            return closeClaimed(ticket.tenant(), claimed, owner);
        }
        if (prepared instanceof PreviewGatewayClient.CreateOutcome.Failed failed) {
            return tenantScope.call(ticket.tenant(), () -> { sessions.failed(ticket.id(), failed.reason()); return require(ticket.id()); });
        }
        PreviewGatewayClient.CreateOutcome.Prepared value = (PreviewGatewayClient.CreateOutcome.Prepared) prepared;
        boolean bound = tenantScope.call(ticket.tenant(), () -> sessions.bindSid(ticket.id(), value.engineSid()));
        if (!bound) return tenantScope.call(ticket.tenant(), () -> require(ticket.id()));
        PreviewGatewayClient.CreateOutcome activated = gateway.activate(identity(ticket));
        if (activated instanceof PreviewGatewayClient.CreateOutcome.Orphan) {
            UUID owner = UUID.randomUUID();
            PreviewSessionView claimed = tenantScope.call(ticket.tenant(), () -> {
                sessions.claimCreatingForCleanup(ticket.id(), owner);
                return require(ticket.id());
            });
            return closeClaimed(ticket.tenant(), claimed, owner);
        }
        return tenantScope.call(ticket.tenant(), () -> settleActivated(ticket, value.engineSid(), activated));
    }

    private PreviewSessionView settleActivated(Ticket ticket, int sid, PreviewGatewayClient.CreateOutcome outcome) {
        if (outcome instanceof PreviewGatewayClient.CreateOutcome.Ready ready) {
            if (sid != ready.engineSid() || !ticket.instance().equals(ready.engineInstanceId())
                    || !ticket.generation().equals(ready.generation())) sessions.failed(ticket.id(), "gateway preview binding mismatch");
            else sessions.ready(ticket.id(), sid, ready.previewUrl());
        } else if (outcome instanceof PreviewGatewayClient.CreateOutcome.Failed failed) {
            sessions.failed(ticket.id(), failed.reason());
        }
        return require(ticket.id());
    }

    private PreviewSessionView closeClaimed(TenantId tenant, PreviewSessionView claimed, UUID owner) {
        PreviewGatewayClient.CloseOutcome outcome = gateway.close(new PreviewGatewayClient.CloseRequest(
                tenant, claimed.id(), claimed.requestId(), claimed.engineInstanceId(), claimed.engineSid()));
        return tenantScope.call(tenant, () -> {
            if (outcome instanceof PreviewGatewayClient.CloseOutcome.Closed) {
                sessions.closed(claimed.id(), owner);
            } else if (outcome instanceof PreviewGatewayClient.CloseOutcome.Failed failed) {
                sessions.cleanupFailed(claimed.id(), owner, failed.reason());
                log.warn("preview cleanup failed: session={} attempts={} reason={}", claimed.id(),
                        claimed.cleanupAttempts(), failed.reason());
            }
            return require(claimed.id());
        });
    }

    private Ticket ticket(TenantId tenant, PreviewSessionView view, boolean fresh) {
        return new Ticket(tenant, view.id(), view.requestId(), view.surveyId(), view.draftVersion(),
                view.requestedBy(), view.engineInstanceId(), view.generation(), view.expiresAt(),
                sessions.definition(view.id()), fresh);
    }

    private PreviewGatewayClient.CreateRequest request(Ticket ticket) {
        return new PreviewGatewayClient.CreateRequest(ticket.tenant(), ticket.id(), ticket.requestId(),
                ticket.instance(), ticket.definition(), ticket.generation(), ticket.expiresAt());
    }

    private PreviewGatewayClient.Identity identity(Ticket ticket) {
        return new PreviewGatewayClient.Identity(ticket.tenant(), ticket.id(), ticket.requestId());
    }

    private void require(TenantContext ctx, Permission permission, UUID surveyId) {
        Decision decision = access.can(ctx, permission, surveyId);
        if (!decision.allowed()) {
            if (decision.reason() == DecisionReason.UNKNOWN_RESOURCE) {
                throw new SurveyNotFoundException("survey not found: " + surveyId);
            }
            throw new SurveyAccessDeniedException(decision.reason(), permission.code() + " denied: " + decision.detail());
        }
    }

    private PreviewSessionView require(UUID id) {
        return sessions.find(id).orElseThrow(() -> new SurveyNotFoundException("preview session not found: " + id));
    }

    private String activeEngine(TenantId tenant) {
        return engines.list(tenant).stream().filter(i -> i.status() == EngineInstanceStatus.ACTIVE)
                .map(EngineInstance::id).findFirst()
                .orElseThrow(() -> new SurveyConflictException("no_active_engine_instance",
                        "tenant has no active engine instance"));
    }
}

interface PreviewGatewayClient {
    record CreateRequest(TenantId tenantId, UUID sessionId, UUID requestId, String engineInstanceId, String definitionJson,
            String generation, OffsetDateTime expiresAt) {
    }
    record Identity(TenantId tenantId, UUID sessionId, UUID requestId) {}
    sealed interface CreateOutcome {
        record Prepared(int engineSid) implements CreateOutcome {}
        record Ready(int engineSid, String engineInstanceId, String generation, OffsetDateTime expiresAt,
                String previewUrl) implements CreateOutcome {
        }
        record Failed(String reason) implements CreateOutcome {
        }
        record Orphan(int engineSid, String reason) implements CreateOutcome {}
        record Unknown(String reason) implements CreateOutcome {}
    }
    record CloseRequest(TenantId tenantId, UUID sessionId, UUID requestId,
            String engineInstanceId, Integer engineSid) {
    }
    sealed interface CloseOutcome {
        record Closed() implements CloseOutcome {
        }
        record Failed(String reason) implements CloseOutcome {
        }
    }
    boolean isConfigured();
    CreateOutcome prepare(CreateRequest request);
    CreateOutcome activate(Identity request);
    CloseOutcome close(CloseRequest request);
}

@Component
class HttpPreviewGatewayClient implements PreviewGatewayClient {
    private final URI baseUrl;
    private final byte[] secret;
    private final Duration timeout;
    private final JsonMapper json;
    private final HttpClient http;

    HttpPreviewGatewayClient(@Value("${platform.pubgw.url:}") String url,
            @Value("${platform.pubgw.secret:}") String secret,
            @Value("${platform.pubgw.timeout-seconds:120}") long timeoutSeconds, JsonMapper json) {
        this.baseUrl = url == null || url.isBlank() ? null : URI.create(url.endsWith("/") ? url : url + "/");
        this.secret = secret == null ? new byte[0] : secret.getBytes(StandardCharsets.UTF_8);
        this.timeout = Duration.ofSeconds(timeoutSeconds);
        this.json = json;
        this.http = HttpClient.newBuilder().connectTimeout(timeout).build();
    }

    @Override public boolean isConfigured() { return baseUrl != null && secret.length >= 32; }

    @Override public CreateOutcome prepare(CreateRequest request) {
        ObjectNode body = json.createObjectNode();
        body.put("tenantId", request.tenantId().value().toString());
        body.put("sessionId", request.sessionId().toString());
        body.put("requestId", request.requestId().toString());
        body.put("engineInstanceId", request.engineInstanceId());
        body.set("definition", json.readTree(request.definitionJson()));
        body.put("generation", request.generation());
        body.put("expiresAt", request.expiresAt().withOffsetSameInstant(ZoneOffset.UTC).toString().replace("+00:00", "Z"));
        Exchange exchange = post("v1/preview", json.writeValueAsBytes(body));
        if (exchange.failure != null) return new CreateOutcome.Unknown(exchange.failure);
        try {
            JsonNode root = json.readTree(exchange.body);
            if (exchange.status == 503) return new CreateOutcome.Unknown("gateway result unknown");
            String status = root == null ? "" : root.path("status").asString();
            if (exchange.status == 502 && root != null && !root.path("result").path("orphanSurveyId").isNull()
                    && root.path("result").path("orphanSurveyId").canConvertToInt()) {
                return new CreateOutcome.Orphan(root.path("result").path("orphanSurveyId").intValue(),
                        "gateway left an orphan preview");
            }
            if (exchange.status != 200 || !("prepared".equals(status) || "ready".equals(status))) {
                return new CreateOutcome.Failed("http " + exchange.status);
            }
            JsonNode result = root.path("result");
            return new CreateOutcome.Prepared(result.path("surveyId").intValue());
        } catch (RuntimeException e) {
            return new CreateOutcome.Unknown("unreadable gateway response");
        }
    }

    @Override public CreateOutcome activate(Identity request) {
        ObjectNode body = identity(request.tenantId(), request.sessionId(), request.requestId());
        Exchange exchange = post("v1/preview/activate", json.writeValueAsBytes(body));
        if (exchange.failure != null || exchange.status == 503) {
            return new CreateOutcome.Unknown(exchange.failure == null ? "gateway result unknown" : exchange.failure);
        }
        try {
            JsonNode root = json.readTree(exchange.body);
            if (exchange.status == 502 && !root.path("result").path("orphanSurveyId").isNull()
                    && root.path("result").path("orphanSurveyId").canConvertToInt()) {
                return new CreateOutcome.Orphan(root.path("result").path("orphanSurveyId").intValue(),
                        "gateway left an orphan preview");
            }
            if (exchange.status != 200 || !"ready".equals(root.path("status").asString()))
                return new CreateOutcome.Failed("http " + exchange.status);
            JsonNode result = root.path("result");
            return new CreateOutcome.Ready(result.path("surveyId").intValue(),
                    result.path("engineInstanceId").asString(), result.path("generation").asString(),
                    OffsetDateTime.parse(result.path("expiresAt").asString()), result.path("previewUrl").asString());
        } catch (RuntimeException e) {
            return new CreateOutcome.Unknown("unreadable gateway response");
        }
    }

    @Override public CloseOutcome close(CloseRequest request) {
        ObjectNode body = identity(request.tenantId(), request.sessionId(), request.requestId());
        Exchange exchange = post("v1/preview/close", json.writeValueAsBytes(body));
        return exchange.failure == null && exchange.status == 200
                ? new CloseOutcome.Closed() : new CloseOutcome.Failed(
                        exchange.failure == null ? "http " + exchange.status : exchange.failure);
    }

    private ObjectNode identity(TenantId tenant, UUID session, UUID request) {
        return json.createObjectNode().put("tenantId", tenant.value().toString())
                .put("sessionId", session.toString()).put("requestId", request.toString());
    }

    private Exchange post(String path, byte[] body) {
        if (!isConfigured()) return new Exchange(0, new byte[0], "gateway_not_configured");
        String timestamp = Long.toString(InstantHolder.epochSecond());
        HttpRequest request = HttpRequest.newBuilder(baseUrl.resolve(path)).timeout(timeout)
                .header("Content-Type", "application/json")
                .header("X-Pubgw-Timestamp", timestamp)
                .header("X-Pubgw-Signature",
                        cn.mjy.platform.survey.gateway.PublishGatewaySigner.sign(secret, timestamp, body))
                .POST(HttpRequest.BodyPublishers.ofByteArray(body)).build();
        try {
            HttpResponse<byte[]> response = http.send(request, HttpResponse.BodyHandlers.ofByteArray());
            return new Exchange(response.statusCode(), response.body(), null);
        } catch (HttpTimeoutException e) {
            return new Exchange(0, new byte[0], "timed out");
        } catch (IOException e) {
            return new Exchange(0, new byte[0], "network error");
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return new Exchange(0, new byte[0], "interrupted");
        }
    }

    private record Exchange(int status, byte[] body, String failure) {
    }
    private static final class InstantHolder {
        private static long epochSecond() { return java.time.Instant.now().getEpochSecond(); }
    }
}
