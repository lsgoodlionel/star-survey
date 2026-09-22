package cn.mjy.platform.access;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

/**
 * 组织同步移除成员（离职 / 禁用）与"最后一名所有者不可移除"的判定必须与手工撤权同一口径：
 * 过期的所有者授权不算所有者。
 */
@SpringBootTest
class MemberSystemRemovalTest {

    @Autowired
    private AccessFixture fixture;

    @Autowired
    private MemberService members;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private void expireGrantsOf(TenantContext owner, String actor) {
        tenantScope.run(owner.tenantId(), () -> jdbc.sql("""
                        UPDATE access_grant SET expires_at = now() - interval '1 hour' WHERE actor_id = :actor
                        """)
                .param("actor", actor).update());
    }

    @Test
    void aDepartedMemberWhoseOwnerGrantExpiredIsRemovedWhileAnotherOwnerIsActive() {
        TenantContext owner = fixture.newTenant();
        fixture.member(owner, "departed", "tenant_owner", null, Instant.now().plus(1, ChronoUnit.HOURS));
        expireGrantsOf(owner, "departed");
        long seatsBefore = members.seatsInUse(owner);

        boolean removed = members.removeBySystem(owner.tenantId(), "departed", "trace-sync");

        assertThat(removed).isTrue();
        assertThat(members.seatsInUse(owner)).isEqualTo(seatsBefore - 1);
    }

    @Test
    void theOnlyActiveOwnerIsStillNotRemoved() {
        TenantContext owner = fixture.newTenant();

        boolean removed = members.removeBySystem(owner.tenantId(), AccessFixture.OWNER, "trace-sync");

        assertThat(removed).isFalse();
    }
}
