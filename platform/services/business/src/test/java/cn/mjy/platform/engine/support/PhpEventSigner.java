package cn.mjy.platform.engine.support;

import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.util.HexFormat;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * 重现 {@code MjyHttpEventTransport::sign()}：
 * {@code hash_hmac('sha256', $timestamp . '.' . $body, $secret)}，输出小写十六进制。
 * 其中 {@code $secret} 是该实例的派生密钥（十六进制字符串本身作为 HMAC 密钥字节）。
 * 这里刻意独立于生产代码实现，契约测试才能发现两端的漂移。
 */
public final class PhpEventSigner {

    /** 派生上下文前缀，与 ADR 0003 记录的契约一致。 */
    public static final String DERIVATION_PREFIX = "mjy-engine-events/v1/";

    private PhpEventSigner() {
    }

    public static String sign(String secret, String timestamp, byte[] body) {
        Mac mac = hmac(secret);
        mac.update((timestamp + ".").getBytes(StandardCharsets.UTF_8));
        mac.update(body);
        return HexFormat.of().formatHex(mac.doFinal());
    }

    /**
     * 运营为某实例签发的密钥：{@code hex(HMAC-SHA256(master, "mjy-engine-events/v1/" + instanceId))}。
     * 等价于 PHP 的 {@code hash_hmac('sha256', 'mjy-engine-events/v1/' . $instanceId, $master)}。
     */
    public static String deriveInstanceSecret(String masterSecret, String engineInstanceId) {
        Mac mac = hmac(masterSecret);
        mac.update((DERIVATION_PREFIX + engineInstanceId).getBytes(StandardCharsets.UTF_8));
        return HexFormat.of().formatHex(mac.doFinal());
    }

    private static Mac hmac(String key) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(key.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            return mac;
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException(e);
        }
    }
}
