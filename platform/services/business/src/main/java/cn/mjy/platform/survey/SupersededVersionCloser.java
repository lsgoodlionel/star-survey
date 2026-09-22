package cn.mjy.platform.survey;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantDirectory;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.VersionRetirementRepository.OpenRetirement;
import cn.mjy.platform.survey.gateway.CloseOutcome;
import cn.mjy.platform.survey.gateway.GatewayCloseRequest;
import cn.mjy.platform.survey.gateway.PublishGatewayClient;
import java.time.Duration;
import java.util.List;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * 收口被取代的已发布版本（ADR 0012 决定 3）：公开路由已切到新版之后，让旧的引擎问卷不再接收新答卷
 * （网关 {@code POST /v1/close}：设过期，不停用、不删除）。
 *
 * <ul>
 *   <li>发布收尾提交之后立即对该问卷试一次（{@link #closeRetired}），失败不影响发布结论；</li>
 *   <li>没收口成功的由后台逐租户重试（{@link #runOnce}，随发布核对的定时任务一起跑），按
 *       {@code platform.survey.reconcile} 的退避参数退避，不设上限——收口是幂等的，重试无害。</li>
 * </ul>
 * 收口前在所属租户作用域内复核：绝不收口当前在线的版本。网关调用不在数据库事务里。
 */
@Component
public class SupersededVersionCloser {

    static final String SYSTEM_ACTOR = "system:version-closer";
    static final String AUDIT_CLOSED = "survey.version.closed";

    private static final int MAX_BACKOFF_EXPONENT = 20;
    private static final int MAX_ERROR_LENGTH = 500;

    private static final Logger log = LoggerFactory.getLogger(SupersededVersionCloser.class);

    private final TenantDirectory tenants;
    private final TenantScope tenantScope;
    private final VersionRetirementRepository retirements;
    private final SurveyRepository surveys;
    private final PublishGatewayClient gateway;
    private final SurveyAudit audit;
    private final PublishReconciliationProperties properties;

    SupersededVersionCloser(TenantDirectory tenants, TenantScope tenantScope, VersionRetirementRepository retirements,
            SurveyRepository surveys, PublishGatewayClient gateway, SurveyAudit audit,
            PublishReconciliationProperties properties) {
        this.tenants = tenants;
        this.tenantScope = tenantScope;
        this.retirements = retirements;
        this.surveys = surveys;
        this.gateway = gateway;
        this.audit = audit;
        this.properties = properties;
    }

    /** 发布收尾之后调用：收口该问卷所有到期的旧版本。从不抛异常。 */
    void closeRetired(TenantId tenant, UUID surveyId) {
        try {
            List<OpenRetirement> open = tenantScope.call(tenant, () -> retirements.openFor(surveyId));
            open.forEach(retirement -> close(tenant, retirement));
        } catch (RuntimeException e) {
            log.error("closing superseded versions of survey {} failed; the background round will retry", surveyId, e);
        }
    }

    /** 后台一轮：跨所有未关闭租户合计最多 batchSize 个旧版本。返回确认收口的个数。 */
    public int runOnce() {
        if (!gateway.isConfigured()) {
            return 0;
        }
        int budget = properties.batchSize();
        int closed = 0;
        for (TenantId tenant : tenants.openTenants()) {
            if (budget <= 0) {
                break;
            }
            for (OpenRetirement retirement : due(tenant, budget)) {
                closed += close(tenant, retirement) ? 1 : 0;
                budget--;
            }
        }
        return closed;
    }

    private List<OpenRetirement> due(TenantId tenant, int limit) {
        try {
            return tenantScope.call(tenant, () -> retirements.due(limit));
        } catch (RuntimeException e) {
            log.error("could not scan tenant {} for superseded versions to close", tenant, e);
            return List.of();
        }
    }

    private boolean close(TenantId tenant, OpenRetirement retirement) {
        if (isLive(tenant, retirement)) {
            log.error("REFUSING to close live version {} of survey {} (sid {}); retirement record is inconsistent",
                    retirement.versionNo(), retirement.surveyId(), retirement.engineSid());
            return false;
        }
        CloseOutcome outcome = callGateway(retirement);
        TenantContext system = new TenantContext(tenant, SYSTEM_ACTOR, List.of(), "close-" + UUID.randomUUID());
        return switch (outcome) {
            case CloseOutcome.Closed closed -> {
                tenantScope.run(tenant, () -> {
                    retirements.markClosed(retirement.surveyId(), retirement.versionNo(), closed.expires());
                    audit.record(system, AUDIT_CLOSED, retirement.surveyId(), "version=" + retirement.versionNo()
                            + " engine=" + retirement.engineInstanceId() + " sid=" + retirement.engineSid()
                            + " expires=" + closed.expires());
                });
                log.info("closed superseded version {} of survey {} (sid {}, expires {})", retirement.versionNo(),
                        retirement.surveyId(), retirement.engineSid(), closed.expires());
                yield true;
            }
            case CloseOutcome.NotClosed notClosed -> {
                Duration retry = backoffAfter(retirement.closeAttempts() + 1);
                tenantScope.run(tenant, () -> retirements.markCloseFailed(retirement.surveyId(),
                        retirement.versionNo(), truncate(notClosed.reason()), retry));
                log.warn("superseded version {} of survey {} (sid {}) is still open to respondents: {}; retry in {}",
                        retirement.versionNo(), retirement.surveyId(), retirement.engineSid(), notClosed.reason(),
                        retry);
                yield false;
            }
        };
    }

    private boolean isLive(TenantId tenant, OpenRetirement retirement) {
        return tenantScope.call(tenant, () -> surveys.find(retirement.surveyId())
                .map(row -> row.publishedVersion() != null && row.publishedVersion() == retirement.versionNo())
                .orElse(true));
    }

    private CloseOutcome callGateway(OpenRetirement retirement) {
        try {
            return gateway.close(new GatewayCloseRequest(UUID.randomUUID(), retirement.engineInstanceId(),
                    retirement.engineSid()));
        } catch (RuntimeException e) {
            log.error("publish gateway client failed closing sid {}", retirement.engineSid(), e);
            return new CloseOutcome.NotClosed("client error: " + e.getClass().getSimpleName());
        }
    }

    /** 第 attempts 次失败之后的等待：backoffInitial × 2^(attempts-1)，不超过 backoffMax。 */
    Duration backoffAfter(int attempts) {
        int exponent = Math.min(Math.max(attempts - 1, 0), MAX_BACKOFF_EXPONENT);
        Duration delay = properties.backoffInitial().multipliedBy(1L << exponent);
        return delay.compareTo(properties.backoffMax()) > 0 ? properties.backoffMax() : delay;
    }

    private static String truncate(String text) {
        return text.length() <= MAX_ERROR_LENGTH ? text : text.substring(0, MAX_ERROR_LENGTH);
    }
}
