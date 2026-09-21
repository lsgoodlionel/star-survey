package cn.mjy.platform.tenant;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantId;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

/**
 * 租户登记是控制平面表：运行期账号权限收到最小（只能新建、只能改状态列、不能删），
 * 状态机在数据库里再守一道，代码写错也改不出非法状态。
 */
@SpringBootTest
class TenantTableGuardsTest {

    @Autowired
    private TenantFixtures fixtures;

    @Autowired
    private JdbcClient jdbc;

    private void setStatus(TenantId id, String status) {
        jdbc.sql("UPDATE tenant SET status = :s WHERE id = :id").param("s", status).param("id", id.value()).update();
    }

    @Test
    void theRuntimeAccountCannotDeleteTenants() {
        TenantId id = fixtures.provisionedTenant().id();

        assertThatThrownBy(() -> jdbc.sql("DELETE FROM tenant WHERE id = :id").param("id", id.value()).update())
                .rootCause().hasMessageContaining("permission denied");
    }

    @Test
    void theRuntimeAccountCannotRenameOrRecodeTenants() {
        TenantId id = fixtures.provisionedTenant().id();

        assertThatThrownBy(() -> jdbc.sql("UPDATE tenant SET code = 'hijack' WHERE id = :id")
                .param("id", id.value()).update())
                .rootCause().hasMessageContaining("permission denied");
    }

    @Test
    void theDatabaseRejectsAnIllegalTransitionEvenIfCodeTriesIt() {
        TenantId id = fixtures.provisionedTenant().id();

        assertThatThrownBy(() -> setStatus(id, "suspended"))
                .rootCause().hasMessageContaining("illegal tenant transition");
    }

    @Test
    void theDatabaseKeepsClosedTerminal() {
        TenantId id = fixtures.provisionedTenant().id();
        setStatus(id, "closed");

        assertThatThrownBy(() -> setStatus(id, "active"))
                .rootCause().hasMessageContaining("illegal tenant transition");
        String status = jdbc.sql("SELECT status FROM tenant WHERE id = :id").param("id", id.value())
                .query(String.class).single();
        assertThat(status).isEqualTo("closed");
    }

    @Test
    void newTenantsMustStartInProvisioning() {
        assertThatThrownBy(() -> jdbc.sql("""
                        INSERT INTO tenant (id, code, name, status) VALUES (:id, :code, 'x', 'active')
                        """)
                .param("id", TenantId.random().value())
                .param("code", TenantFixtures.uniqueCode("skip"))
                .update())
                .rootCause().hasMessageContaining("provisioning");
    }
}
