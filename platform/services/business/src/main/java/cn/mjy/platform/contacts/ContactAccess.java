package cn.mjy.platform.contacts;

import cn.mjy.platform.access.AccessDecisionService;
import cn.mjy.platform.access.Decision;
import cn.mjy.platform.access.Permission;
import cn.mjy.platform.shared.TenantContext;
import java.util.Objects;
import org.springframework.stereotype.Component;

/**
 * 通讯录与 access 模块的唯一接点（ADR 0017 决定 4）。能不能碰通讯录完全由 {@link AccessDecisionService}
 * 判定，这里只把判定结果翻译成数据范围：
 * <ul>
 *   <li>租户级 {@code manage-settings} → 全租户联系人；</li>
 *   <li>否则租户级 {@code manage-members} → 只看范围行指定的部门及其子孙（没有范围行就什么都看不到）；</li>
 *   <li>两者都没有 → 拒绝，原因码沿用 access 的判定。</li>
 * </ul>
 */
@Component
class ContactAccess {

    private final AccessDecisionService access;

    ContactAccess(AccessDecisionService access) {
        this.access = access;
    }

    /** 读写通讯录的范围；无权时抛出，原因码来自 access。 */
    ContactScope scopeOf(TenantContext ctx) {
        Objects.requireNonNull(ctx, "ctx");
        if (access.canInTenant(ctx, Permission.MANAGE_SETTINGS).allowed()) {
            return ContactScope.wholeTenant(ctx.actorId());
        }
        Decision manage = access.canInTenant(ctx, Permission.MANAGE_MEMBERS);
        if (manage.allowed()) {
            return ContactScope.departments(ctx.actorId());
        }
        throw new ContactAccessDeniedException(manage.reason(), "contacts denied: " + manage.detail());
    }

    /** 只有租户管理员能做的事：建名单、建根部门、授予部门范围。部门管理员不能自己扩大范围。 */
    void requireTenantAdmin(TenantContext ctx) {
        Decision decision = access.canInTenant(ctx, Permission.MANAGE_SETTINGS);
        if (!decision.allowed()) {
            throw new ContactAccessDeniedException(decision.reason(),
                    "manage-settings denied: " + decision.detail());
        }
    }
}
