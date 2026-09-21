package cn.mjy.platform.tenant.engine;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.engine.EngineInstanceDirectory;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.tenant.TenantFixtures;
import cn.mjy.platform.tenant.TenantService;
import cn.mjy.platform.tenant.TenantStatus;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * 引擎实例归属查询：只有"启用租户的启用实例"才解析出租户。
 * 引擎事件模块据此判定事件归属，所以任何不确定的情况都必须返回空（失败即关闭）。
 */
@SpringBootTest
class EngineInstanceDirectoryTest {

    private static final String ACTOR = TenantFixtures.OPERATOR_ACTOR;

    @Autowired
    private EngineInstanceDirectory directory;

    @Autowired
    private EngineInstanceService instances;

    @Autowired
    private TenantService tenants;

    @Autowired
    private TenantFixtures fixtures;

    @Autowired
    private TenantScope tenantScope;

    private TenantId tenant;
    private String instanceId;

    @BeforeEach
    void registerAnActiveInstance() {
        tenant = fixtures.activeTenant();
        instanceId = EngineFixtures.uniqueInstanceId();
        instances.register(tenant, instanceId, "https://a.engine.example", ACTOR, "trace");
    }

    private void setInstanceStatus(EngineInstanceStatus status) {
        instances.changeStatus(tenant, instanceId, status, ACTOR, "trace");
    }

    private void setTenantStatus(TenantStatus status) {
        tenants.changeStatus(tenant, status, ACTOR, "trace");
    }

    @Test
    void anActiveInstanceOfAnActiveTenantResolvesToItsOwner() {
        assertThat(directory.tenantOf(instanceId)).contains(tenant);
    }

    @Test
    void theAnswerDoesNotDependOnTheCallersTenantContext() {
        TenantId someoneElse = fixtures.activeTenant();

        assertThat(tenantScope.call(someoneElse, () -> directory.tenantOf(instanceId))).contains(tenant);
    }

    @Test
    void unknownOrMalformedInstancesResolveToNothing() {
        assertThat(directory.tenantOf(EngineFixtures.uniqueInstanceId())).isEmpty();
        assertThat(directory.tenantOf(null)).isEmpty();
        assertThat(directory.tenantOf("")).isEmpty();
        assertThat(directory.tenantOf("' OR 1=1 --")).isEmpty();
    }

    @Test
    void aRetiredInstanceResolvesToNothing() {
        setInstanceStatus(EngineInstanceStatus.RETIRED);

        assertThat(directory.tenantOf(instanceId)).isEmpty();
    }

    @Test
    void aDrainingInstanceResolvesToNothing() {
        setInstanceStatus(EngineInstanceStatus.DRAINING);

        assertThat(directory.tenantOf(instanceId)).isEmpty();
    }

    @Test
    void aSuspendedTenantsInstanceResolvesToNothingUntilReactivated() {
        setTenantStatus(TenantStatus.SUSPENDED);
        assertThat(directory.tenantOf(instanceId)).isEmpty();

        setTenantStatus(TenantStatus.ACTIVE);
        assertThat(directory.tenantOf(instanceId)).contains(tenant);
    }

    @Test
    void aClosedTenantsInstanceResolvesToNothing() {
        setTenantStatus(TenantStatus.CLOSED);

        assertThat(directory.tenantOf(instanceId)).isEmpty();
    }

    @Test
    void anInstanceOfATenantStillProvisioningResolvesToNothing() {
        TenantId provisioning = fixtures.provisionedTenant().id();
        String pending = EngineFixtures.uniqueInstanceId();
        instances.register(provisioning, pending, "https://p.engine.example", ACTOR, "trace");

        assertThat(directory.tenantOf(pending)).isEmpty();
    }
}
