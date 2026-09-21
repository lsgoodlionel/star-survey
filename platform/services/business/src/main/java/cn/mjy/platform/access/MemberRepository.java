package cn.mjy.platform.access;

import cn.mjy.platform.shared.TenantId;
import java.util.Optional;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/** 成员表的数据访问。所有方法都须在租户作用域内调用；显式带 tenant_id 只是纵深防御，隔离靠行级安全。 */
@Repository
class MemberRepository {

    static final String INVITED = "invited";
    static final String ACTIVE = "active";
    static final String REMOVED = "removed";

    private final JdbcClient jdbc;

    MemberRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    record Member(String actorId, String status, boolean usesSeat) {

        boolean isActive() {
            return ACTIVE.equals(status);
        }

        boolean isRemoved() {
            return REMOVED.equals(status);
        }
    }

    Optional<Member> find(TenantId tenant, String actorId) {
        return jdbc.sql("SELECT actor_id, status, uses_seat FROM access_member WHERE tenant_id = :tenant AND actor_id = :actor")
                .param("tenant", tenant.value())
                .param("actor", actorId)
                .query((rs, n) -> new Member(rs.getString("actor_id"), rs.getString("status"), rs.getBoolean("uses_seat")))
                .optional();
    }

    boolean hasAnyMember(TenantId tenant) {
        return jdbc.sql("SELECT EXISTS (SELECT 1 FROM access_member WHERE tenant_id = :tenant)")
                .param("tenant", tenant.value())
                .query(Boolean.class)
                .single();
    }

    long seatsInUse(TenantId tenant) {
        return jdbc.sql("""
                SELECT COUNT(*) FROM access_member
                WHERE tenant_id = :tenant AND uses_seat AND status <> 'removed'
                """)
                .param("tenant", tenant.value())
                .query(Long.class)
                .single();
    }

    /** 新建或重新邀请（已移除的成员再次邀请时复用原行）。 */
    void upsert(TenantId tenant, String actorId, String status, boolean usesSeat, String invitedBy) {
        jdbc.sql("""
                INSERT INTO access_member (tenant_id, actor_id, status, uses_seat, invited_by)
                VALUES (:tenant, :actor, :status, :seat, :by)
                ON CONFLICT (tenant_id, actor_id) DO UPDATE
                    SET status = EXCLUDED.status, uses_seat = EXCLUDED.uses_seat,
                        invited_by = EXCLUDED.invited_by, updated_at = now()
                """)
                .param("tenant", tenant.value())
                .param("actor", actorId)
                .param("status", status)
                .param("seat", usesSeat)
                .param("by", invitedBy)
                .update();
    }

    void markRemoved(TenantId tenant, String actorId) {
        jdbc.sql("""
                UPDATE access_member SET status = 'removed', updated_at = now()
                WHERE tenant_id = :tenant AND actor_id = :actor AND status <> 'removed'
                """)
                .param("tenant", tenant.value())
                .param("actor", actorId)
                .update();
    }

    int changeStatus(TenantId tenant, String actorId, String fromStatus, String toStatus) {
        return jdbc.sql("""
                UPDATE access_member SET status = :to, updated_at = now()
                WHERE tenant_id = :tenant AND actor_id = :actor AND status = :from
                """)
                .param("tenant", tenant.value())
                .param("actor", actorId)
                .param("from", fromStatus)
                .param("to", toStatus)
                .update();
    }
}
