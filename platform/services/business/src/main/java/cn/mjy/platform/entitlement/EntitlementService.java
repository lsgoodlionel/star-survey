package cn.mjy.platform.entitlement;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.time.Instant;
import org.springframework.stereotype.Service;

/**
 * 额度判定入口：某租户此刻能否使用某能力若干单位。
 *
 * 判定只读、不占额度，适合界面置灰与提前提示；真正消耗额度的动作必须走
 * {@link UsageLedger#reserve}，那里在同一事务内重新判定并原子预占。
 */
@Service
public class EntitlementService {

    private final TenantScope tenantScope;
    private final EntitlementEvaluator evaluator;

    public EntitlementService(TenantScope tenantScope, EntitlementEvaluator evaluator) {
        this.tenantScope = tenantScope;
        this.evaluator = evaluator;
    }

    /** 按租户累计或不计量的能力。按问卷累计的能力请用带 subject 的重载。 */
    public Decision decide(TenantContext context, String capability, long quantity) {
        return decide(context, capability, null, quantity);
    }

    /** {@code subject} 为问卷标识，仅对按问卷累计的计量（如有效完成答卷）有意义。 */
    public Decision decide(TenantContext context, String capability, String subject, long quantity) {
        return tenantScope.call(context.tenantId(),
                () -> evaluator.evaluate(capability, subject, quantity, Instant.now()).decision());
    }
}
