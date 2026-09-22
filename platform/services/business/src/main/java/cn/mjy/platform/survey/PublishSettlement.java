package cn.mjy.platform.survey;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.SurveyPublishService.Ticket;
import cn.mjy.platform.survey.SurveyRepository.SurveyRow;
import cn.mjy.platform.survey.gateway.GatewayBinding;
import cn.mjy.platform.survey.gateway.GatewayOutcome;
import cn.mjy.platform.survey.gateway.GatewayResult;
import cn.mjy.platform.tenant.api.ConflictException;
import cn.mjy.platform.tenant.api.NotFoundException;
import cn.mjy.platform.tenant.routing.SurveyRouteService;
import java.util.List;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * 发布收尾（事务 2，持问卷行锁）。只有问卷仍处于 publishing 且当前 requestId 就是本次的，才记录结局；
 * 否则说明同一 requestId 的另一次调用已经收尾（陈旧核对与原调用都返回时），直接返回当前状态，不重复记录。
 * <ul>
 *   <li>成功：写不可变的已发布版本与三段映射，经租户模块登记公开路由（公开 UUID → 实例 → sid），审计——同一事务；
 *       已有在线版本时（重新发布，ADR 0012）不是登记而是<b>切换</b>路由：旧路由标记为被取代、新路由成为当前，
 *       旧版本记入待收口——仍是同一事务，所以路由要么还指向旧版、要么已指向完整落库的新版。
 *       绑定与请求对不上、或路由登记 / 切换失败时，整笔回滚并转为待核对（绝不留下"有版本没路由"的半成品，
 *       旧版照旧在线）；事务提交之后才收口旧的引擎问卷（{@link SupersededVersionCloser}），收口失败不影响结论；</li>
 *   <li>失败（422/502）与拒收（400/401/404）：记录失败阶段与原因，问卷回到可再次发布，不登记路由；
 *       网关报告孤儿问卷时大声记日志并存档；</li>
 *   <li>未知：待核对，下次发布用同一 requestId 重试。</li>
 * </ul>
 */
@Component
class PublishSettlement {

    static final String GATEWAY_STAGE = "gateway";

    private static final Logger log = LoggerFactory.getLogger(PublishSettlement.class);

    private final TenantScope tenantScope;
    private final SurveyRepository surveys;
    private final PublishAttemptRepository attempts;
    private final PublishedVersionRepository versions;
    private final SurveyRouteService routes;
    private final SurveyViews views;
    private final SurveyAudit audit;
    private final PublishApprovalGate approvalGate;
    private final VersionRetirementRepository retirements;
    private final SupersededVersionCloser closer;

    PublishSettlement(TenantScope tenantScope, SurveyRepository surveys, PublishAttemptRepository attempts,
            PublishedVersionRepository versions, SurveyRouteService routes, SurveyViews views, SurveyAudit audit,
            PublishApprovalGate approvalGate, VersionRetirementRepository retirements,
            SupersededVersionCloser closer) {
        this.tenantScope = tenantScope;
        this.surveys = surveys;
        this.attempts = attempts;
        this.versions = versions;
        this.routes = routes;
        this.views = views;
        this.audit = audit;
        this.approvalGate = approvalGate;
        this.retirements = retirements;
        this.closer = closer;
    }

    /** 成功结局无法落地（绑定不符、路由登记失败）：整笔回滚后改记为待核对。 */
    private static final class UnrecordablePublishException extends RuntimeException {

        UnrecordablePublishException(String message) {
            super(message);
        }
    }

    PublishOutcome settle(TenantContext ctx, Ticket ticket, GatewayOutcome outcome) {
        PublishOutcome settled = settleOrPend(ctx, ticket, outcome);
        if (settled.survey().status() == SurveyStatus.PUBLISHED) {
            closer.closeRetired(ctx.tenantId(), ticket.surveyId());
        }
        return settled;
    }

