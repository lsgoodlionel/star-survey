package cn.mjy.platform.survey.gateway;

import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.util.HexFormat;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * 契约规定的请求签名：{@code X-Pubgw-Signature = hex(HMAC-SHA256(secret, "<timestamp>.<原始请求体>"))}。
 * 签名按实际发送的字节计算，绝不对重新序列化后的 JSON 计算。
 */
public final class PublishGatewaySigner {

    private static final String ALGORITHM = "HmacSHA256";

    private PublishGatewaySigner() {
    }

    public static String sign(byte[] secret, String timestamp, byte[] body) {
        try {
            Mac mac = Mac.getInstance(ALGORITHM);
            mac.init(new SecretKeySpec(secret, ALGORITHM));
            mac.update((timestamp + ".").getBytes(StandardCharsets.US_ASCII));
            mac.update(body);
            return HexFormat.of().formatHex(mac.doFinal());
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("HMAC-SHA256 unavailable", e);
        }
    }
}
