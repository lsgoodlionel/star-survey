package cn.mjy.platform.access;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.util.Objects;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;

/**
 * 资源树登记：项目、文件夹、问卷由各自所属模块创建后在此登记，授权才能落在上面并向下继承。
 *
 * <p>这是给其他模块调用的内部接口：调用方负责先判定"能否在父节点下创建"
 * （例如 {@code can(ctx, EDIT, parent)}），本方法只保证树结构合法且不跨租户。
 * 节点只能挂在已存在的父节点下；改名与移动由 {@link ResourceTreeService} 负责（移动时锁根行并拒绝成环，
 * V402 的触发器兜底），因此树不会成环，继承计算总是确定的。
 */
@Service
public class AccessResources {

    private final TenantScope tenantScope;
    private final JdbcClient jdbc;

    public AccessResources(TenantScope tenantScope, JdbcClient jdbc) {
        this.tenantScope = tenantScope;
        this.jdbc = jdbc;
    }

    public UUID register(TenantId tenant, UUID id, ResourceKind kind, UUID parentId, String name) {
        Objects.requireNonNull(id, "id");
        Objects.requireNonNull(kind, "kind");
        if (name == null || name.isBlank()) {
            throw new IllegalArgumentException("resource name is required");
        }
        return tenantScope.call(tenant, () -> {
            requireValidParent(tenant, kind, parentId);
            jdbc.sql("""
                    INSERT INTO access_resource (tenant_id, id, kind, parent_id, name)
                    VALUES (:tenant, :id, :kind, :parent, :name)
                    """)
                    .param("tenant", tenant.value())
                    .param("id", id)
                    .param("kind", kind.code())
                    .param("parent", parentId)
                    .param("name", name)
                    .update();
            return id;
        });
    }

    /** 当前作用域内的资源类型；不存在或属于别的租户时为空。须在租户作用域内调用。 */
    Optional<ResourceKind> kindOf(TenantId tenant, UUID id) {
        return jdbc.sql("SELECT kind FROM access_resource WHERE tenant_id = :tenant AND id = :id")
                .param("tenant", tenant.value())
                .param("id", id)
                .query(String.class)
                .optional()
                .map(ResourceKind::fromCode);
    }

    private void requireValidParent(TenantId tenant, ResourceKind kind, UUID parentId) {
        if (kind == ResourceKind.PROJECT) {
            if (parentId != null) {
                throw new IllegalArgumentException("a project cannot have a parent");
            }
            return;
        }
        if (parentId == null) {
            throw new IllegalArgumentException(kind.code() + " requires a parent");
        }
        ResourceKind parentKind = kindOf(tenant, parentId)
                .orElseThrow(() -> new IllegalArgumentException("unknown parent resource: " + parentId));
        if (!parentKind.canContainChildren()) {
            throw new IllegalArgumentException("a " + parentKind.code() + " cannot contain children");
        }
    }
}
