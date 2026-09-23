package cn.mjy.platform.delivery;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.time.Clock;
import java.time.Instant;
import java.util.UUID;
import org.springframework.stereotype.Service;

/**
 * 应答者完成记录的登记入口（R05-06"未完成人员催答不误发已完成者"的事实来源）。
 *
 * <p>两条来源：
 * <ul>
 *   <li>{@link CompletionReconciler} 按引擎答卷投影对账（需要 {@link RespondentIdentityResolver}）；</li>
 *   <li>集成方经接口显式登记（例如自建的作答回调）。</li>
 * </ul>
 * 两条都落到同一张表，催答只看这张表。记录只追加：一个人完成过就不会再被催。
 */
@Service
public class CompletionService {

    static final String SOURCE_PROJECTION = "projection";
    static final String SOURCE_API = "api";

    private final TenantScope tenantScope;
    private final CompletionRepository completions;
    private final DeliveryAccess access;
    private final DeliveryAudit audit;
    private final Clock clock;

    CompletionService(TenantScope tenantScope, CompletionRepository completions, DeliveryAccess access,
            DeliveryAudit audit) {
        this.tenantScope = tenantScope;
        this.completions = completions;
        this.access = access;
        this.audit = audit;
        this.clock = Clock.systemUTC();
    }

    /** 经接口登记一次完成；重复登记保留首次的时刻并返回 false。 */
    public boolean record(TenantContext ctx, UUID surveyId, String respondentKey, Instant completedAt) {
        access.require(ctx, DeliveryAccess.SEND, surveyId);
        requireKey(respondentKey);
        Instant at = completedAt == null ? clock.instant() : completedAt;
        return tenantScope.call(ctx.tenantId(), () -> {
            boolean recorded = completions.record(ctx.tenantId(), surveyId, respondentKey, at, SOURCE_API);
            if (recorded) {
                // 审计里只记应答者标识（邀请码或随机键），不记地址。
                audit.record(ctx, DeliveryAudit.COMPLETION, "survey/" + surveyId + " respondent=" + respondentKey);
            }
            return recorded;
        });
    }

    /** 对账用的内部登记；须在租户作用域内调用。 */
    boolean recordFromProjection(TenantId tenant, UUID surveyId, String respondentKey, Instant completedAt) {
        return completions.record(tenant, surveyId, respondentKey, completedAt, SOURCE_PROJECTION);
    }

    public boolean isCompleted(TenantContext ctx, UUID surveyId, String respondentKey) {
        access.require(ctx, DeliveryAccess.READ, surveyId);
        return tenantScope.call(ctx.tenantId(), () -> completions.isCompleted(surveyId, respondentKey));
    }

    private static void requireKey(String respondentKey) {
        if (respondentKey == null || respondentKey.isBlank() || respondentKey.length() > 64) {
            throw DeliveryExceptions.invalid("respondentKey must be 1 to 64 characters");
        }
    }
}
