package cn.mjy.platform.tenant;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantDirectory;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/** 租户枚举只给出未关闭的租户标识。 */
@SpringBootTest
class TenantDirectoryTest {

    @Autowired
    private TenantDirectory directory;

    @Autowired
    private TenantFixtures fixtures;

    @Autowired
    private TenantService tenants;

    @Test
    void openTenantsListsActiveSuspendedAndProvisioningButNotClosedTenants() {
        TenantId active = fixtures.activeTenant();
        TenantId provisioning = fixtures.provisionedTenant().id();
        TenantId suspended = fixtures.activeTenant();
        tenants.changeStatus(suspended, TenantStatus.SUSPENDED, TenantFixtures.OPERATOR_ACTOR, "trace");
        TenantId closed = fixtures.activeTenant();
        tenants.changeStatus(closed, TenantStatus.CLOSED, TenantFixtures.OPERATOR_ACTOR, "trace");

        assertThat(directory.openTenants()).contains(active, provisioning, suspended).doesNotContain(closed);
    }
}
