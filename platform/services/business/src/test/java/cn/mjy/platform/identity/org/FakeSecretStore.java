package cn.mjy.platform.identity.org;

import cn.mjy.platform.shared.TenantId;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.context.annotation.Primary;
import org.springframework.stereotype.Component;

/** 测试用密钥库：按 (租户, 密钥名) 存放，与生产解析规则一致——别的租户用同一个名字解析不到。 */
@Primary
@Component
public class FakeSecretStore implements SecretResolver {

    private final Map<String, String> secrets = new ConcurrentHashMap<>();

    public void put(TenantId tenant, String secretRef, String value) {
        secrets.put(tenant + "|" + secretRef, value);
    }

    @Override
    public Optional<String> resolve(TenantId tenant, String secretRef) {
        return Optional.ofNullable(secrets.get(tenant + "|" + secretRef));
    }
}
