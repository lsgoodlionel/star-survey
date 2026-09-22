package cn.mjy.platform.survey;

import cn.mjy.platform.access.Permission;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.PublishAttemptRepository.AttemptRow;
import cn.mjy.platform.survey.SurveyRepository.SurveyRow;
import cn.mjy.platform.survey.gateway.GatewayOutcome;
import cn.mjy.platform.survey.gateway.GatewayRequest;
import cn.mjy.platform.survey.gateway.PublishGatewayClient;
import cn.mjy.platform.tenant.engine.EngineInstance;
import cn.mjy.platform.tenant.engine.EngineInstanceService;
import cn.mjy.platform.tenant.engine.EngineInstanceStatus;
import java.time.Duration;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * 发布流程（契约 publish-gateway-v1 的平台侧）。分三步，网关调用不在任何数据库事务里：
 * <ol>
 *   <li><b>开始</b>（事务 1，持问卷行锁）：经 access 模块判定 PUBLISH（需要审核时 403 APPROVAL_REQUIRED，绝不绕过）；
 *       按状态决定是新发起（新 requestId、选本租户的 active 引擎实例、固化定义快照）还是核对（沿用上次的
 *       requestId、实例与快照）；先把尝试落库、问卷置为 publishing，再提交。并发的第二个请求在行锁后看到
 *       publishing，得到 409 publish_in_progress——同一问卷同一时刻只有一个请求到达网关。</li>
 *   <li><b>调用网关</b>：结局归为成功 / 失败 / 拒收 / 未知四类。</li>
 *   <li><b>收尾</b>（事务 2，再持行锁）：见 {@link PublishSettlement}。</li>
 * </ol>
 */
@Service
public class SurveyPublishService {

    /**
     * publishing 超过这个时长仍未收尾，视为发布进程已经死掉（结果未知），允许用同一 requestId 核对。
     * 必须明显大于网关调用超时（默认 120 秒），否则会和仍在进行的调用抢同一个 requestId——
     * 即使抢了也安全（网关按 requestId 幂等，收尾时只有第一个生效），只是多一次调用。
     */
    public static final Duration STALE_PUBLISHING = Duration.ofMinutes(10);

    private static final Logger log = LoggerFactory.getLogger(SurveyPublishService.class);

    private final TenantScope tenantScope;
    private final SurveyAccess access;
    private final SurveyRepository surveys;
    private final PublishAttemptRepository attempts;
    private final SurveyDefinitions definitions;
    private final EngineInstanceService engines;
    private final PublishGatewayClient gateway;
    private final PublishSettlement settlement;

    SurveyPublishService(TenantScope tenantScope, SurveyAccess access, SurveyRepository surveys,
            PublishAttemptRepository attempts, SurveyDefinitions definitions, EngineInstanceService engines,
            PublishGatewayClient gateway, PublishSettlement settlement) {
        this.tenantScope = tenantScope;
        this.access = access;
        this.surveys = surveys;
        this.attempts = attempts;
        this.definitions = definitions;
        this.engines = engines;
        this.gateway = gateway;
        this.settlement = settlement;
    }

    /** 已固化、即将发给网关的一次尝试。 */
    record Ticket(UUID surveyId, UUID requestId, String engineInstanceId, int draftVersion, String definition) {

        GatewayRequest request() {
            return new GatewayRequest(requestId, engineInstanceId, definition);
        }
    }

    public PublishOutcome publish(TenantContext ctx, UUID surveyId) {
        Ticket ticket = begin(ctx, surveyId);
        GatewayOutcome outcome = callGateway(ticket);
        return settlement.settle(ctx, ticket, outcome);
    }

    private Ticket begin(TenantContext ctx, UUID surveyId) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.require(ctx, Permission.PUBLISH, surveyId);
            if (!gateway.isConfigured()) {
                throw new PublishUnavailableException("publish gateway is not configured");
            }
            SurveyRow row = surveys.lock(surveyId, STALE_PUBLISHING)
                    .orElseThrow(() -> new SurveyNotFoundException("survey not found: " + surveyId));
            return switch (row.status()) {
                case PUBLISHED -> throw new SurveyConflictException(SurveyConflictException.ALREADY_PUBLISHED,
                        "survey is already published; republishing is not supported yet (ADR 0009)");
                case PUBLISHING -> {
                    if (!row.stale()) {
                        throw new SurveyConflictException(SurveyConflictException.PUBLISH_IN_PROGRESS,
                                "a publish of this survey is already in progress");
                    }
                    yield reconcile(row);
                }
                case PENDING_RECONCILIATION -> reconcile(row);
                case DRAFT, PUBLISH_FAILED -> freshAttempt(ctx, row);
            };
        });
    }

    /** 新发起：新 requestId，定义以当前草稿为准并再校验一次，requestId 先于网关调用落库。 */
    private Ticket freshAttempt(TenantContext ctx, SurveyRow row) {
        String instance = activeEngineInstance(ctx.tenantId());
        String definition = definitions.serialize(
                definitions.normalize(definitions.parse(row.draftDefinition()), row.id()));
        UUID requestId = UUID.randomUUID();
        attempts.insert(ctx.tenantId(), requestId, row.id(), row.draftVersion(), instance, definition, ctx.actorId());
        surveys.markPublishing(row.id(), requestId);
        return new Ticket(row.id(), requestId, instance, row.draftVersion(), definition);
    }

    /** 核对：结果未知时只能原样重发（同一 requestId、同一实例、同一份定义），由网关幂等给出确定结局。 */
    private Ticket reconcile(SurveyRow row) {
        AttemptRow attempt = attempts.find(row.currentRequestId())
                .orElseThrow(() -> new IllegalStateException("publish attempt missing: " + row.currentRequestId()));
        log.info("reconciling publish of survey {} with request {} (try {})",
                row.id(), attempt.requestId(), attempt.tries() + 1);
        attempts.markRetry(attempt.requestId());
        surveys.markPublishing(row.id(), attempt.requestId());
        return new Ticket(row.id(), attempt.requestId(), attempt.engineInstanceId(), attempt.draftVersion(),
                attempt.definition());
    }

    /** 本租户最早登记的 active 实例；没有时 409，问卷保持原状态。 */
    private String activeEngineInstance(TenantId tenant) {
        return engines.list(tenant).stream()
                .filter(instance -> instance.status() == EngineInstanceStatus.ACTIVE)
                .map(EngineInstance::id)
                .findFirst()
                .orElseThrow(() -> new SurveyConflictException(SurveyConflictException.NO_ACTIVE_ENGINE_INSTANCE,
                        "tenant has no active engine instance to publish to"));
    }

    private GatewayOutcome callGateway(Ticket ticket) {
        try {
            return gateway.publish(ticket.request());
        } catch (RuntimeException e) {
            // 契约要求实现不抛异常；万一抛了，结果同样未知，按待核对处理而不是让问卷卡在 publishing。
            log.error("publish gateway client failed for request {}", ticket.requestId(), e);
            return new GatewayOutcome.Unknown("client error: " + e.getClass().getSimpleName());
        }
    }
}
