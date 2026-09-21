package cn.mjy.platform.engine.support;

import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.util.HexFormat;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * 重现 {@code MjyHttpEventTransport::sign()}：
 * {@code hash_hmac('sha256', $timestamp . '.' . $body, $secret)}，输出小写十六进制。
 * 这里刻意独立于生产代码实现，契约测试才能发现两端的漂移。
 */
public final class PhpEventSigner {

    private PhpEventSigner() {
    }

    public static String sign(String secret, String timestamp, byte[] body) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            mac.update((timestamp + ".").getBytes(StandardCharsets.UTF_8));
            mac.update(body);
            return HexFormat.of().formatHex(mac.doFinal());
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException(e);
        }
    }
}
