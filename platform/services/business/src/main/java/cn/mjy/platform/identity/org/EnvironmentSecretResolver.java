package cn.mjy.platform.identity.org;

import cn.mjy.platform.shared.TenantId;
import java.util.Locale;
import java.util.Optional;
import java.util.regex.Pattern;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;

/**
 * 从运行环境读取密钥：变量名 {@code PLATFORM_ORG_SECRET_<租户标识去横线大写>_<密钥名>}。
 * 例：租户 3f2a0c1e-… 的密钥名 WECOM → {@code PLATFORM_ORG_SECRET_3F2A0C1E…_WECOM}。
 * 环境由运维或密钥库（Vault Agent、K8s Secret 等注入环境变量）提供；换密钥库时实现本接口另一个 bean 即可。
 */
@Component
class EnvironmentSecretResolver implements SecretResolver {

    static final String PREFIX = "PLATFORM_ORG_SECRET_";
    private static final Pattern REF = Pattern.compile("[A-Z][A-Z0-9_]{0,63}");

    private final Environment environment;

    EnvironmentSecretResolver(Environment environment) {
        this.environment = environment;
    }

    @Override
    public Optional<String> resolve(TenantId tenant, String secretRef) {
        if (secretRef == null || !REF.matcher(secretRef).matches()) {
            return Optional.empty();
        }
        String value = environment.getProperty(variableName(tenant, secretRef));
        return value == null || value.isBlank() ? Optional.empty() : Optional.of(value);
    }

    static String variableName(TenantId tenant, String secretRef) {
        return PREFIX + tenant.value().toString().replace("-", "").toUpperCase(Locale.ROOT) + "_" + secretRef;
    }
}
