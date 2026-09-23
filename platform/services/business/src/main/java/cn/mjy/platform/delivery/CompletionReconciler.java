package cn.mjy.platform.delivery;

import cn.mjy.platform.engine.ResponseProjection;
import cn.mjy.platform.engine.ResponseProjectionQuery;
import cn.mjy.platform.engine.ResponseState;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.tenant.routing.SurveyRoute;
import cn.mjy.platform.tenant.routing.SurveyRouteService;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.stereotype.Component;

/**
 * 把引擎答卷投影里新出现的"已完成"答卷对账成应答者完成记录，供催答判断使用。
 *
 * <p>只读引擎模块的公开查询 {@link ResponseProjectionQuery}，不直接读它的表（CONVENTIONS）。
 * 按 (代次, 答卷号) 键集分页，游标存在 delivery_completion_cursor 里，每轮只看新增的部分。
 *
 * <p>投影里没有参与者 token，所以"这份答卷是谁交的"要问 {@link RespondentIdentityResolver}。
 * 没有注册实现时本对账什么也不做（只在第一次提示一句），催答改为只依据显式登记的完成记录——
 * 这是 ADR 0016 已经记在案的缺口，不在这里假装解决。
 */
@Component
class CompletionReconciler {

    /** 每个问卷每轮最多对账多少份答卷。 */
    private static final int PAGE = 200;

    private static final Logger log = LoggerFactory.getLogger(CompletionReconciler.class);

    private final TenantScope tenantScope;
    private final ReminderRuleRepository rules;
    private final SurveyRouteService routes;
    private final ResponseProjectionQuery projections;
    private final CompletionCursorRepository cursors;
    private final CompletionService completions;
    private final ObjectProvider<RespondentIdentityResolver> resolvers;

    private volatile boolean warnedAboutMissingResolver;

    CompletionReconciler(TenantScope tenantScope, ReminderRuleRepository rules, SurveyRouteService routes,
            ResponseProjectionQuery projections, CompletionCursorRepository cursors, CompletionService completions,
            ObjectProvider<RespondentIdentityResolver> resolvers) {
        this.tenantScope = tenantScope;
        this.rules = rules;
        this.routes = routes;
        this.projections = projections;
        this.cursors = cursors;
        this.completions = completions;
        this.resolvers = resolvers;
    }

    /** 对账该租户里所有"有启用中的催答规则"的问卷；返回新登记的完成条数。 */
    int runOnce(TenantId tenant) {
        RespondentIdentityResolver resolver = resolvers.getIfAvailable();
        if (resolver == null) {
            warnOnce();
            return 0;
        }
        List<UUID> surveys = tenantScope.call(tenant, rules::surveysWithEnabledRules);
        int recorded = 0;
        for (UUID surveyId : surveys) {
            recorded += reconcile(tenant, surveyId, resolver);
        }
        return recorded;
    }

    private int reconcile(TenantId tenant, UUID surveyId, RespondentIdentityResolver resolver) {
        Optional<SurveyRoute> route = routes.findByPublicId(tenant, surveyId);
        if (route.isEmpty()) {
            return 0;
        }
        return tenantScope.call(tenant, () -> {
            ResponseProjectionQuery.Position after = cursors.find(surveyId).orElse(null);
            List<ResponseProjection> page = projections.page(route.get().engineInstanceId(),
                    route.get().engineSid(), ResponseState.ENGINE_COMPLETED, after, PAGE);
            int recorded = 0;
            ResponseProjection last = null;
            for (ResponseProjection response : page) {
                last = response;
                Optional<String> key = resolver.respondentKeyOf(tenant, response);
                if (key.isPresent() && completions.recordFromProjection(tenant, surveyId, key.get(),
                        response.completedAt() == null ? response.firstEventAt() : response.completedAt())) {
                    recorded++;
                }
            }
            if (last != null) {
                cursors.save(tenant, surveyId, last.key().generation(), last.key().responseId());
            }
            return recorded;
        });
    }

    private void warnOnce() {
        if (!warnedAboutMissingResolver) {
            warnedAboutMissingResolver = true;
            log.info("no RespondentIdentityResolver is registered; completion reconciliation from the engine "
                    + "response projection is inactive (see ADR 0016: invitation codes must be read back from "
                    + "the engine after publishing). Reminders will only honour explicitly recorded completions.");
        }
    }
}
