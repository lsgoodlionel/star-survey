package cn.mjy.platform.contacts;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/** 标签与标签分配的读写。须在 {@code TenantScope} 内调用；跨租户的标签行级安全下不可见。 */
@Repository
class ContactTagRepository {

    private final JdbcClient jdbc;

    ContactTagRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /** 同名（不分大小写）已存在时返回原标签，保持幂等。 */
    Optional<ContactTagView> findByName(String name) {
        return jdbc.sql("SELECT id, name FROM contact_tag WHERE lower(name) = lower(:name)")
                .param("name", name).query(ContactTagRepository::toView).optional();
    }

    Optional<ContactTagView> find(UUID id) {
        return jdbc.sql("SELECT id, name FROM contact_tag WHERE id = :id")
                .param("id", id).query(ContactTagRepository::toView).optional();
    }

    ContactTagView insert(UUID id, String name, String createdBy) {
        return jdbc.sql("""
                        INSERT INTO contact_tag (tenant_id, id, name, created_by)
                        VALUES (app_current_tenant(), :id, :name, :by)
                        RETURNING id, name
                        """).param("id", id).param("name", name).param("by", createdBy)
                .query(ContactTagRepository::toView).single();
    }

    List<ContactTagView> list() {
        return jdbc.sql("SELECT id, name FROM contact_tag ORDER BY name")
                .query(ContactTagRepository::toView).list();
    }

    void assign(UUID contactId, UUID tagId, String assignedBy) {
        jdbc.sql("""
                INSERT INTO contact_tag_assignment (tenant_id, contact_id, tag_id, assigned_by)
                VALUES (app_current_tenant(), :contact, :tag, :by)
                ON CONFLICT (tenant_id, contact_id, tag_id) DO NOTHING
                """).param("contact", contactId).param("tag", tagId).param("by", assignedBy).update();
    }

    void unassign(UUID contactId, UUID tagId) {
        jdbc.sql("DELETE FROM contact_tag_assignment WHERE contact_id = :contact AND tag_id = :tag")
                .param("contact", contactId).param("tag", tagId).update();
    }

    private static ContactTagView toView(ResultSet rs, int row) throws SQLException {
        return new ContactTagView(rs.getObject("id", UUID.class), rs.getString("name"));
    }
}
