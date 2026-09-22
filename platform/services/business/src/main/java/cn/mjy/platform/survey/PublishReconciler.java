package cn.mjy.platform.survey;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantDirectory;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.PublishAttemptRepository.AttemptRow;
import cn.mjy.platform.survey.PublishReconciliationRepository.LockedCandidate;
import cn.mjy.platform.survey.SurveyPublishService.Ticket;
import cn.mjy.platform.survey.gateway.GatewayOutcome;
import cn.mjy.platform.survey.gateway.PublishGatewayClient;
import java.time.Duration;
import java.util.EnumMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * 待核对发布的后台核对（契约 publish-gateway-v1 v1.1：网关按 requestId 存档结果，同一 requestId + 同一请求体
 * 重发只返回首次结果，不会再发布一次）。
 *
 * <p><b>跨租户枚举</b>：不开放任何跨租户读取。经 {@link TenantDirectory} 拿到未关闭租户的标识名单，
 * 逐个租户进入 {@link TenantScope} 查"本租户"到期的待核对问卷（行级安全照常生效，部分索引让每次扫描很便宜）；
 * 每个问卷的认领、收尾都在所属租户的作用域里进行，审计的操作者是系统身份 {@value #SYSTEM_ACTOR}。
 *
 * <p><b>每个问卷三步</b>，与用户发布同一套收尾路径（{@link PublishSettlement}）：
 * <ol>
 *   <li>认领（事务，FOR UPDATE SKIP LOCKED）：锁内复核仍待核对且退避已到；累计发送次数已达上限则标记人工复核、
 *       不再发送；否则计数加一、问卷置为 publishing（用户此时发布得到 409 publish_in_progress），
 *       并预先写好下一次的退避时间——即使本进程在网关调用中途死掉，陈旧判定加退避也能让它再被捡起；</li>
 *   <li>原样重发尝试里记录的 requestId、实例与定义快照（不在事务里）；</li>
 *   <li>收尾：published → 版本 + 绑定 + 路由；failed → publish_failed；仍未知 → 保持待核对。
 *       收尾只认"仍是 publishing 且 requestId 相同"的问卷，因此与用户并发时只记录一次结局。</li>
 * </ol>
 */
@Component
public class PublishReconciler {

    static final String SYSTEM_ACTOR = "system:publish-reconciler";
    static final String MANUAL_REVIEW_ACTION = "survey.publish.manual_review";

    /** 退避指数的上限，防止位移溢出；实际等待还会被 backoffMax 截断。 */
    private static final int MAX_BACKOFF_EXPONENT = 20;

    private static final Logger log = LoggerFactory.getLogger(PublishReconciler.class);

    /** 一个问卷本轮的处理结果。 */
    enum Result { PUBLISHED, FAILED, STILL_PENDING, MANUAL_REVIEW, SKIPPED, ERROR }

    /** 认领结果：ticket 非空表示已认领、需要调用网关；否则 result 说明为何不发送。 */
    private record Claim(Result result, Ticket ticket) {

        static Claim not(Result result) {
            return new Claim(result, null);
        }
    }

    private final TenantDirectory tenants;
    private final TenantScope tenantScope;
    private final PublishReconciliationRepository candidates;
    private final PublishAttemptRepository attempts;
    private final SurveyRepository surveys;
    private final PublishGatewayClient gateway;
    private final PublishSettlement settlement;
    private final SurveyAudit audit;
    private final PublishReconciliationProperties properties;

    PublishReconciler(TenantDirectory tenants, TenantScope tenantScope, PublishReconciliationRepository candidates,
            PublishAttemptRepository attempts, SurveyRepository surveys, PublishGatewayClient gateway,
            PublishSettlement settlement, SurveyAudit audit, PublishReconciliationProperties properties) {
        this.tenants = tenants;
        this.tenantScope = tenantScope;
        this.candidates = candidates;
        this.attempts = attempts;
        this.surveys = surveys;
        this.gateway = gateway;
        this.settlement = settlement;
        this.audit = audit;
        this.properties = properties;
    }

    /** 跑一轮：跨所有未关闭租户合计最多处理 batchSize 个问卷。单个问卷或租户出错不影响其余。 */
    public Map<Result, Integer> runOnce() {
        Map<Result, Integer> tally = new EnumMap<>(Result.class);
        if (!gateway.isConfigured()) {
            log.debug("publish gateway is not configured; skipping publish reconciliation");
            return tally;
        }
        int budget = properties.batchSize();
        for (TenantId tenant : tenants.openTenants()) {
            if (budget <= 0) {
                break;
            }
            for (UUID survey : dueSurveys(tenant, budget)) {
                tally.merge(reconcile(tenant, survey), 1, Integer::sum);
                budget--;
            }
        }
        if (!tally.isEmpty()) {
            log.info("publish reconciliation round finished: {}", tally);
        }
        return tally;
    }

    private List<UUID> dueSurveys(TenantId tenant, int limit) {
        try {
            return tenantScope.call(tenant,
                    () -> candidates.dueSurveys(SurveyPublishService.STALE_PUBLISHING, limit));
        } catch (RuntimeException e) {
            log.error("publish reconciliation could not scan tenant {}", tenant, e);
            return List.of();
        }
    }

    Result reconcile(TenantId tenant, UUID surveyId) {
        TenantContext system = new TenantContext(tenant, SYSTEM_ACTOR, List.of(), "reconcile-" + UUID.randomUUID());
        try {
            Claim claim = tenantScope.call(tenant, () -> claim(system, surveyId));
            if (claim.ticket() == null) {
                return claim.result();
            }
            GatewayOutcome outcome = callGateway(claim.ticket());
            SurveyStatus settled = settlement.settle(system, claim.ticket(), outcome).survey().status();
            return switch (settled) {
                case PUBLISHED -> Result.PUBLISHED;
                case PUBLISH_FAILED -> Result.FAILED;
                default -> Result.STILL_PENDING;
            };
        } catch (RuntimeException e) {
            log.error("publish reconciliation failed for survey {} of tenant {}", surveyId, tenant, e);
            return Result.ERROR;
        }
    }

    /** 事务内、持问卷行锁。行被占用或已不再待核对时跳过。 */
    private Claim claim(TenantContext system, UUID surveyId) {
        Optional<LockedCandidate> locked = candidates.lockIfFree(surveyId, SurveyPublishService.STALE_PUBLISHING);
        if (locked.isEmpty() || !locked.get().due()) {
            return Claim.not(Result.SKIPPED);
        }
        UUID requestId = locked.get().requestId();
        AttemptRow attempt = attempts.find(requestId)
                .orElseThrow(() -> new IllegalStateException("publish attempt missing: " + requestId));
        if (attempt.tries() >= properties.maxAttempts()) {
            flagManualReview(system, surveyId, attempt);
            return Claim.not(Result.MANUAL_REVIEW);
        }
        log.info("reconciling publish of survey {} with request {} (try {})",
                surveyId, requestId, attempt.tries() + 1);
        attempts.markRetry(requestId);
        surveys.markPublishing(surveyId, requestId);
        candidates.scheduleNext(requestId, backoffAfter(attempt.tries() + 1));
        return new Claim(null, new Ticket(surveyId, requestId, attempt.engineInstanceId(), attempt.draftVersion(),
                attempt.definition()));
    }

    private void flagManualReview(TenantContext system, UUID surveyId, AttemptRow attempt) {
        if (!candidates.flagManualReview(attempt.requestId())) {
            return;
        }
        log.error("PUBLISH NEEDS MANUAL REVIEW: tenant={} survey={} request={} engine={} outcome still unknown "
                + "after {} tries; automatic reconciliation stopped, last failures={}", system.tenantId(), surveyId,
                attempt.requestId(), attempt.engineInstanceId(), attempt.tries(), attempt.failures());
        audit.record(system, MANUAL_REVIEW_ACTION, surveyId,
                "request=" + attempt.requestId() + " tries=" + attempt.tries());
    }

    /** 第 tries 次发送之后的退避：backoffInitial × 2^(tries-1)，不超过 backoffMax。 */
    Duration backoffAfter(int tries) {
        int exponent = Math.min(Math.max(tries - 1, 0), MAX_BACKOFF_EXPONENT);
        Duration delay = properties.backoffInitial().multipliedBy(1L << exponent);
        return delay.compareTo(properties.backoffMax()) > 0 ? properties.backoffMax() : delay;
    }

    private GatewayOutcome callGateway(Ticket ticket) {
        try {
            return gateway.publish(ticket.request());
        } catch (RuntimeException e) {
            // 与用户发布一致：客户端意外抛异常时结果同样未知，按待核对收尾，不让问卷卡在 publishing。
            log.error("publish gateway client failed for request {}", ticket.requestId(), e);
            return new GatewayOutcome.Unknown("client error: " + e.getClass().getSimpleName());
        }
    }
}
