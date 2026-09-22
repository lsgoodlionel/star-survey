package cn.mjy.platform.survey;

import cn.mjy.platform.access.Permission;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantDirectory;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.DriftCheckRepository.Finding;
import cn.mjy.platform.survey.DriftCheckRepository.Target;
import cn.mjy.platform.survey.gateway.DriftOutcome;
import cn.mjy.platform.survey.gateway.GatewayDriftRequest;
import cn.mjy.platform.survey.gateway.PublishGatewayClient;
import java.util.List;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * 漂移检测（WP-01 01.4，ADR 0012 决定 5）：有人绕过平台在引擎管理端改了已发布的问卷。
 *
 * <ul>
 *   <li>按需：对问卷有编辑权的人可以检查任一已发布版本（在线的或已被取代的）；有查看权的人可以读检查记录；</li>
 *   <li>定时（默认关闭）：逐租户检查在线版本，同一版本在 recheckAfter 内只查一次；</li>
 *   <li>每次检查都记一行（match / drift / error）；drift 另写审计并大声记日志。</li>
 * </ul>
 * <b>绝不自动重新发布或覆盖引擎里的改动</b>：检查只读，结论交给人处理。网关调用不在数据库事务里。
 */
@Service
public class SurveyDriftService {

    static final String AUDIT_DRIFT = "survey.drift.detected";
    static final String SYSTEM_ACTOR = "system:drift-monitor";
    static final int LIST_LIMIT = 50;

    private static final Logger log = LoggerFactory.getLogger(SurveyDriftService.class);

    private final TenantDirectory tenants;
    private final TenantScope tenantScope;
    private final SurveyAccess access;
    private final DriftCheckRepository checks;
    private final PublishGatewayClient gateway;
    private final SurveyAudit audit;
    private final DriftMonitorProperties properties;

    SurveyDriftService(TenantDirectory tenants, TenantScope tenantScope, SurveyAccess access,
            DriftCheckRepository checks, PublishGatewayClient gateway, SurveyAudit audit,
            DriftMonitorProperties properties) {
        this.tenants = tenants;
        this.tenantScope = tenantScope;
        this.access = access;
        this.checks = checks;
        this.gateway = gateway;
        this.audit = audit;
        this.properties = properties;
    }

    /** 立即检查某个已发布版本并记录结论（需要编辑权）。 */
    public DriftCheckView check(TenantContext ctx, UUID surveyId, int versionNo) {
        Target target = tenantScope.call(ctx.tenantId(), () -> {
            access.require(ctx, Permission.EDIT, surveyId);
            return requireTarget(surveyId, versionNo);
        });
        if (!gateway.isConfigured()) {
            throw new PublishUnavailableException("publish gateway is not configured");
        }
        return run(ctx, target);
    }

    /** 某版本最近的检查记录，新的在前（需要查看权）。 */
    public List<DriftCheckView> checks(TenantContext ctx, UUID surveyId, int versionNo) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.require(ctx, Permission.VIEW, surveyId);
            requireTarget(surveyId, versionNo);
            return checks.list(surveyId, versionNo, LIST_LIMIT);
        });
    }

    /** 定时巡检一轮：跨所有未关闭租户合计最多 batchSize 个在线版本。返回检查的个数。 */
    public int runScheduledRound() {
        if (!gateway.isConfigured()) {
            return 0;
        }
        int budget = properties.batchSize();
        for (TenantId tenant : tenants.openTenants()) {
            if (budget <= 0) {
                break;
            }
            budget -= checkLiveVersions(tenant, budget);
        }
        return properties.batchSize() - budget;
    }

    /** 巡检一个租户的在线版本（最多 limit 个）。单个版本出错不影响其余。返回检查的个数。 */
    int checkLiveVersions(TenantId tenant, int limit) {
        List<Target> due;
        try {
            due = tenantScope.call(tenant, () -> checks.liveVersionsDue(properties.recheckAfter(), limit));
        } catch (RuntimeException e) {
            log.error("drift monitor could not scan tenant {}", tenant, e);
            return 0;
        }
        TenantContext system = new TenantContext(tenant, SYSTEM_ACTOR, List.of(), "drift-" + UUID.randomUUID());
        int checked = 0;
        for (Target target : due) {
            try {
                run(system, target);
                checked++;
            } catch (RuntimeException e) {
                log.error("drift check of survey {} version {} failed", target.surveyId(), target.versionNo(), e);
            }
        }
        return checked;
    }

    private Target requireTarget(UUID surveyId, int versionNo) {
        return checks.target(surveyId, versionNo).orElseThrow(() -> new SurveyNotFoundException(
                "published version " + versionNo + " of survey " + surveyId + " not found"));
    }

    private DriftCheckView run(TenantContext ctx, Target target) {
        Finding finding = finding(callGateway(target));
        return tenantScope.call(ctx.tenantId(), () -> {
            long id = checks.insert(ctx.tenantId(), target, finding, ctx.actorId());
            if (DriftCheckView.DRIFT.equals(finding.outcome())) {
                log.warn("DRIFT DETECTED: tenant={} survey={} version={} engine={} sid={} expected={} current={} "
                        + "issues={}", ctx.tenantId(), target.surveyId(), target.versionNo(),
                        target.engineInstanceId(), target.engineSid(), target.fingerprint(),
                        finding.currentFingerprint(), finding.issues());
                audit.record(ctx, AUDIT_DRIFT, target.surveyId(), "version=" + target.versionNo() + " sid="
                        + target.engineSid() + " current=" + finding.currentFingerprint() + " check=" + id);
            }
            return checks.find(id).orElseThrow();
        });
    }

    private DriftOutcome callGateway(Target target) {
        try {
            return gateway.driftCheck(new GatewayDriftRequest(target.engineInstanceId(), target.engineSid(),
                    target.fingerprint(), target.bindingJson()));
        } catch (RuntimeException e) {
            log.error("publish gateway client failed checking sid {}", target.engineSid(), e);
            return new DriftOutcome.Unavailable("client error: " + e.getClass().getSimpleName());
        }
    }

    private static Finding finding(DriftOutcome outcome) {
        return switch (outcome) {
            case DriftOutcome.Checked checked -> new Finding(
                    checked.drifted() ? DriftCheckView.DRIFT : DriftCheckView.MATCH,
                    checked.currentFingerprint(),
                    checked.issues().stream().map(i -> new DriftCheckView.Issue(i.code(), i.detail())).toList(),
                    checked.renamed().stream()
                            .map(r -> new DriftCheckView.Rename(r.uuid(), r.from(), r.to())).toList(),
                    null);
            case DriftOutcome.Unavailable unavailable ->
                    new Finding(DriftCheckView.ERROR, null, List.of(), List.of(), unavailable.reason());
        };
    }
}
