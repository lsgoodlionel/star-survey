package cn.mjy.platform.access;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.util.List;
import java.util.Objects;
import java.util.UUID;
import java.util.stream.Stream;
import org.springframework.stereotype.Service;

/**
 * 资源树管理（ADR 0011 规则 1、2）：建项目、建文件夹、查询、改名、移动。全部服务端判定、同事务审计。
 *
 * <ul>
 *   <li>建项目需要租户级 manage-settings；创建者在新项目上获得 {@link #CREATOR_ROLE}；</li>
 *   <li>建文件夹需要父节点（项目或文件夹）上的 edit；</li>
 *   <li>移动文件夹或问卷需要源位置（当前父节点）与目标位置都有 edit，不得移到自己或子孙下；
 *       继承的授权由判定时沿祖先链实时计算，因此移动后自动按新位置生效，挂在节点本身的授权原样保留；</li>
 *   <li>别的租户的 id 在行级安全下不可见，一律当作不存在（404）。</li>
 * </ul>
 */
@Service
public class ResourceTreeService {

    /** 项目创建者获得的授权角色：可在项目上管理成员与内容，但不含审核与免审发布。 */
    public static final String CREATOR_ROLE = "project_manager";
    public static final int MAX_NAME_LENGTH = 200;
    public static final int DEFAULT_PAGE_SIZE = 50;
    public static final int MAX_PAGE_SIZE = 200;
    /** 锁根行时，被并发移动"换了项目"的节点最多重试的轮数。 */
    private static final int MAX_LOCK_ROUNDS = 5;

    private final TenantScope tenantScope;
    private final AccessDecisionService access;
    private final AccessResources resources;
    private final ResourceTreeRepository tree;
    private final MemberRepository members;
    private final GrantRepository grants;
    private final AccessAudit audit;

    ResourceTreeService(TenantScope tenantScope, AccessDecisionService access, AccessResources resources,
            ResourceTreeRepository tree, MemberRepository members, GrantRepository grants, AccessAudit audit) {
        this.tenantScope = tenantScope;
        this.access = access;
        this.resources = resources;
        this.tree = tree;
        this.members = members;
        this.grants = grants;
        this.audit = audit;
    }

    public ResourceView createProject(TenantContext ctx, String name) {
        String normalized = normalizeName(name);
        TenantId tenant = ctx.tenantId();
        return tenantScope.call(tenant, () -> {
            require(ctx, Permission.MANAGE_SETTINGS, null);
            UUID id = resources.register(tenant, UUID.randomUUID(), ResourceKind.PROJECT, null, normalized);
            audit.record(ctx, AccessAudit.RESOURCE_CREATE, describe(id, ResourceKind.PROJECT, null, normalized));
            GrantRequest creatorGrant = new GrantRequest(ctx.actorId(), CREATOR_ROLE, id, null);
            GrantView granted = grants.find(tenant, grants.upsert(tenant, creatorGrant, ctx.actorId())).orElseThrow();
            audit.record(ctx, AccessAudit.GRANT_CREATE, AccessAudit.describeGrant(granted));
            return requireNode(tenant, id);
        });
    }

    public ResourceView createFolder(TenantContext ctx, UUID parentId, String name) {
        String normalized = normalizeName(name);
        requireId(parentId, "parentId");
        TenantId tenant = ctx.tenantId();
        return tenantScope.call(tenant, () -> {
            require(ctx, Permission.EDIT, parentId);
            requireContainer(requireNode(tenant, parentId));
            UUID id = resources.register(tenant, UUID.randomUUID(), ResourceKind.FOLDER, parentId, normalized);
            audit.record(ctx, AccessAudit.RESOURCE_CREATE, describe(id, ResourceKind.FOLDER, parentId, normalized));
            return requireNode(tenant, id);
        });
    }

    /** 调用者有 view 的节点（含继承），可按父节点过滤；按 id 排序、游标分页。 */
    public ResourcePage list(TenantContext ctx, UUID parentId, String cursor, Integer limit) {
        int pageSize = pageSize(limit);
        UUID after = ResourceCursor.decode(cursor);
        TenantId tenant = ctx.tenantId();
        return tenantScope.call(tenant, () -> {
            requireActiveMember(ctx);
            if (parentId != null && tree.find(tenant, parentId).isEmpty()) {
                throw new ResourceNotFoundException("resource not found: " + parentId);
            }
            List<ResourceView> rows = tree.visible(tenant, ctx.actorId(), parentId, after, pageSize + 1);
            boolean hasMore = rows.size() > pageSize;
            List<ResourceView> items = hasMore ? rows.subList(0, pageSize) : rows;
            return new ResourcePage(items, hasMore ? ResourceCursor.encode(items.getLast().id()) : null);
        });
    }

    public ResourceView get(TenantContext ctx, UUID id) {
        requireId(id, "id");
        return tenantScope.call(ctx.tenantId(), () -> {
            require(ctx, Permission.VIEW, id);
            return requireNode(ctx.tenantId(), id);
        });
    }

    /** 改名需要节点上的 edit。问卷的名称来自其定义标题，改名须经保存草稿，不在这里改。 */
    public ResourceView rename(TenantContext ctx, UUID id, String name) {
        String normalized = normalizeName(name);
        requireId(id, "id");
        TenantId tenant = ctx.tenantId();
        return tenantScope.call(tenant, () -> {
            require(ctx, Permission.EDIT, id);
            ResourceView node = requireNode(tenant, id);
            if (node.resourceKind() == ResourceKind.SURVEY) {
                throw new InvalidResourceRequestException("a survey is renamed by saving its draft title");
            }
            tree.rename(tenant, id, normalized);
            audit.record(ctx, AccessAudit.RESOURCE_RENAME, describe(id, node.resourceKind(), node.parentId(), normalized)
                    + " from=" + quoted(node.name()));
            return requireNode(tenant, id);
        });
    }

