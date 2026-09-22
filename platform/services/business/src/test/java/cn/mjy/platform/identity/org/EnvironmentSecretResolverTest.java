package cn.mjy.platform.identity.org;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.shared.TenantId;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.mock.env.MockEnvironment;

/** 生产密钥解析：变量名 = PLATFORM_ORG_SECRET_<租户标识去横线大写>_<密钥名>，别的租户用同一名字解析不到。 */
class EnvironmentSecretResolverTest {

    private static final TenantId TENANT = new TenantId(UUID.fromString("3f2a0c1e-7b4d-4e5f-9a8b-0c1d2e3f4a5b"));

    @Test
    void resolvesTheTenantScopedVariable() {
        MockEnvironment env = new MockEnvironment()
                .withProperty("PLATFORM_ORG_SECRET_3F2A0C1E7B4D4E5F9A8B0C1D2E3F4A5B_WECOM", "the-secret");

        assertThat(new EnvironmentSecretResolver(env).resolve(TENANT, "WECOM")).contains("the-secret");
    }

    @Test
    void anotherTenantCannotResolveTheSameName() {
        MockEnvironment env = new MockEnvironment()
                .withProperty("PLATFORM_ORG_SECRET_3F2A0C1E7B4D4E5F9A8B0C1D2E3F4A5B_WECOM", "the-secret");

        assertThat(new EnvironmentSecretResolver(env).resolve(TenantId.random(), "WECOM")).isEmpty();
    }

    @Test
    void blankValuesAndIllegalNamesResolveToNothing() {
        MockEnvironment env = new MockEnvironment()
                .withProperty("PLATFORM_ORG_SECRET_3F2A0C1E7B4D4E5F9A8B0C1D2E3F4A5B_EMPTY", " ")
                .withProperty("PLATFORM_DB_OWNER_PASSWORD", "db");
        EnvironmentSecretResolver resolver = new EnvironmentSecretResolver(env);

        assertThat(resolver.resolve(TENANT, "EMPTY")).isEmpty();
        assertThat(resolver.resolve(TENANT, "../PLATFORM_DB_OWNER_PASSWORD")).isEmpty();
        assertThat(resolver.resolve(TENANT, "lower")).isEmpty();
    }
}
