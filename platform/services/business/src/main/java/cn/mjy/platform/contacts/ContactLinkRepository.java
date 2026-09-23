package cn.mjy.platform.contacts;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/** 身份关联的读写。无向关系按 id 大小规范化；只追加，撤销是写 unlinked_at。 */
@Repository
class ContactLinkRepository {

    private static final String COLUMNS =
            "id, left_contact_id, right_contact_id, reason, linked_at, unlinked_at";

    private final JdbcClient jdbc;

    ContactLinkRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    Optional<ContactLinkView> findOpen(UUID left, UUID right) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM contact_link WHERE left_contact_id = :left"
                        + " AND right_contact_id = :right AND unlinked_at IS NULL")
                .param("left", left).param("right", right)
                .query(ContactLinkRepository::toView).optional();
    }

    Optional<ContactLinkView> find(UUID id) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM contact_link WHERE id = :id")
                .param("id", id).query(ContactLinkRepository::toView).optional();
    }

    ContactLinkView insert(UUID id, UUID left, UUID right, String reason, String linkedBy) {
        return jdbc.sql("""
                        INSERT INTO contact_link (tenant_id, id, left_contact_id, right_contact_id, reason, linked_by)
                        VALUES (app_current_tenant(), :id, :left, :right, :reason, :by)
                        RETURNING """ + " " + COLUMNS)
                .param("id", id).param("left", left).param("right", right)
                .param("reason", reason).param("by", linkedBy)
                .query(ContactLinkRepository::toView).single();
    }

    boolean revoke(UUID id, String actorId) {
        return jdbc.sql("""
                UPDATE contact_link SET unlinked_at = now(), unlinked_by = :by
                 WHERE id = :id AND unlinked_at IS NULL
                """).param("id", id).param("by", actorId).update() == 1;
    }

    List<ContactLinkView> openFor(UUID contactId) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM contact_link"
                        + " WHERE (left_contact_id = :id OR right_contact_id = :id) AND unlinked_at IS NULL"
                        + " ORDER BY linked_at, id")
                .param("id", contactId).query(ContactLinkRepository::toView).list();
    }

    private static ContactLinkView toView(ResultSet rs, int row) throws SQLException {
        return new ContactLinkView(rs.getObject("id", UUID.class),
                rs.getObject("left_contact_id", UUID.class), rs.getObject("right_contact_id", UUID.class),
                rs.getString("reason"), rs.getObject("linked_at", OffsetDateTime.class),
                rs.getObject("unlinked_at", OffsetDateTime.class));
    }
}
