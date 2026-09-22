package cn.mjy.platform.tenant.routing;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.tenant.TenantFixtures;
import cn.mjy.platform.tenant.api.ConflictException;
import cn.mjy.platform.tenant.api.NotFoundException;
import cn.mjy.platform.tenant.engine.EngineFixtures;
import cn.mjy.platform.tenant.engine.EngineInstanceService;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/** 发布流程按问卷自己的公开 UUID 登记路由（而不是由路由服务随机生成）。 */
@SpringBootTest
class SurveyRouteRegistrationTest {

    @Autowired
    private SurveyRouteService routes;

    @Autowired
    private EngineInstanceService instances;

    @Autowired
    private TenantFixtures fixtures;

    private TenantId tenant;
    private String instance;

    @BeforeEach
    void setUp() {
        tenant = fixtures.activeTenant();
        instance = EngineFixtures.uniqueInstanceId();
        instances.register(tenant, instance, "https://engine.example/" + instance, "op", "trace");
    }

    @Test
    void aRouteIsRegisteredUnderTheGivenPublicId() {
        UUID publicId = UUID.randomUUID();

        SurveyRoute route = routes.registerPublished(tenant, publicId, instance, 3001, "user", "trace");

        assertThat(route.publicId()).isEqualTo(publicId);
        assertThat(routes.findByPublicId(tenant, publicId)).contains(route);
    }

    @Test
    void registeringTheSameRouteAgainIsIdempotent() {
        UUID publicId = UUID.randomUUID();
        SurveyRoute first = routes.registerPublished(tenant, publicId, instance, 3002, "user", "trace");

        assertThat(routes.registerPublished(tenant, publicId, instance, 3002, "user", "trace")).isEqualTo(first);
    }

    @Test
    void aPublicIdAlreadyPointingElsewhereIsAConflict() {
        UUID publicId = UUID.randomUUID();
        routes.registerPublished(tenant, publicId, instance, 3003, "user", "trace");

        assertThatThrownBy(() -> routes.registerPublished(tenant, publicId, instance, 3004, "user", "trace"))
                .isInstanceOf(ConflictException.class);
    }

    @Test
    void anEngineSurveyAlreadyRoutedUnderAnotherPublicIdIsAConflict() {
        routes.registerPublished(tenant, UUID.randomUUID(), instance, 3005, "user", "trace");

        assertThatThrownBy(() -> routes.registerPublished(tenant, UUID.randomUUID(), instance, 3005, "user", "trace"))
                .isInstanceOf(ConflictException.class);
    }

    @Test
    void anotherTenantsInstanceIsNotFound() {
        TenantId other = fixtures.activeTenant();

        assertThatThrownBy(() -> routes.registerPublished(other, UUID.randomUUID(), instance, 3006, "user", "trace"))
                .isInstanceOf(NotFoundException.class);
    }
}