    private PublishOutcome settleOrPend(TenantContext ctx, Ticket ticket, GatewayOutcome outcome) {
        try {
            return tenantScope.call(ctx.tenantId(), () -> settleLocked(ctx, ticket, outcome));
        } catch (UnrecordablePublishException e) {
            log.error("survey {} was published by the gateway (request {}) but could not be recorded: {}; "
                    + "marked pending_reconciliation", ticket.surveyId(), ticket.requestId(), e.getMessage());
            GatewayOutcome unknown = new GatewayOutcome.Unknown("unrecordable: " + e.getMessage());
            return tenantScope.call(ctx.tenantId(), () -> settleLocked(ctx, ticket, unknown));
        }
    }

    private PublishOutcome settleLocked(TenantContext ctx, Ticket ticket, GatewayOutcome outcome) {
        SurveyRow row = surveys.lock(ticket.surveyId(), SurveyPublishService.STALE_PUBLISHING).orElseThrow();
        if (row.status() != SurveyStatus.PUBLISHING || !ticket.requestId().equals(row.currentRequestId())) {
            log.info("publish request {} for survey {} was already settled as {}",
                    ticket.requestId(), ticket.surveyId(), row.status().code());
            return current(row);
        }
        return switch (outcome) {
            case GatewayOutcome.Published published -> recordPublished(ctx, ticket, published.result(), row);
            case GatewayOutcome.Failed failed -> recordFailed(ctx, ticket, failed.httpStatus(),
                    failed.result().failedStage(), failed.result().failures(), failed.result().orphanSurveyId());
            case GatewayOutcome.Refused refused -> recordFailed(ctx, ticket, refused.httpStatus(),
                    GATEWAY_STAGE, List.of(refused.error()), null);
            case GatewayOutcome.Unknown unknown -> recordPending(ctx, ticket, unknown.reason());
        };
    }

    private PublishOutcome recordPublished(TenantContext ctx, Ticket ticket, GatewayResult result, SurveyRow row) {
        GatewayBinding binding = requireMatchingBinding(ticket, result.binding());
        int versionNo = versions.insert(ctx.tenantId(), ticket.surveyId(), ticket.requestId(), ticket.draftVersion(),
                ticket.definition(), binding, ctx.actorId());
        if (row.publishedVersion() == null) {
            registerRoute(ctx, ticket, binding);
        } else {
            switchRoute(ctx, ticket, binding, row.publishedVersion(), versionNo);
        }
        attempts.complete(ticket.requestId(), PublishAttemptRepository.PUBLISHED, 200, null, List.of(), null);
        surveys.markSettled(ticket.surveyId(), SurveyStatus.PUBLISHED, versionNo);
        approvalGate.onPublished(ctx, ticket.surveyId(), ticket.draftVersion(), ticket.requestId());
        audit.record(ctx, SurveyAudit.PUBLISH, ticket.surveyId(), "version=" + versionNo
                + " engine=" + binding.engineInstance() + " sid=" + binding.surveyId()
                + " fingerprint=" + binding.fingerprint() + " request=" + ticket.requestId());
        return new PublishOutcome(views.of(surveys.find(ticket.surveyId()).orElseThrow()),
                versions.find(ticket.surveyId(), versionNo).orElseThrow());
    }

    private void registerRoute(TenantContext ctx, Ticket ticket, GatewayBinding binding) {
        try {
            routes.registerPublished(ctx.tenantId(), ticket.surveyId(), binding.engineInstance(),
                    binding.surveyId(), ctx.actorId(), ctx.traceId());
        } catch (ConflictException | NotFoundException e) {
            throw new UnrecordablePublishException("route registration failed: " + e.getMessage());
        }
    }

