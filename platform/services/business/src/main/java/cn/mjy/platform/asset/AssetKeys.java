package cn.mjy.platform.asset;

import cn.mjy.platform.shared.TenantId;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * 资产取件票密钥的唯一派生点。平台只保存一份主密钥（环境变量 {@code PLATFORM_ASSET_SECRET}，
 * 至少 32 字节），每个租户的密钥都从它派生（沿用 {@code EngineEventKeys} / {@code DeliveryKeys} 的做法）：
 *
 * <pre>
 * 租户密钥 = HMAC-SHA256(主密钥, "mjy-asset/v1/fetch/" + 租户标识)
 * </pre>
 *
 * <p>一个租户的密钥泄露，推不出别的租户的。前缀里的 {@code v1} 让将来换算法时得到互不相干的一组密钥。
 *
 * <p>主密钥缺失时不阻止应用启动（其他模块照常服务），但签发与验签一律拒绝——失败即关闭。
 */
@Component
public class AssetKeys {

    public static final String MASTER_SECRET_PROPERTY = "PLATFORM_ASSET_SECRET";
    static final int MIN_MASTER_SECRET_BYTES = 32;
    private static final String ALGORITHM = "HmacSHA256";
    private static final String PREFIX = "mjy-asset/v1/";

    private final SecretKeySpec master;

    public AssetKeys(@Value("${" + MASTER_SECRET_PROPERTY + ":}") String masterSecret) {
        byte[] bytes = masterSecret == null ? new byte[0] : masterSecret.getBytes(StandardCharsets.UTF_8);
        this.master = bytes.length >= MIN_MASTER_SECRET_BYTES ? new SecretKeySpec(bytes, ALGORITHM) : null;
    }

    public boolean isConfigured() {
        return master != null;
    }

    /** 取件票的签名密钥。 */
    SecretKeySpec fetchKey(TenantId tenant) {
        if (master == null) {
            throw new AssetUnavailableException(MASTER_SECRET_PROPERTY + " is not configured");
        }
        return new SecretKeySpec(hmac(master, PREFIX + "fetch/" + tenant.value()), ALGORITHM);
    }

    private static byte[] hmac(SecretKeySpec key, String context) {
        try {
            Mac mac = Mac.getInstance(ALGORITHM);
            mac.init(key);
            return mac.doFinal(context.getBytes(StandardCharsets.UTF_8));
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("HMAC-SHA256 unavailable", e);
        }
    }
}
