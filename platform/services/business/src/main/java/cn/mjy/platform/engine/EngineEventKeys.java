package cn.mjy.platform.engine;

import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.util.HexFormat;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * 引擎实例密钥的唯一派生点：验签（{@link EventSignatureVerifier}）与运营签发（{@link EngineEventSecretController}）
 * 都只经由这里取得实例密钥。
 *
 * <p>契约（v1）：
 * <pre>
 * 实例密钥 = hex(HMAC-SHA256(主密钥, "mjy-engine-events/v1/" + engineInstanceId))   // 64 位小写十六进制
 * </pre>
 * 平台只保存一份主密钥（环境变量 {@code PLATFORM_ENGINE_EVENTS_SECRET}，至少 32 字节）；每个引擎实例只拿到
 * 自己的实例密钥，配置为 {@code MJY_PLATFORM_EVENTS_SECRET}。PHP 端 {@code hash_hmac} 以字符串作密钥，
 * 所以签名时的 HMAC 密钥字节就是这 64 个十六进制字符的 ASCII 字节——验签端必须同样取 ASCII 字节，而不是解码后的 32 字节。
 *
 * <p>一个实例的密钥泄露，只能冒充该实例本身；拿不到主密钥就推不出别的实例的密钥。
 */
@Component
public class EngineEventKeys {

    public static final String MASTER_SECRET_PROPERTY = "PLATFORM_ENGINE_EVENTS_SECRET";
    /** 派生上下文；将来换算法时升版本号，新旧密钥互不相同。 */
    static final String DERIVATION_PREFIX = "mjy-engine-events/v1/";
    static final int MIN_MASTER_SECRET_BYTES = 32;
    static final String ALGORITHM = "HmacSHA256";

    private final SecretKeySpec master;

    public EngineEventKeys(@Value("${" + MASTER_SECRET_PROPERTY + ":}") String masterSecret) {
        byte[] bytes = masterSecret == null ? new byte[0] : masterSecret.getBytes(StandardCharsets.UTF_8);
        this.master = bytes.length >= MIN_MASTER_SECRET_BYTES ? new SecretKeySpec(bytes, ALGORITHM) : null;
    }

    /** 主密钥缺失或不足 32 字节时为 false：此时不派生任何密钥，接收端拒收一切（失败即关闭）。 */
    public boolean isConfigured() {
        return master != null;
    }

    /**
     * 某实例的密钥（小写十六进制），即引擎侧应配置的 {@code MJY_PLATFORM_EVENTS_SECRET}。
     * 调用方负责保密：不得写日志。
     *
     * @throws IllegalStateException 主密钥未配置
     */
    public String derivedSecret(String engineInstanceId) {
        if (master == null) {
            throw new IllegalStateException(MASTER_SECRET_PROPERTY + " is not configured");
        }
        byte[] context = (DERIVATION_PREFIX + engineInstanceId).getBytes(StandardCharsets.UTF_8);
        return HexFormat.of().formatHex(hmac(master, context));
    }

    /** 实例的 HMAC 签名密钥：实例密钥十六进制串的 ASCII 字节（与 PHP {@code hash_hmac} 一致）。 */
    SecretKeySpec signingKey(String engineInstanceId) {
        return new SecretKeySpec(derivedSecret(engineInstanceId).getBytes(StandardCharsets.US_ASCII), ALGORITHM);
    }

    private static byte[] hmac(SecretKeySpec key, byte[] data) {
        try {
            Mac mac = Mac.getInstance(ALGORITHM);
            mac.init(key);
            return mac.doFinal(data);
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("HMAC-SHA256 unavailable", e);
        }
    }
}
