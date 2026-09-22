package cn.mjy.platform.identity.org;

import cn.mjy.platform.access.AccessDecisionService;
import cn.mjy.platform.access.MemberKind;
import cn.mjy.platform.access.MemberService;
import cn.mjy.platform.access.Permission;
import cn.mjy.platform.access.SeatLimitExceededException;
import cn.mjy.platform.audit.AuditLogRepository;
import cn.mjy.platform.identity.IdentityBinding;
import cn.mjy.platform.identity.IdentityBindingService;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantDirectory;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.tenant.api.ConflictException;
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
 * 组织同步：离职后立即失权（R18-01），以及管理员恢复。
 *
 * <p>三个入口走同一撤权路径 {@link #revoke}：
 * <ul>
 *   <li>拉取：{@link #sync}（管理员按连接触发）与 {@link #runScheduledRound}（定时，逐个未关闭租户的启用连接）。
 *       按 (建立时间, 主体) 键集分页核对仍有效的绑定，开放平台明确答复"已离职 / 已禁用 / 不存在"才撤；</li>
 *   <li>推送：{@link #memberDeparted}，由通讯录事件回调（{@link OrgEventService}）在验签解密后调用。</li>
 * </ul>
 * 撤权 = 记绑定撤销 + 撤销其全部会话（令牌在下一个请求即被拒）+ 移除成员并释放席位。
 * 查询失败或无法判定时不撤（计入 unknown），避免一次开放平台故障把全员踢下线。
 * 恢复 {@link #reinstate}：管理员（manage-members）重新邀请并解除撤销，逐次审计。
 */
@Service
public class OrgDirectorySyncService {

    private static final Logger log = LoggerFactory.getLogger(OrgDirectorySyncService.class);

    static final String ACTION_REVOKE = "identity.binding.revoke";
    static final String ACTION_REINSTATE = "identity.binding.reinstate";
    static final String SYSTEM_ACTOR = "system:org_sync";

    /** checked：核对的有效绑定数；revoked：本次撤销数；unknown：无法判定、未处理的数。 */
    public record SyncResult(int checked, int revoked, int unknown) {

        static final SyncResult NONE = new SyncResult(0, 0, 0);

        SyncResult plus(SyncResult other) {
            return new SyncResult(checked + other.checked, revoked + other.revoked, unknown + other.unknown);
        }
    }

    private final TenantScope tenantScope;
    private final TenantDirectory tenants;
    private final AccessDecisionService access;
    private final OrgConnectionService connections;
    private final OrgBindingRepository orgBindings;
    private final OrgSessionRepository sessions;
    private final IdentityBindingService bindings;
    private final MemberService members;
    private final SecretResolver secrets;
    private final AuditLogRepository audit;
    private final OrgSyncProperties properties;
    private final Map<OrgProvider, OrgPlatformClient> clients;

    OrgDirectorySyncService(TenantScope tenantScope, TenantDirectory tenants, AccessDecisionService access,
            OrgConnectionService connections, OrgBindingRepository orgBindings, OrgSessionRepository sessions,
            IdentityBindingService bindings, MemberService members, SecretResolver secrets, AuditLogRepository audit,
            OrgSyncProperties properties, List<OrgPlatformClient> clients) {
        this.tenantScope = tenantScope;
        this.tenants = tenants;
        this.access = access;
        this.connections = connections;
        this.orgBindings = orgBindings;
        this.sessions = sessions;
        this.bindings = bindings;
        this.members = members;
        this.secrets = secrets;
        this.audit = audit;
        this.properties = properties;
        this.clients = clients.stream().collect(Collectors.toUnmodifiableMap(OrgPlatformClient::provider,
                Function.identity()));
    }

    /** 管理员触发的拉取同步；需要 manage-members。 */
    public SyncResult sync(TenantContext ctx, UUID connectionId) {
        access.require(ctx, Permission.MANAGE_MEMBERS, null);
        OrgConnection connection = connections.find(ctx.tenantId(), connectionId)
                .orElseThrow(() -> new NotFoundException("organisation connection not found"));
        return syncConnection(connection, ctx.actorId(), ctx.traceId());
    }

    /**
     * 定时全量同步一轮：逐个未关闭的租户、逐个启用的连接。单个连接失败（如密钥未配置、开放平台不可用）
     * 只记日志、不影响其他连接。返回全部连接的合计。
     */
    public SyncResult runScheduledRound() {
        SyncResult total = SyncResult.NONE;
        for (TenantId tenant : tenants.openTenants()) {
            for (OrgConnection connection : connections.listForSystem(tenant)) {
                if (!connection.enabled()) {
                    continue;
                }
                try {
                    total = total.plus(syncConnection(connection, SYSTEM_ACTOR, UUID.randomUUID().toString()));
                } catch (RuntimeException e) {
                    log.warn("scheduled sync skipped connection {} in tenant {}: {}", connection.id(), tenant,
                            e.getMessage());
                }
            }
        }
        return total;
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

    /**
     * 管理员恢复一个被撤销的组织身份（复职、误判）：需要 manage-members。先按员工重新邀请（占席位，首次免登时生效；
     * 没有空闲席位则 409 且什么都不改），再在撤销上盖恢复戳并审计。撤销历史保留，之后再离职会记新的撤销。
     * 旧会话不会复活，须重新免登。
     */
    public IdentityBinding reinstate(TenantContext ctx, UUID principalId) {
        access.require(ctx, Permission.MANAGE_MEMBERS, null);
        TenantId tenant = ctx.tenantId();
        IdentityBinding binding = bindings.findByPrincipal(tenant, principalId)
                .orElseThrow(() -> new NotFoundException("identity binding not found"));
        if (!tenantScope.call(tenant, () -> orgBindings.isRevoked(principalId))) {
            throw new ConflictException("this identity binding is not revoked");
        }
        try {
            members.invite(ctx, principalId.toString(), MemberKind.STAFF);
        } catch (SeatLimitExceededException e) {
            throw new ConflictException("no seat is available to reinstate this member");
        }
        boolean lifted = tenantScope.call(tenant, () -> {
            boolean reinstated = orgBindings.reinstate(principalId, ctx.actorId());
            if (reinstated) {
                audit.record(tenant, ctx.actorId(), ACTION_REINSTATE, "principal/" + principalId
                        + " provider=" + binding.identity().provider(), ctx.traceId());
            }
            return reinstated;
        });
        if (!lifted) {
            throw new ConflictException("this identity binding is not revoked");
        }
        return binding;
    }

    private SyncResult syncConnection(OrgConnection connection, String actor, String traceId) {
        TenantId tenant = connection.tenantId();
        String secret = secrets.resolve(tenant, connection.secretRef())
                .orElseThrow(() -> OrgLoginException.notConfigured("the connection secret"));
        OrgPlatformClient client = clients.get(connection.provider());
        if (client == null) {
            throw OrgLoginException.notConfigured("provider " + connection.provider().code());
        }
        SyncResult total = SyncResult.NONE;
        OrgBindingRepository.Cursor cursor = null;
        while (true) {
            OrgBindingRepository.Cursor after = cursor;
            List<OrgBindingRepository.ActiveBinding> page = tenantScope.call(tenant, () -> orgBindings
                    .listActivePage(connection.provider().code(), connection.corpId(), after, properties.batchSize()));
            total = total.plus(checkPage(connection, client, secret, page, actor, traceId));
            if (page.size() < properties.batchSize()) {
                return total;
            }
            OrgBindingRepository.ActiveBinding last = page.getLast();
            cursor = new OrgBindingRepository.Cursor(last.createdAt(), last.principalId());
        }
    }

    private SyncResult checkPage(OrgConnection connection, OrgPlatformClient client, String secret,
            List<OrgBindingRepository.ActiveBinding> page, String actor, String traceId) {
        int revoked = 0;
        int unknown = 0;
        for (OrgBindingRepository.ActiveBinding binding : page) {
            OrgPlatformClient.DirectoryStatus status = client.lookup(connection, secret, binding.externalId());
            if (status == OrgPlatformClient.DirectoryStatus.DEPARTED
                    && revoke(connection.tenantId(), binding.principalId(), actor, traceId)) {
                revoked++;
            } else if (status == OrgPlatformClient.DirectoryStatus.UNKNOWN) {
                unknown++;
            }
        }
        return new SyncResult(page.size(), revoked, unknown);
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
