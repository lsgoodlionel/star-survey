package cn.mjy.platform.tenant.routing;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.tenant.TenantFixtures;
import cn.mjy.platform.tenant.api.ConflictException;
import cn.mjy.platform.tenant.engine.EngineFixtures;
import cn.mjy.platform.tenant.engine.EngineInstanceService;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

/**
 * 重新发布的路由切换（ADR 0012 决定 2）：公开 UUID 的当前路由原子地从旧 sid 换到新 sid；
 * 旧 (实例, sid) 仍能反查到公开 UUID（旧版本的答卷要认得回来），且被取代的路由不能"复活"。
 */
@SpringBootTest
class SurveyRouteSwitchTest {

    @Autowired
    private SurveyRouteService routes;

    @Autowired
    private EngineInstanceService instances;

    @Autowired
    private TenantFixtures fixtures;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private TenantId tenant;
    private String instance;
    private UUID publicId;

    @BeforeEach
    void aPublishedRoute() {
        tenant = fixtures.activeTenant();
        instance = EngineFixtures.uniqueInstanceId();
        instances.register(tenant, instance, "https://engine.example/" + instance, "op", "trace");
        publicId = UUID.randomUUID();
        routes.registerPublished(tenant, publicId, instance, 4001, "user", "trace");
    }

    @Test
    void switchingMovesThePublicIdToTheNewEngineSurvey() {
        SurveyRoute switched = routes.switchPublished(tenant, publicId, instance, 4001, instance, 4002, "user", "t");

        assertThat(switched.engineSid()).isEqualTo(4002);
        assertThat(routes.findByPublicId(tenant, publicId)).get()
                .extracting(SurveyRoute::engineSid).isEqualTo(4002);
    }

    @Test
    void theOldEngineSurveyStillResolvesToThePublicIdAfterTheSwitch() {
        routes.switchPublished(tenant, publicId, instance, 4001, instance, 4002, "user", "t");

        assertThat(routes.findByEngine(tenant, instance, 4001)).get()
                .extracting(SurveyRoute::publicId).isEqualTo(publicId);
        assertThat(routes.findByEngine(tenant, instance, 4002)).get()
                .extracting(SurveyRoute::publicId).isEqualTo(publicId);
    }

    @Test
    void switchingAgainToTheSameTargetIsIdempotent() {
        SurveyRoute first = routes.switchPublished(tenant, publicId, instance, 4001, instance, 4002, "user", "t");

        assertThat(routes.switchPublished(tenant, publicId, instance, 4001, instance, 4002, "user", "t"))
                .isEqualTo(first);
    }

    @Test
    void switchingFromASidThatIsNoLongerCurrentIsAConflict() {
        routes.switchPublished(tenant, publicId, instance, 4001, instance, 4002, "user", "t");

        assertThatThrownBy(() -> routes.switchPublished(tenant, publicId, instance, 4001, instance, 4003, "u", "t"))
                .isInstanceOf(ConflictException.class);
        assertThat(routes.findByPublicId(tenant, publicId)).get()
                .extracting(SurveyRoute::engineSid).isEqualTo(4002);
    }

    @Test
    void aTargetAlreadyRoutedElsewhereIsAConflictAndTheOldRouteStaysCurrent() {
        routes.registerPublished(tenant, UUID.randomUUID(), instance, 4009, "user", "trace");

        assertThatThrownBy(() -> routes.switchPublished(tenant, publicId, instance, 4001, instance, 4009, "u", "t"))
                .isInstanceOf(ConflictException.class);
        assertThat(routes.findByPublicId(tenant, publicId)).get()
                .extracting(SurveyRoute::engineSid).isEqualTo(4001);
    }

    @Test
    void aSupersededRouteCannotBeMadeCurrentAgain() {
        routes.switchPublished(tenant, publicId, instance, 4001, instance, 4002, "user", "t");

        assertThatThrownBy(() -> tenantScope.run(tenant, () -> jdbc
                .sql("UPDATE survey_route SET superseded_at = NULL WHERE engine_instance_id = :i AND engine_sid = 4001")
                .param("i", instance).update()))
                .rootCause().hasMessageContaining("superseded");
    }

    @Test
    void thereIsAtMostOneCurrentRoutePerPublicId() {
        assertThatThrownBy(() -> tenantScope.run(tenant, () -> jdbc.sql("""
                        INSERT INTO survey_route (public_id, tenant_id, engine_instance_id, engine_sid)
                        VALUES (:id, :tenant, :instance, 4077)
                        """)
                .param("id", publicId).param("tenant", tenant.value()).param("instance", instance).update()))
                .rootCause().hasMessageContaining("survey_route_current");
    }

    @Test
    void theRuntimeAccountStillCannotRewriteWhereARoutePoints() {
        assertThatThrownBy(() -> tenantScope.run(tenant, () -> jdbc
                .sql("UPDATE survey_route SET engine_sid = 1 WHERE public_id = :id")
                .param("id", publicId).update()))
                .rootCause().hasMessageContaining("permission denied");
    }
}
