package cn.mjy.platform.asset;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.security.SignedParameters;
import java.time.Instant;
import java.util.Map;
import java.util.UUID;
import org.springframework.stereotype.Component;

/**
 * 取件票的签发与验签（ADR 0019 决定 4）。
 *
 * <p>签名上下文覆盖<b>租户、资产、版本</b>三者：
 *
 * <pre>
 * asset/v1/&lt;租户 uuid&gt;/&lt;资产 uuid&gt;/&lt;版本号&gt;
 * </pre>
 *
 * 改其中任何一项都验不过——A 租户的票改不去取 B 租户的资产，第 1 版的票改不去取第 2 版。
 * 业务参数集合是<b>空的</b>：地址上除了 {@code exp} 与 {@code sig} 不该再有别的东西，
 * 多带一个参数就会落进签名比对里，直接变成 BAD_SIGNATURE。
 *
 * <p>签名机制本身不在这里，在 {@link SignedParameters}——投放链接与取件票共用同一份实现与同一批向量测试。
 */
@Component
public class AssetTickets {

    static final String PATH_PREFIX = "/a/";

    /** 校验结论。{@link #VALID} 之外一律不许取到字节。 */
    public enum Verdict {
        VALID,
        EXPIRED,
        BAD_SIGNATURE
    }

    private final AssetKeys keys;

    AssetTickets(AssetKeys keys) {
        this.keys = keys;
    }

    public boolean isConfigured() {
        return keys.isConfigured();
    }

    public AssetTicket mint(TenantId tenant, UUID assetId, int versionNo, Instant expiresAt) {
        Map<String, String> signed = SignedParameters.sign(keys.fetchKey(tenant),
                context(tenant, assetId, versionNo), Map.of(), expiresAt);
        return new AssetTicket(path(tenant, assetId, versionNo), signed);
    }

    /**
     * 验签。主密钥没配时返回 {@link Verdict#BAD_SIGNATURE} 而不是抛异常——
     * 匿名可达面上"没配密钥"与"签名不对"必须是同一个结局（ADR 0018 决定 5）。
     */
    public Verdict verify(TenantId tenant, UUID assetId, int versionNo, Map<String, String> presented, Instant now) {
        if (!keys.isConfigured()) {
            return Verdict.BAD_SIGNATURE;
        }
        SignedParameters.Verdict verdict = SignedParameters.verify(keys.fetchKey(tenant),
                context(tenant, assetId, versionNo), presented, now);
        return switch (verdict) {
            case VALID -> Verdict.VALID;
            case EXPIRED -> Verdict.EXPIRED;
            case MISSING_SIGNATURE, MALFORMED, BAD_SIGNATURE -> Verdict.BAD_SIGNATURE;
        };
    }

    static String path(TenantId tenant, UUID assetId, int versionNo) {
        return PATH_PREFIX + tenant.value() + "/" + assetId + "/" + versionNo;
    }

    private static String context(TenantId tenant, UUID assetId, int versionNo) {
        return "asset/v1/" + tenant.value() + "/" + assetId + "/" + versionNo;
    }
}
