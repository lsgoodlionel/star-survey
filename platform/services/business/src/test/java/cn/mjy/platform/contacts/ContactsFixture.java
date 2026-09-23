package cn.mjy.platform.contacts;

import cn.mjy.platform.access.AccessFixture;
import cn.mjy.platform.access.GrantRequest;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import java.util.UUID;
import org.springframework.stereotype.Component;

/** 通讯录测试夹具：开通租户、建名单与部门、把某人变成"部门管理员"。全部走公开服务方法。 */
@Component
public class ContactsFixture {

    /** 组织管理员：租户级 manage-settings ＋ manage-members → 全租户通讯录管理员。 */
    public static final String TENANT_ADMIN_ROLE = "org_admin";

    /** 项目管理者：有 manage-members、没有 manage-settings → 只能按范围行看部门（部门管理员）。 */
    public static final String DEPARTMENT_ADMIN_ROLE = "project_manager";

    private final AccessFixture access;
    private final ContactListService lists;
    private final OrgUnitService units;
    private final ContactDirectoryService contacts;
    private final ContactScopeService scopes;

    public ContactsFixture(AccessFixture access, ContactListService lists, OrgUnitService units,
            ContactDirectoryService contacts, ContactScopeService scopes) {
        this.access = access;
        this.lists = lists;
        this.units = units;
        this.contacts = contacts;
        this.scopes = scopes;
    }

    /** 新租户的所有者上下文：持有全部权限且没有范围行，因此看得到全租户联系人。 */
    public TenantContext newTenant() {
        return access.newTenant();
    }

    public TenantId tenantOf(TenantContext ctx) {
        return ctx.tenantId();
    }

    public ContactListView list(TenantContext owner, String name, ContactKind kind, DedupeKey key) {
        return lists.create(owner, name, kind, key);
    }

    public OrgUnitView unit(TenantContext owner, UUID parentId, String name) {
        return units.create(owner, parentId, name, null);
    }

    public OrgUnitView unitWithRef(TenantContext owner, UUID parentId, String name, String externalRef) {
        return units.create(owner, parentId, name, externalRef);
    }

    /** 全租户通讯录管理员：租户级 manage-settings，看得到全部联系人。 */
    public TenantContext tenantAdmin(TenantContext owner, String actor) {
        return access.member(owner, actor, TENANT_ADMIN_ROLE, null);
    }

    /** 部门管理员：租户级 manage-members（无 manage-settings）＋ 一条部门范围行。 */
    public TenantContext departmentAdmin(TenantContext owner, String actor, UUID unitId) {
        TenantContext admin = access.member(owner, actor, DEPARTMENT_ADMIN_ROLE, null);
        scopes.grant(owner, actor, unitId, null);
        return admin;
    }

    /** 没有 manage-members 的在职成员。 */
    public TenantContext plainMember(TenantContext owner, String actor) {
        return access.member(owner, actor, "statistics_viewer", null);
    }

    public ContactView contact(TenantContext ctx, UUID listId, ContactKind kind, ContactDraft draft) {
        return contacts.create(ctx, listId, kind, draft);
    }
}
