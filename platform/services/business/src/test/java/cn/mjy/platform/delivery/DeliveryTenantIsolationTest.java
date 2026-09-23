package cn.mjy.platform.delivery;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.access.AccessFixture;
import cn.mjy.platform.response.ResponseFixture.Published;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.simple.JdbcClient;

/**
 * 租户隔离（CONVENTIONS 硬性要求）：对每张租户表都做两条负面断言——
 * B 读不到 A 的行，B 写 A 的行被数据库拒绝。断言走原始 SQL，绕开服务层的任何判断，
 * 证明隔离是数据库强制的，不是应用层"记得加 where"。
 */
@DeliveryIntegrationTest
class DeliveryTenantIsolationTest {

    /** 本车道所有带行级安全的表。新增表时必须补进来，否则这条用例就白跑了。 */
    private static final List<String> TENANT_TABLES = List.of(
            "delivery_link",
            "delivery_task",
            "delivery_recipient",
            "delivery_send",
            "delivery_rate_window",
            "delivery_receipt",
            "delivery_unsubscribe",
            "delivery_reminder_rule",
            "delivery_completion",
            "delivery_completion_cursor",
            "delivery_notification_rule");

    @Autowired
    private DeliveryFixture fixture;

    @Autowired
    private DeliveryLinkService links;

    @Autowired
    private DeliveryTaskService tasks;

    @Autowired
    private AccessFixture access;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    @Autowired
    private FakeEmailProvider email;

    private Published p;
    private TaskView task;
    private TenantId other;

    @BeforeEach
    void twoTenants() {
        email.reset();
        p = fixture.publishedSurvey();
        task = fixture.emailTask(p, 2);
        fixture.run(p.tenant(), task.id());
        other = access.newTenant().tenantId();
    }

    @Test
    void everyDeliveryTableEnforcesRowLevelSecurity() {
        for (String table : TENANT_TABLES) {
            assertThat(rowSecurity(table))
                    .describedAs("%s must have row level security enabled and forced", table)
                    .containsExactly(true, true);
            assertThat(policies(table))
                    .describedAs("%s must carry the tenant_isolation policy", table)
                    .contains("tenant_isolation");
        }
    }

    @Test
    void anotherTenantSeesNoneOfTheseRows() {
        long inOwnScope = tenantScope.call(p.tenant(), () -> count("delivery_recipient"));
        assertThat(inOwnScope).isEqualTo(2);

        tenantScope.run(other, () -> {
            for (String table : TENANT_TABLES) {
                assertThat(count(table)).describedAs("%s must be empty for another tenant", table).isZero();
            }
        });
    }

    @Test
    void writingAnotherTenantsRowIsRejectedByTheDatabase() {
        assertThatThrownBy(() -> tenantScope.run(other, () -> jdbc.sql("""
                        INSERT INTO delivery_link (tenant_id, id, survey_id, label, created_by)
                        VALUES (:tenant, :id, :survey, '偷建的链接', 'intruder')
                        """)
                .param("tenant", p.tenant().value())
                .param("id", UUID.randomUUID())
                .param("survey", p.surveyId())
                .update()))
                .isInstanceOf(DataAccessException.class);
    }

    @Test
    void updatingAnotherTenantsRecipientChangesNothing() {
        int updated = tenantScope.call(other, () -> jdbc.sql("""
                        UPDATE delivery_recipient SET state = 'failed' WHERE task_id = :task
                        """)
                .param("task", task.id())
                .update());

        assertThat(updated).isZero();
        assertThat(fixture.recipientViews(p.owner(), task.id()))
                .allSatisfy(recipient -> assertThat(recipient.state()).isEqualTo("sent"));
    }

    /** 服务层的跨租户访问一律 404，而不是 403——否则就能拿标识去探测别的租户有没有这条记录。 */
    @Test
    void crossTenantServiceCallsAreNotFound() {
        TenantContext intruder = AccessFixture.context(other, AccessFixture.OWNER);

        assertThatThrownBy(() -> tasks.status(intruder, task.id()))
                .isInstanceOf(DeliveryNotFoundException.class);
        assertThatThrownBy(() -> tasks.recipients(intruder, task.id()))
                .isInstanceOf(DeliveryNotFoundException.class);
        assertThatThrownBy(() -> tasks.cancel(intruder, task.id()))
                .isInstanceOf(DeliveryNotFoundException.class);
        assertThatThrownBy(() -> links.find(intruder, UUID.randomUUID()))
                .isInstanceOf(DeliveryNotFoundException.class);
    }

    /**
     * 短链登记是控制平面表（无行级隔离，只存标识）。这里钉住两件事：它不含任何租户业务数据，
     * 且只拿到短链码也读不出别的租户的链接内容——内容仍在受行级安全保护的 delivery_link 里。
     */
    @Test
    void theShortLinkRegistryHoldsIdentifiersOnlyAndLeaksNoContent() {
        LinkView link = fixture.link(p, "海报");
        String code = link.shortUrl().substring(link.shortUrl().lastIndexOf('/') + 1);

        List<String> columns = jdbc.sql("""
                        SELECT column_name FROM information_schema.columns
                         WHERE table_name = 'delivery_short_link' ORDER BY column_name
                        """)
                .query(String.class)
                .list();
        assertThat(columns).containsExactlyInAnyOrder("code", "tenant_id", "link_id", "created_at");

        // 在别的租户的作用域里，用短链码找到的链接行一行都看不见。
        tenantScope.run(other, () -> assertThat(jdbc.sql("""
                        SELECT COUNT(*) FROM delivery_short_link s
                          JOIN delivery_link l ON l.id = s.link_id
                         WHERE s.code = :code
                        """)
                .param("code", code)
                .query(Long.class)
                .single()).isZero());
    }

    private long count(String table) {
        return jdbc.sql("SELECT COUNT(*) FROM " + table).query(Long.class).single();
    }

    private List<Boolean> rowSecurity(String table) {
        return jdbc.sql("""
                        SELECT relrowsecurity, relforcerowsecurity FROM pg_class
                         WHERE oid = CAST(:table AS regclass)
                        """)
                .param("table", table)
                .query((rs, n) -> List.of(rs.getBoolean(1), rs.getBoolean(2)))
                .single();
    }

    private List<String> policies(String table) {
        return jdbc.sql("SELECT policyname FROM pg_policies WHERE tablename = :table")
                .param("table", table)
                .query(String.class)
                .list();
    }
}
