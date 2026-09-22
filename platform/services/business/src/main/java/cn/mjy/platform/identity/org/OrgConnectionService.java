package cn.mjy.platform.identity.org;

import cn.mjy.platform.access.AccessDecisionService;
import cn.mjy.platform.access.MemberKind;
import cn.mjy.platform.access.MemberService;
import cn.mjy.platform.access.Permission;
import cn.mjy.platform.audit.AuditLogRepository;
import cn.mjy.platform.identity.IdentityBinding;
import cn.mjy.platform.identity.IdentityBindingService;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.tenant.api.ConflictException;
import cn.mjy.platform.tenant.api.CreateResult;
import cn.mjy.platform.tenant.api.NotFoundException;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.stereotype.Service;

/**
 * 组织连接的管理（租户管理员，manage-settings）与成员预授权（manage-members）。全部写审计；
 * 审计与响应里只有密钥名，没有密钥值。
 */
@Service
public class OrgConnectionService {

    static final String ACTION_CREATE = "identity.org_connection.create";
    static final String ACTION_UPDATE = "identity.org_connection.update";
    static final String ACTION_PREAUTHORIZE = "identity.org_connection.preauthorize";

    record CreateCommand(OrgProvider provider, String corpId, String appId, String secretRef,
            UnknownUserPolicy unknownUserPolicy) {
    }

    record UpdateCommand(String secretRef, UnknownUserPolicy unknownUserPolicy, Boolean enabled) {
    }

    private final TenantScope tenantScope;
    private final AccessDecisionService access;
    private final OrgConnectionRepository connections;
    private final OrgBindingRepository orgBindings;
    private final IdentityBindingService bindings;
    private final MemberService members;
    private final AuditLogRepository audit;

    OrgConnectionService(TenantScope tenantScope, AccessDecisionService access, OrgConnectionRepository connections,
            OrgBindingRepository orgBindings, IdentityBindingService bindings, MemberService members,
            AuditLogRepository audit) {
        this.tenantScope = tenantScope;
        this.access = access;
        this.connections = connections;
        this.orgBindings = orgBindings;
        this.bindings = bindings;
        this.members = members;
        this.audit = audit;
    }

    OrgConnection create(TenantContext ctx, CreateCommand command) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.require(ctx, Permission.MANAGE_SETTINGS, null);
            OrgConnection created = connections.insert(new OrgConnection(UUID.randomUUID(), ctx.tenantId(),
                    command.provider(), command.corpId(), command.appId(), command.secretRef(),
                    command.unknownUserPolicy(), true), ctx.actorId());
            audit.record(ctx.tenantId(), ctx.actorId(), ACTION_CREATE, describe(created), ctx.traceId());
            return created;
        });
    }

    List<OrgConnection> list(TenantContext ctx) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.require(ctx, Permission.MANAGE_SETTINGS, null);
            return connections.list();
        });
    }

    OrgConnection get(TenantContext ctx, UUID id) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.require(ctx, Permission.MANAGE_SETTINGS, null);
            return connections.find(id).orElseThrow(OrgConnectionService::notFound);
        });
    }

    OrgConnection update(TenantContext ctx, UUID id, UpdateCommand command) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.require(ctx, Permission.MANAGE_SETTINGS, null);
            OrgConnection current = connections.find(id).orElseThrow(OrgConnectionService::notFound);
            OrgConnection updated = connections.update(new OrgConnection(current.id(), current.tenantId(),
                    current.provider(), current.corpId(), current.appId(),
                    Optional.ofNullable(command.secretRef()).orElse(current.secretRef()),
                    Optional.ofNullable(command.unknownUserPolicy()).orElse(current.unknownUserPolicy()),
                    Optional.ofNullable(command.enabled()).orElse(current.enabled())));
            audit.record(ctx.tenantId(), ctx.actorId(), ACTION_UPDATE, describe(updated), ctx.traceId());
            return updated;
        });
    }

    /**
     * 预授权组织成员：绑定其组织身份，并按员工邀请（占一个席位）；首次免登时邀请自动生效。
     * 需要 manage-members。已撤销（离职）的身份不能预授权——撤销只追加，复职需另行处理（见 ADR 0014）。
     */
    CreateResult<IdentityBinding> preauthorize(TenantContext ctx, UUID connectionId, String externalId) {
        access.require(ctx, Permission.MANAGE_MEMBERS, null);
        OrgConnection connection = tenantScope.call(ctx.tenantId(),
                () -> connections.find(connectionId).orElseThrow(OrgConnectionService::notFound));
        CreateResult<IdentityBinding> bound = bindings.bind(ctx.tenantId(), connection.identityOf(externalId),
                ctx.actorId(), ctx.traceId());
        UUID principal = bound.value().principalId();
        if (tenantScope.call(ctx.tenantId(), () -> orgBindings.isRevoked(principal))) {
            throw new ConflictException("this organisation identity has been revoked");
        }
        members.invite(ctx, principal.toString(), MemberKind.STAFF);
        tenantScope.run(ctx.tenantId(), () -> audit.record(ctx.tenantId(), ctx.actorId(), ACTION_PREAUTHORIZE,
                "principal/" + principal + " connection/" + connectionId, ctx.traceId()));
        return bound;
    }

    /** 免登与同步使用：不做权限判定（调用方是系统流程），只在租户作用域内读取。 */
    Optional<OrgConnection> find(TenantId tenant, UUID id) {
        return tenantScope.call(tenant, () -> connections.find(id));
    }

    private static String describe(OrgConnection c) {
        return "org_connection/" + c.id() + " provider=" + c.provider().code() + " corp=" + c.corpId()
                + " app=" + c.appId() + " secret_ref=" + c.secretRef() + " policy=" + c.unknownUserPolicy().code()
                + " enabled=" + c.enabled();
    }

    private static NotFoundException notFound() {
        return new NotFoundException("organisation connection not found");
    }
}
