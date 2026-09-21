package cn.mjy.platform.tenant.routing;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.tenant.TenantFixtures;
import cn.mjy.platform.tenant.engine.EngineFixtures;
import cn.mjy.platform.tenant.engine.EngineInstanceService;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

/** survey_route 是租户表：跨租户读写由数据库拒绝；路由也不能指向别的租户的引擎实例。 */
@SpringBootTest
class SurveyRouteIsolationTest {

    @Autowired
    private SurveyRouteService routes;

    @Autowired
    private SurveyRouteRepository repository;

    @Autowired
    private EngineInstanceService instances;

    @Autowired
    private TenantFixtures fixtures;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private TenantId tenantA;
    private TenantId tenantB;
    private String instanceOfA;
    private SurveyRoute routeOfA;

    @BeforeEach
    void setUp() {
        tenantA = fixtures.activeTenant();
        tenantB = fixtures.activeTenant();
        instanceOfA = EngineFixtures.uniqueInstanceId();
        instances.register(tenantA, instanceOfA, "https://a.engine.example", "op", "trace");
        routeOfA = routes.create(tenantA, instanceOfA, 1001, "user-a", "trace").value();
    }

    private void insertRaw(TenantId scope, TenantId rowTenant, String instanceId) {
        tenantScope.run(scope, () -> jdbc.sql("""
                        INSERT INTO survey_route (public_id, tenant_id, engine_instance_id, engine_sid)
                        VALUES (:id, :tenant, :instance, 2002)
                        """)
                .param("id", UUID.randomUUID())
                .param("tenant", rowTenant.value())
                .param("instance", instanceId)
                .update());
    }

    @Test
    void tenantBCannotReadTenantAsRoutes() {
        Optional<SurveyRoute> seenByB = tenantScope.call(tenantB, () -> repository.findByPublicId(routeOfA.publicId()));
        Optional<SurveyRoute> seenByA = tenantScope.call(tenantA, () -> repository.findByPublicId(routeOfA.publicId()));

        assertThat(seenByB).isEmpty();
        assertThat(seenByA).contains(routeOfA);
        assertThat(routes.findByPublicId(tenantB, routeOfA.publicId())).isEmpty();
    }

    @Test
    void tenantBInsertingARouteForTenantAIsRejectedByTheDatabase() {
        assertThatThrownBy(() -> insertRaw(tenantB, tenantA, instanceOfA))
                .rootCause().hasMessageContaining("row-level security");
    }

    @Test
    void aRouteCannotPointAtAnotherTenantsInstanceEvenWithACorrectTenantId() {
        assertThatThrownBy(() -> insertRaw(tenantB, tenantB, instanceOfA))
                .rootCause().hasMessageContaining("foreign key");
    }

    @Test
    void tenantBUpdatingOrDeletingTenantAsRouteIsRejected() {
        assertThatThrownBy(() -> tenantScope.run(tenantB, () -> jdbc
                .sql("UPDATE survey_route SET engine_sid = 1 WHERE public_id = :id")
                .param("id", routeOfA.publicId()).update()))
                .rootCause().hasMessageContaining("permission denied");
        assertThatThrownBy(() -> tenantScope.run(tenantB, () -> jdbc
                .sql("DELETE FROM survey_route WHERE public_id = :id")
                .param("id", routeOfA.publicId()).update()))
                .rootCause().hasMessageContaining("permission denied");
    }

    @Test
    void withoutATenantContextNoRouteIsVisible() {
        assertThat(jdbc.sql("SELECT COUNT(*) FROM survey_route").query(Long.class).single()).isZero();
    }
}
