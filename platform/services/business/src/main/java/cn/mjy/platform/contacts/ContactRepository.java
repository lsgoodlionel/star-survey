package cn.mjy.platform.contacts;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Types;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * 联系人行的读写。须在 {@code TenantScope} 内调用；跨租户由行级安全挡住，
 * 部门范围由 {@link OrgUnitRepository#VISIBLE_UNITS} 收窄——两者都在 SQL 里，不靠内存过滤。
 */
@Repository
class ContactRepository {

    private static final String BARE_COLUMNS = """
            id, kind, list_id, org_unit_id, display_name, email, phone, employee_id,
            provider, app_id, external_id, dedupe_value, source, status, actor_id, created_at, updated_at""";

    private static final String COLUMNS = """
            c.id, c.kind, c.list_id, c.org_unit_id, c.display_name, c.email, c.phone, c.employee_id,
            c.provider, c.app_id, c.external_id, c.dedupe_value, c.source, c.status, c.actor_id,
            c.created_at, c.updated_at""";

    /** 范围过滤：全租户放行，否则必须落在可见部门里（没有部门的联系人只有全租户范围看得到）。 */
    private static final String IN_SCOPE = "(:whole OR c.org_unit_id IN (SELECT id FROM visible_unit))";

    /** 新建联系人所需的全部字段。 */
    record NewContact(UUID id, ContactKind kind, UUID listId, ContactDraft draft, String dedupeValue,
            String source, String createdBy) {
    }

    /** 数据库里的一行。 */
    record ContactRow(UUID id, ContactKind kind, UUID listId, UUID orgUnitId, String displayName, String email,
            String phone, String employeeId, ExternalContactIdentity identity, String dedupeValue,
            String source, String status, String actorId, OffsetDateTime createdAt, OffsetDateTime updatedAt) {
    }

    private final JdbcClient jdbc;

    ContactRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    ContactRow insert(NewContact contact) {
        ContactDraft draft = contact.draft();
        ExternalContactIdentity identity = draft.identity();
        return jdbc.sql("""
                        INSERT INTO contact (tenant_id, id, kind, list_id, org_unit_id, display_name, email, phone,
                            employee_id, provider, app_id, external_id, dedupe_value, source, actor_id,
                            principal_id, created_by)
                        VALUES (app_current_tenant(), :id, :kind, :list, :unit, :name, :email, :phone,
                            :employee, :provider, :app, :external, :dedupe, :source, :actor, :principal, :by)
                        RETURNING """ + " " + BARE_COLUMNS)
                .param("id", contact.id()).param("kind", contact.kind().code()).param("list", contact.listId())
                .param("unit", draft.orgUnitId())
                .param("name", draft.displayName(), Types.VARCHAR)
                .param("email", draft.email(), Types.VARCHAR)
                .param("phone", draft.phone(), Types.VARCHAR)
                .param("employee", draft.employeeId(), Types.VARCHAR)
                .param("provider", identity == null ? null : identity.provider(), Types.VARCHAR)
                .param("app", identity == null ? null : identity.appId(), Types.VARCHAR)
                .param("external", identity == null ? null : identity.externalId(), Types.VARCHAR)
                .param("dedupe", contact.dedupeValue(), Types.VARCHAR)
                .param("actor", draft.actorId(), Types.VARCHAR).param("principal", draft.principalId())
                .param("source", contact.source()).param("by", contact.createdBy())
                .query(ContactRepository::toRow).single();
    }

    /**
     * 合并更新：只覆盖本次提供（非空）的字段，其余保留。dedupeValue 为空表示去重值不变。
     */
    ContactRow update(UUID id, ContactDraft draft, String dedupeValue) {
        ExternalContactIdentity identity = draft.identity();
        return jdbc.sql("""
                        UPDATE contact SET
                            display_name = COALESCE(:name, display_name),
                            email        = COALESCE(:email, email),
                            phone        = COALESCE(:phone, phone),
                            employee_id  = COALESCE(:employee, employee_id),
                            provider     = COALESCE(:provider, provider),
                            app_id       = COALESCE(:app, app_id),
                            external_id  = COALESCE(:external, external_id),
                            org_unit_id  = COALESCE(:unit, org_unit_id),
                            actor_id     = COALESCE(:actor, actor_id),
                            principal_id = COALESCE(:principal, principal_id),
                            dedupe_value = COALESCE(:dedupe, dedupe_value),
                            updated_at   = now()
                        WHERE id = :id
                        RETURNING """ + " " + BARE_COLUMNS)
                .param("id", id)
                .param("name", draft.displayName(), Types.VARCHAR)
                .param("email", draft.email(), Types.VARCHAR)
                .param("phone", draft.phone(), Types.VARCHAR)
                .param("employee", draft.employeeId(), Types.VARCHAR)
                .param("provider", identity == null ? null : identity.provider(), Types.VARCHAR)
                .param("app", identity == null ? null : identity.appId(), Types.VARCHAR)
                .param("external", identity == null ? null : identity.externalId(), Types.VARCHAR)
                .param("unit", draft.orgUnitId())
                .param("actor", draft.actorId(), Types.VARCHAR).param("principal", draft.principalId())
                .param("dedupe", dedupeValue, Types.VARCHAR)
                .query(ContactRepository::toRow).single();
    }

    void setStatus(UUID id, String status) {
        jdbc.sql("UPDATE contact SET status = :status, updated_at = now() WHERE id = :id")
                .param("status", status).param("id", id).update();
    }

    /** 按范围读一行；不在范围内等同不存在。 */
    Optional<ContactRow> findVisible(ContactScope scope, UUID id) {
        return jdbc.sql(OrgUnitRepository.VISIBLE_UNITS + " SELECT " + COLUMNS + " FROM contact c"
                        + " WHERE c.id = :id AND " + IN_SCOPE)
                .param("actor", scope.actorId()).param("whole", scope.wholeTenant()).param("id", id)
                .query(ContactRepository::toRow).optional();
    }

    /**
     * 本租户里有没有这个联系人。不看数据范围：调用方是发布收尾，
     * 它拿到的 id 是平台自己写进定义、由网关回显的 {@code ref}，这里只判"是不是本租户的人"。
     */
    boolean exists(UUID id) {
        return Boolean.TRUE.equals(jdbc.sql("SELECT EXISTS (SELECT 1 FROM contact WHERE id = :id)")
                .param("id", id).query(Boolean.class).single());
    }

    /** 名单内按去重值查（导入与新建的判重入口）。不看范围：去重是名单内的事实，不是可见性。 */
    Optional<ContactRow> findInListByDedupe(UUID listId, String dedupeValue) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM contact c"
                        + " WHERE c.list_id = :list AND c.dedupe_value = :dedupe")
                .param("list", listId).param("dedupe", dedupeValue)
                .query(ContactRepository::toRow).optional();
    }

    /** 不属于任何名单的目录联系人，按 (类别, provider, app_id, external_id) 查。 */
    Optional<ContactRow> findDirectoryByIdentity(ContactKind kind, ExternalContactIdentity identity) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM contact c WHERE c.list_id IS NULL AND c.kind = :kind"
                        + " AND c.provider = :provider AND c.app_id = :app AND c.external_id = :external")
                .param("kind", kind.code()).param("provider", identity.provider())
                .param("app", identity.appId()).param("external", identity.externalId())
                .query(ContactRepository::toRow).optional();
    }

    List<ContactRow> search(ContactScope scope, ContactQuery query, int limit) {
        List<String> where = new ArrayList<>(List.of(IN_SCOPE));
        StringBuilder ctes = new StringBuilder(OrgUnitRepository.VISIBLE_UNITS);
        if (query.listId() != null) {
            where.add("c.list_id = :list");
        }
        if (query.orgUnitId() != null && query.includeDescendants()) {
            ctes.append(", ").append(OrgUnitRepository.UNIT_SUBTREE);
            where.add("c.org_unit_id IN (SELECT id FROM unit_subtree)");
        } else if (query.orgUnitId() != null) {
            where.add("c.org_unit_id = :unit");
        }
        if (query.tag() != null) {
            where.add("""
                    EXISTS (SELECT 1 FROM contact_tag_assignment a JOIN contact_tag t ON t.id = a.tag_id
                             WHERE a.contact_id = c.id AND lower(t.name) = lower(:tag))""");
        }
        if (query.cursor() != null) {
            where.add("c.id > :after");
        }

        JdbcClient.StatementSpec statement = jdbc.sql(ctes + " SELECT " + COLUMNS + " FROM contact c WHERE "
                        + String.join(" AND ", where) + " ORDER BY c.id LIMIT :limit")
                .param("actor", scope.actorId()).param("whole", scope.wholeTenant()).param("limit", limit);
        if (query.listId() != null) {
            statement = statement.param("list", query.listId());
        }
        if (query.orgUnitId() != null) {
            statement = statement.param("unit", query.orgUnitId());
        }
        if (query.tag() != null) {
            statement = statement.param("tag", query.tag());
        }
        if (query.cursor() != null) {
            statement = statement.param("after", UUID.fromString(query.cursor()));
        }
        return statement.query(ContactRepository::toRow).list();
    }

    List<String> tagsOf(UUID contactId) {
        return jdbc.sql("""
                        SELECT t.name FROM contact_tag_assignment a JOIN contact_tag t ON t.id = a.tag_id
                         WHERE a.contact_id = :id ORDER BY t.name
                        """).param("id", contactId).query(String.class).list();
    }

    /** 批量取标签，避免逐行查询（N+1）。 */
    List<TagRow> tagsOf(Collection<UUID> contactIds) {
        if (contactIds.isEmpty()) {
            return List.of();
        }
        return jdbc.sql("""
                        SELECT a.contact_id, t.name FROM contact_tag_assignment a
                          JOIN contact_tag t ON t.id = a.tag_id
                         WHERE a.contact_id IN (:ids) ORDER BY a.contact_id, t.name
                        """).param("ids", contactIds)
                .query((rs, n) -> new TagRow(rs.getObject("contact_id", UUID.class), rs.getString("name")))
                .list();
    }

    record TagRow(UUID contactId, String name) {
    }

    private static ContactRow toRow(ResultSet rs, int row) throws SQLException {
        String provider = rs.getString("provider");
        ExternalContactIdentity identity = provider == null ? null
                : new ExternalContactIdentity(provider, rs.getString("app_id"), rs.getString("external_id"));
        return new ContactRow(rs.getObject("id", UUID.class), ContactKind.fromDb(rs.getString("kind")),
                rs.getObject("list_id", UUID.class), rs.getObject("org_unit_id", UUID.class),
                rs.getString("display_name"), rs.getString("email"), rs.getString("phone"),
                rs.getString("employee_id"), identity, rs.getString("dedupe_value"), rs.getString("source"),
                rs.getString("status"), rs.getString("actor_id"),
                rs.getObject("created_at", OffsetDateTime.class),
                rs.getObject("updated_at", OffsetDateTime.class));
    }
}
