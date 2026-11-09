package cn.mjy.platform.identity.org;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.shared.TenantId;
import java.util.UUID;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.io.TempDir;
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
    @Test
    void directoryIsAuthoritativeAndTenantScoped(@TempDir Path directory) throws Exception {
        MockEnvironment env = new MockEnvironment()
                .withProperty("PLATFORM_ORG_SECRET_DIR", directory.toString())
                .withProperty(EnvironmentSecretResolver.variableName(TENANT, "WECOM"), "stale-env");
        EnvironmentSecretResolver resolver = new EnvironmentSecretResolver(env);
        assertThat(resolver.resolve(TENANT, "WECOM")).isEmpty();
        Files.writeString(directory.resolve(EnvironmentSecretResolver.variableName(TENANT, "WECOM")), "synthetic-secret\n");
        assertThat(resolver.resolve(TENANT, "WECOM")).contains("synthetic-secret");
        assertThat(resolver.resolve(TenantId.random(), "WECOM")).isEmpty();
        assertThat(resolver.resolve(TENANT, "../WECOM")).isEmpty();
    }

    @Test
    void legacyEmptyDirectoryConfigurationDoesNotFallBackToEnvironment() {
        String name = EnvironmentSecretResolver.variableName(TENANT, "WECOM");
        MockEnvironment env = new MockEnvironment().withProperty("PLATFORM_ORG_SECRET_DIR", "")
                .withProperty(name, "stale-env");
        assertThat(new EnvironmentSecretResolver(env).resolve(TENANT, "WECOM")).isEmpty();
    }

    @Test
    void unsafeOrMalformedFilesFailClosedWithoutEnvironmentFallback(@TempDir Path directory) throws Exception {
        String name = EnvironmentSecretResolver.variableName(TENANT, "WECOM");
        MockEnvironment env = new MockEnvironment().withProperty("PLATFORM_ORG_SECRET_DIR", directory.toString())
                .withProperty(name, "stale-env");
        EnvironmentSecretResolver resolver = new EnvironmentSecretResolver(env);
        Path file = directory.resolve(name);
        Path target = directory.resolve("target");
        Files.writeString(target, "synthetic-secret");
        Files.createSymbolicLink(file, target);
        assertThat(resolver.resolve(TENANT, "WECOM")).isEmpty();
        Files.delete(file);
        Files.writeString(file, "x".repeat(4097));
        assertThat(resolver.resolve(TENANT, "WECOM")).isEmpty();
        Files.writeString(file, "two\nlines");
        assertThat(resolver.resolve(TENANT, "WECOM")).isEmpty();
        Files.write(file, new byte[] {(byte) 0xff});
        assertThat(resolver.resolve(TENANT, "WECOM")).isEmpty();
    }

}