    /** 重新发布：公开路由从在线版本切到新版本，在线版本记入待收口（同一事务）。 */
    private void switchRoute(TenantContext ctx, Ticket ticket, GatewayBinding binding, int liveVersion,
            int newVersion) {
        PublishedVersionView live = versions.find(ticket.surveyId(), liveVersion)
                .orElseThrow(() -> new UnrecordablePublishException("live version " + liveVersion + " is missing"));
        if (live.engineInstanceId().equals(binding.engineInstance()) && live.engineSid() == binding.surveyId()) {
            throw new UnrecordablePublishException("new version reuses the live engine survey sid=" + live.engineSid());
        }
        try {
            routes.switchPublished(ctx.tenantId(), ticket.surveyId(), live.engineInstanceId(), live.engineSid(),
                    binding.engineInstance(), binding.surveyId(), ctx.actorId(), ctx.traceId());
        } catch (ConflictException | NotFoundException e) {
            throw new UnrecordablePublishException("route switch failed: " + e.getMessage());
        }
        retirements.insert(ctx.tenantId(), ticket.surveyId(), liveVersion, newVersion, live.engineInstanceId(),
                live.engineSid());
        audit.record(ctx, SurveyAudit.ROUTE_SWITCH, ticket.surveyId(), "from=" + liveVersion + " sid="
                + live.engineSid() + " to=" + newVersion + " sid=" + binding.surveyId());
    }

    /** 绑定必须对应本次请求：同一实例、同一定义 UUID、有效 sid、题目 UUID 合法。 */
    private static GatewayBinding requireMatchingBinding(Ticket ticket, GatewayBinding binding) {
        if (binding == null) {
            throw new UnrecordablePublishException("published result carries no binding");
        }
        if (!ticket.engineInstanceId().equals(binding.engineInstance())
                || !ticket.surveyId().toString().equalsIgnoreCase(binding.definitionUuid())
                || binding.surveyId() <= 0) {
            throw new UnrecordablePublishException("binding does not match the request: instance="
                    + binding.engineInstance() + " definition=" + binding.definitionUuid() + " sid=" + binding.surveyId());
        }
        for (GatewayBinding.QuestionBinding question : binding.questions()) {
            try {
                UUID.fromString(question.uuid());
            } catch (IllegalArgumentException e) {
                throw new UnrecordablePublishException("binding has a malformed question uuid: " + question.uuid());
            }
        }
        return binding;
    }

    private PublishOutcome recordFailed(TenantContext ctx, Ticket ticket, int gatewayStatus, String failedStage,
            List<String> failures, Integer orphanSid) {
        if (orphanSid != null) {
            log.error("ORPHAN ENGINE SURVEY needs manual cleanup: tenant={} survey={} engine={} sid={} request={} "
                    + "failures={}", ctx.tenantId(), ticket.surveyId(), ticket.engineInstanceId(), orphanSid,
                    ticket.requestId(), failures);
        } else {
            log.warn("publish of survey {} failed at {} (http {}): {}",
                    ticket.surveyId(), failedStage, gatewayStatus, failures);
        }
        attempts.complete(ticket.requestId(), PublishAttemptRepository.FAILED, gatewayStatus, failedStage, failures,
                orphanSid);
        surveys.markSettled(ticket.surveyId(), SurveyStatus.PUBLISH_FAILED, null);
        audit.record(ctx, SurveyAudit.PUBLISH_FAILED, ticket.surveyId(), "request=" + ticket.requestId()
                + " status=" + gatewayStatus + " stage=" + failedStage
                + (orphanSid == null ? "" : " orphanSid=" + orphanSid));
        return new PublishOutcome(views.of(surveys.find(ticket.surveyId()).orElseThrow()), null);
    }

    private PublishOutcome recordPending(TenantContext ctx, Ticket ticket, String reason) {
        log.warn("publish of survey {} has an unknown outcome (request {}): {}; retry reuses the same request id",
                ticket.surveyId(), ticket.requestId(), reason);
        attempts.complete(ticket.requestId(), PublishAttemptRepository.UNKNOWN, null, null, List.of(reason), null);
        surveys.markSettled(ticket.surveyId(), SurveyStatus.PENDING_RECONCILIATION, null);
        audit.record(ctx, SurveyAudit.PUBLISH_PENDING, ticket.surveyId(), "request=" + ticket.requestId());
        return new PublishOutcome(views.of(surveys.find(ticket.surveyId()).orElseThrow()), null);
    }

    private PublishOutcome current(SurveyRow row) {
        PublishedVersionView version = row.status() == SurveyStatus.PUBLISHED && row.publishedVersion() != null
                ? versions.find(row.id(), row.publishedVersion()).orElse(null)
                : null;
        return new PublishOutcome(views.of(row), version);
    }
}
