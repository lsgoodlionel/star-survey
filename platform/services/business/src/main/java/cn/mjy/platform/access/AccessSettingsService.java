package cn.mjy.platform.access;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import org.springframework.stereotype.Service;

/** 租户访问设置：目前是发布审核开关（WP-01：子账户发布需审核，开关按租户可配，默认开启）。 */
@Service
public class AccessSettingsService {

    private final TenantScope tenantScope;
    private final AccessDecisionService access;
    private final AccessSettingsRepository settings;
    private final AccessAudit audit;

    AccessSettingsService(TenantScope tenantScope, AccessDecisionService access, AccessSettingsRepository settings,
            AccessAudit audit) {
        this.tenantScope = tenantScope;
        this.access = access;
        this.settings = settings;
        this.audit = audit;
    }

    public boolean isPublishApprovalRequired(TenantId tenant) {
        return tenantScope.call(tenant, () -> settings.publishApprovalRequired(tenant));
    }

    /** 需要租户级"管理设置"权限；变更进审计。 */
    public void setPublishApprovalRequired(TenantContext ctx, boolean required) {
        tenantScope.run(ctx.tenantId(), () -> {
            access.require(ctx, Permission.MANAGE_SETTINGS, null);
            settings.setPublishApprovalRequired(ctx.tenantId(), required, ctx.actorId());
            audit.record(ctx, AccessAudit.SETTINGS_PUBLISH_APPROVAL, "tenant-settings publish_approval_required=" + required);
        });
    }
}
