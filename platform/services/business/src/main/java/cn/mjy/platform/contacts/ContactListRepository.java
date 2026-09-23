package cn.mjy.platform.contacts;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/** 联系人名单的读写。须在 {@code TenantScope} 内调用。 */
@Repository
class ContactListRepository {

    private static final String COLUMNS = "id, name, kind, dedupe_key, created_at";

    private final JdbcClient jdbc;

    ContactListRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    ContactListView insert(UUID id, String name, ContactKind kind, DedupeKey dedupeKey, String createdBy) {
        return jdbc.sql("""
                        INSERT INTO contact_list (tenant_id, id, name, kind, dedupe_key, created_by)
                        VALUES (app_current_tenant(), :id, :name, :kind, :key, :by)
                        RETURNING """ + " " + COLUMNS)
                .param("id", id).param("name", name).param("kind", kind.code())
                .param("key", dedupeKey.code()).param("by", createdBy)
                .query(ContactListRepository::toView).single();
    }

    Optional<ContactListView> find(UUID id) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM contact_list WHERE id = :id")
                .param("id", id).query(ContactListRepository::toView).optional();
    }

    List<ContactListView> list() {
        return jdbc.sql("SELECT " + COLUMNS + " FROM contact_list ORDER BY created_at, id")
                .query(ContactListRepository::toView).list();
    }

    private static ContactListView toView(ResultSet rs, int row) throws SQLException {
        return new ContactListView(rs.getObject("id", UUID.class), rs.getString("name"),
                ContactKind.fromDb(rs.getString("kind")), DedupeKey.fromDb(rs.getString("dedupe_key")),
                rs.getObject("created_at", OffsetDateTime.class));
    }
}