    /** 把文件夹或问卷移到 targetParentId 下（ADR 0011 规则 2）。移到当前父节点下是无操作。 */
    public ResourceView move(TenantContext ctx, UUID id, UUID targetParentId) {
        requireId(id, "id");
        requireId(targetParentId, "parentId");
        TenantId tenant = ctx.tenantId();
        return tenantScope.call(tenant, () -> {
            requireMovable(requireNode(tenant, id));
            requireContainer(requireNode(tenant, targetParentId));
            lockTrees(tenant, id, targetParentId);
            ResourceView node = requireNode(tenant, id);
            require(ctx, Permission.EDIT, node.parentId());
            require(ctx, Permission.EDIT, targetParentId);
            if (targetParentId.equals(node.parentId())) {
                return node;
            }
            if (tree.chain(tenant, targetParentId).contains(id)) {
                throw new ResourceConflictException(ResourceConflictException.MOVE_INTO_DESCENDANT,
                        "cannot move " + id + " under itself or its own descendant " + targetParentId);
            }
            tree.reparent(tenant, id, targetParentId);
            audit.record(ctx, AccessAudit.RESOURCE_MOVE, describe(id, node.resourceKind(), targetParentId, node.name())
                    + " from=" + node.parentId());
            return requireNode(tenant, id);
        });
    }

    /**
     * 锁住节点与目标所在项目的根行。任何移动都要先锁住涉及的项目，所以持锁期间这两棵树的结构不会被别的移动改变，
     * 随后的祖先链检查看到的就是最终状态。锁前读到的项目可能已被并发移动改变，因此锁后重算、直到稳定。
     */
    private void lockTrees(TenantId tenant, UUID id, UUID targetParentId) {
        List<UUID> locked = List.of();
        for (int round = 0; round < MAX_LOCK_ROUNDS; round++) {
            List<UUID> roots = List.of(rootOf(tenant, id), rootOf(tenant, targetParentId));
            if (locked.containsAll(roots)) {
                return;
            }
            tree.lock(tenant, roots);
            locked = Stream.concat(locked.stream(), roots.stream()).distinct().toList();
        }
        throw new ResourceConflictException(ResourceConflictException.TREE_BUSY,
                "the tree kept changing while moving " + id + "; retry");
    }

    private UUID rootOf(TenantId tenant, UUID id) {
        List<UUID> chain = tree.chain(tenant, id);
        if (chain.isEmpty()) {
            throw new ResourceNotFoundException("resource not found: " + id);
        }
        return chain.getLast();
    }

    private void require(TenantContext ctx, Permission permission, UUID resourceId) {
        Decision decision = access.can(ctx, permission, resourceId);
        if (decision.allowed()) {
            return;
        }
        if (decision.reason() == DecisionReason.UNKNOWN_RESOURCE) {
            throw new ResourceNotFoundException("resource not found: " + resourceId);
        }
        throw new ResourceAccessDeniedException(decision.reason(), permission.code() + " denied: " + decision.detail());
    }

    private void requireActiveMember(TenantContext ctx) {
        boolean active = members.find(ctx.tenantId(), ctx.actorId())
                .map(MemberRepository.Member::isActive).orElse(false);
        if (!active) {
            throw new ResourceAccessDeniedException(DecisionReason.NOT_AN_ACTIVE_MEMBER, "actor " + ctx.actorId());
        }
    }

    private ResourceView requireNode(TenantId tenant, UUID id) {
        return tree.find(tenant, id).orElseThrow(() -> new ResourceNotFoundException("resource not found: " + id));
    }

    private static void requireContainer(ResourceView parent) {
        if (!parent.resourceKind().canContainChildren()) {
            throw new InvalidResourceRequestException("a " + parent.kind() + " cannot contain children");
        }
    }

    private static void requireMovable(ResourceView node) {
        if (node.resourceKind() == ResourceKind.PROJECT) {
            throw new InvalidResourceRequestException("a project is a root and cannot be moved");
        }
    }

    private static void requireId(UUID id, String field) {
        if (id == null) {
            throw new InvalidResourceRequestException(field + " is required");
        }
    }

    static String normalizeName(String name) {
        String trimmed = name == null ? "" : name.strip();
        if (trimmed.isEmpty()) {
            throw new InvalidResourceRequestException("name is required");
        }
        if (trimmed.length() > MAX_NAME_LENGTH) {
            throw new InvalidResourceRequestException("name is longer than " + MAX_NAME_LENGTH + " characters");
        }
        return trimmed;
    }

    private static int pageSize(Integer limit) {
        int size = Objects.requireNonNullElse(limit, DEFAULT_PAGE_SIZE);
        if (size < 1 || size > MAX_PAGE_SIZE) {
            throw new InvalidResourceRequestException("limit must be between 1 and " + MAX_PAGE_SIZE);
        }
        return size;
    }

    private static String describe(UUID id, ResourceKind kind, UUID parentId, String name) {
        return "resource/" + id + " kind=" + kind.code() + " parent=" + (parentId == null ? "none" : parentId)
                + " name=" + quoted(name);
    }

    /** 名称可含空格与引号；审计里加引号并转义，保持"键=值"可解析。 */
    private static String quoted(String value) {
        return '"' + value.replace("\\", "\\\\").replace("\"", "\\\"") + '"';
    }
}
