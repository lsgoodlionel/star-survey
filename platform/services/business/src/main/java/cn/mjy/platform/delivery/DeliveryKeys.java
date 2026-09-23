package cn.mjy.platform.delivery;

import cn.mjy.platform.shared.TenantId;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.util.HexFormat;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * 触达相关密钥的唯一派生点。平台只保存一份主密钥（环境变量 {@code PLATFORM_DELIVERY_SECRET}，
 * 至少 32 字节），各用途、各租户的密钥都从它派生（沿用 {@link cn.mjy.platform.engine.EngineEventKeys} 的做法）：
 *
 * <pre>
 * 租户密钥 = HMAC-SHA256(主密钥, "mjy-delivery/v1/&lt;用途&gt;/" + 租户标识)
 * </pre>
 *
 * <p>三种用途互不相通：
 * <ul>
 *   <li>{@code link}——链接业务参数签名（{@link SignedParameters}）；</li>
 *   <li>{@code unsubscribe}——退订令牌；</li>
 *   <li>{@code receipt}——渠道商回执回调的共享密钥，按 (租户, 渠道商) 再派生一层，
 *       由租户管理员读出后配置到渠道商后台。</li>
 * </ul>
 * 一个租户、一个用途的密钥泄露，推不出别的租户或别的用途的密钥。
 *
 * <p>主密钥缺失时不阻止应用启动（其他模块照常服务），但一切签名与验签都拒绝——失败即关闭。
 */
@Component
public class DeliveryKeys {

    public static final String MASTER_SECRET_PROPERTY = "PLATFORM_DELIVERY_SECRET";
    static final int MIN_MASTER_SECRET_BYTES = 32;
    private static final String ALGORITHM = "HmacSHA256";
    private static final String PREFIX = "mjy-delivery/v1/";

    private final SecretKeySpec master;

    public DeliveryKeys(@Value("${" + MASTER_SECRET_PROPERTY + ":}") String masterSecret) {
        byte[] bytes = masterSecret == null ? new byte[0] : masterSecret.getBytes(StandardCharsets.UTF_8);
        this.master = bytes.length >= MIN_MASTER_SECRET_BYTES ? new SecretKeySpec(bytes, ALGORITHM) : null;
    }

    public boolean isConfigured() {
        return master != null;
    }

    /** 链接业务参数的签名密钥。 */
    public SecretKeySpec linkKey(TenantId tenant) {
        return derive("link/" + tenant.value());
    }

    /** 退订令牌的签名密钥。 */
    public SecretKeySpec unsubscribeKey(TenantId tenant) {
        return derive("unsubscribe/" + tenant.value());
    }

    /** 回执回调的共享密钥（十六进制串），交给渠道商配置；调用方负责不写日志。 */
    public String receiptSecret(TenantId tenant, String provider) {
        return HexFormat.of().formatHex(hmac(require(), PREFIX + "receipt/" + tenant.value() + "/" + provider));
    }

    /** 回执验签用的密钥：共享密钥十六进制串的 ASCII 字节（与大多数渠道商的 HMAC 约定一致）。 */
    public SecretKeySpec receiptKey(TenantId tenant, String provider) {
        return new SecretKeySpec(receiptSecret(tenant, provider).getBytes(StandardCharsets.US_ASCII), ALGORITHM);
    }

    private SecretKeySpec derive(String context) {
        return new SecretKeySpec(hmac(require(), PREFIX + context), ALGORITHM);
    }

    private SecretKeySpec require() {
        if (master == null) {
            throw new DeliveryUnavailableException(MASTER_SECRET_PROPERTY + " is not configured");
        }
        return master;
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
