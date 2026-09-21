package cn.mjy.platform.access;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.springframework.stereotype.Component;

/** 测试夹具：开通一个带所有者的租户、建资源树、邀请成员并授权。全部走公开服务方法。 */
@Component
public class AccessFixture {

    public static final String OWNER = "owner";

    private final AccessResources resources;
    private final MemberService members;
    private final GrantService grants;

    public AccessFixture(AccessResources resources, MemberService members, GrantService grants) {
        this.resources = resources;
        this.members = members;
        this.grants = grants;
    }

    public MemberService members() {
        return members;
    }

    public GrantService grants() {
        return grants;
    }

    /** 新租户，返回所有者的上下文。 */
    public TenantContext newTenant() {
        TenantId tenant = TenantId.random();
        members.bootstrapOwner(tenant, OWNER, "trace-bootstrap");
        return context(tenant, OWNER);
    }

    public static TenantContext context(TenantId tenant, String actor) {
        return new TenantContext(tenant, actor, List.of(), "trace-" + actor);
    }

    public UUID project(TenantId tenant) {
        return resources.register(tenant, UUID.randomUUID(), ResourceKind.PROJECT, null, "项目");
    }

    public UUID folder(TenantId tenant, UUID parent) {
        return resources.register(tenant, UUID.randomUUID(), ResourceKind.FOLDER, parent, "文件夹");
    }

    public UUID survey(TenantId tenant, UUID parent) {
        return resources.register(tenant, UUID.randomUUID(), ResourceKind.SURVEY, parent, "问卷");
    }

    /** 由所有者邀请、成员接受邀请，再按角色与范围授权；返回该成员的上下文。 */
    public TenantContext member(TenantContext owner, String actor, String role, UUID resource) {
        return member(owner, actor, role, resource, null);
    }

    public TenantContext member(TenantContext owner, String actor, String role, UUID resource, Instant expiresAt) {
        TenantContext member = activeMember(owner, actor);
        grants.grant(owner, new GrantRequest(actor, role, resource, expiresAt));
        return member;
    }

    public TenantContext activeMember(TenantContext owner, String actor) {
        members.invite(owner, actor, MemberKind.STAFF);
        TenantContext member = context(owner.tenantId(), actor);
        members.acceptInvitation(member);
        return member;
    }
}
