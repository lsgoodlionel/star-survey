package cn.mjy.platform.tenant;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.support.TestTokens;
import java.util.List;
import java.util.UUID;
import org.springframework.stereotype.Component;

/** 本车道测试的公共夹具：签发运营/租户令牌，直接经服务层开通租户。 */
@Component
public class TenantFixtures {

    public static final String OPERATOR_ACTOR = "operator-1";

    private final TestTokens tokens;
    private final TenantService tenants;

    public TenantFixtures(TestTokens tokens, TenantService tenants) {
        this.tokens = tokens;
        this.tenants = tenants;
    }

    /** 运营令牌：带平台运营角色。所带的租户声明对控制平面没有意义，只是为了令牌格式一致。 */
    public String operatorBearer() {
        return "Bearer " + tokens.issue(OPERATOR_ACTOR, UUID.randomUUID(), List.of(PlatformRoles.OPERATOR));
    }

    /** 普通租户用户令牌：即使角色名看起来很"大"，也不含平台运营角色。 */
    public String userBearer(TenantId tenant) {
        return "Bearer " + tokens.issue("user-" + tenant, tenant.value(), List.of("admin", "editor"));
    }

    public static String uniqueCode(String prefix) {
        return prefix + "-" + UUID.randomUUID().toString().substring(0, 8);
    }

    public Tenant provisionedTenant() {
        return tenants.create(uniqueCode("t"), "测试租户", OPERATOR_ACTOR, "trace-fixture");
    }

    public TenantId activeTenant() {
        Tenant tenant = provisionedTenant();
        tenants.changeStatus(tenant.id(), TenantStatus.ACTIVE, OPERATOR_ACTOR, "trace-fixture");
        return tenant.id();
    }
}
