package cn.mjy.platform.identity.org;

import cn.mjy.platform.access.AccessDecisionService;
import cn.mjy.platform.access.MemberService;
import cn.mjy.platform.access.Permission;
import cn.mjy.platform.audit.AuditLogRepository;
import cn.mjy.platform.identity.IdentityBindingService;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.tenant.api.NotFoundException;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.function.Function;
import java.util.stream.Collectors;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * 组织同步最小切片：离职后立即失权（R18-01）。
 *
 * <p>两个入口走同一撤权路径 {@link #revoke}：
 * <ul>
 *   <li>拉取：{@link #sync} 逐个核对该组织下仍有效的绑定，开放平台明确答复"已离职 / 已禁用 / 不存在"才撤；</li>
 *   <li>推送：{@link #memberDeparted} 供通讯录事件回调（企业微信 change_contact、钉钉 user_leave_org、
 *       飞书 contact.user.deleted_v3）接入后调用。</li>
 * </ul>
 * 撤权 = 记绑定撤销 + 撤销其全部会话（令牌在下一个请求即被拒）+ 移除成员并释放席位。
 * 查询失败或无法判定时不撤（计入 unknown），避免一次开放平台故障把全员踢下线。
 */
@Service
public class OrgDirectorySyncService {

    private static final Logger log = LoggerFactory.getLogger(OrgDirectorySyncService.class);

    static final String ACTION_REVOKE = "identity.binding.revoke";
    static final String SYSTEM_ACTOR = "system:org_sync";

    /** checked：核对的有效绑定数；revoked：本次撤销数；unknown：无法判定、未处理的数。 */
    public record SyncResult(int checked, int revoked, int unknown) {
    }

    private final TenantScope tenantScope;
    private final AccessDecisionService access;
    private final OrgConnectionService connections;
    private final OrgBindingRepository orgBindings;
    private final OrgSessionRepository sessions;
    private final IdentityBindingService bindings;
    private final MemberService members;
    private final SecretResolver secrets;
    private final AuditLogRepository audit;
    private final Map<OrgProvider, OrgPlatformClient> clients;

    OrgDirectorySyncService(TenantScope tenantScope, AccessDecisionService access, OrgConnectionService connections,
            OrgBindingRepository orgBindings, OrgSessionRepository sessions, IdentityBindingService bindings,
            MemberService members, SecretResolver secrets, AuditLogRepository audit, List<OrgPlatformClient> clients) {
        this.tenantScope = tenantScope;
        this.access = access;
        this.connections = connections;
        this.orgBindings = orgBindings;
        this.sessions = sessions;
        this.bindings = bindings;
        this.members = members;
        this.secrets = secrets;
        this.audit = audit;
        this.clients = clients.stream().collect(Collectors.toUnmodifiableMap(OrgPlatformClient::provider,
                Function.identity()));
    }

    /** 管理员触发的拉取同步；需要 manage-members。 */
    public SyncResult sync(TenantContext ctx, UUID connectionId) {
        access.require(ctx, Permission.MANAGE_MEMBERS, null);
        TenantId tenant = ctx.tenantId();
        OrgConnection connection = connections.find(tenant, connectionId)
                .orElseThrow(() -> new NotFoundException("organisation connection not found"));
        String secret = secrets.resolve(tenant, connection.secretRef())
                .orElseThrow(() -> OrgLoginException.notConfigured("the connection secret"));
        OrgPlatformClient client = clients.get(connection.provider());
        List<OrgBindingRepository.ActiveBinding> active = tenantScope.call(tenant,
                () -> orgBindings.listActive(connection.provider().code(), connection.corpId()));
        int revoked = 0;
        int unknown = 0;
        for (OrgBindingRepository.ActiveBinding binding : active) {
            OrgPlatformClient.DirectoryStatus status = client.lookup(connection, secret, binding.externalId());
            if (status == OrgPlatformClient.DirectoryStatus.DEPARTED
                    && revoke(tenant, binding.principalId(), ctx.actorId(), ctx.traceId())) {
                revoked++;
            } else if (status == OrgPlatformClient.DirectoryStatus.UNKNOWN) {
                unknown++;
            }
        }
        return new SyncResult(active.size(), revoked, unknown);
    }

    /**
     * 开放平台报告某成员离职（事件推送入口，调用方负责验签与解密事件）。返回是否为本次新撤销；
     * 连接或绑定不存在时为假。
     */
    public boolean memberDeparted(TenantId tenant, UUID connectionId, String externalId, String traceId) {
        return connections.find(tenant, connectionId)
                .flatMap(connection -> bindings.findByIdentity(tenant, connection.identityOf(externalId)))
                .map(binding -> revoke(tenant, binding.principalId(), SYSTEM_ACTOR, traceId))
                .orElse(false);
    }

    private boolean revoke(TenantId tenant, UUID principal, String actor, String traceId) {
        boolean newlyRevoked = tenantScope.call(tenant, () -> {
            boolean inserted = orgBindings.revoke(tenant, principal, OrgBindingRepository.REASON_DEPARTED, actor);
            int closed = sessions.revokeAllOf(principal, OrgSessionRepository.REASON_DEPARTED);
            if (inserted) {
                audit.record(tenant, actor, ACTION_REVOKE,
                        "principal/" + principal + " reason=departed sessions_revoked=" + closed, traceId);
            }
            return inserted;
        });
        if (!members.removeBySystem(tenant, principal.toString(), traceId)) {
            log.warn("departed principal {} in tenant {} is the sole tenant owner; membership kept, sessions revoked",
                    principal, tenant);
        }
        return newlyRevoked;
    }
}
