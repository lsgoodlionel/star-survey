package cn.mjy.platform.delivery;

import cn.mjy.platform.shared.TenantId;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.util.Base64;
import java.util.Optional;
import java.util.UUID;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.stereotype.Component;

/**
 * 退订令牌：{@code base64url(租户|任务|收件人).base64url(HMAC)}，用租户自己的退订密钥签名。
 *
 * <p><b>令牌里不放邮箱或手机号</b>：退订地址会出现在邮件正文、浏览器地址栏、代理日志里，
 * 联系方式不该跟着到处走。令牌只带内部标识，地址由服务端按标识查出来。
 *
 * <p>令牌不设过期：退订链接在邮件里可能躺很久，到期反而会让人退不掉。
 * 令牌被他人拿到的后果是"替别人退订"——这是拒绝服务而不是信息泄露，且退订本身不泄漏任何内容。
 */
@Component
class UnsubscribeTokens {

    private static final String ALGORITHM = "HmacSHA256";
    private static final Base64.Encoder ENCODER = Base64.getUrlEncoder().withoutPadding();
    private static final Base64.Decoder DECODER = Base64.getUrlDecoder();
    /** 令牌总长上限，避免拿超长串做解析开销攻击。 */
    private static final int MAX_TOKEN_LENGTH = 256;

    /** 令牌指向的收件人。 */
    record Target(TenantId tenant, UUID taskId, UUID recipientId) {
    }

    private final DeliveryKeys keys;

    UnsubscribeTokens(DeliveryKeys keys) {
        this.keys = keys;
    }

    String issue(TenantId tenant, UUID taskId, UUID recipientId) {
        String payload = tenant.value() + "|" + taskId + "|" + recipientId;
        String encoded = ENCODER.encodeToString(payload.getBytes(StandardCharsets.UTF_8));
        return encoded + "." + ENCODER.encodeToString(sign(tenant, encoded));
    }

    /** 解析并验签；形状不对、签名不符一律返回空（调用方一律回同一个 404，不区分原因）。 */
    Optional<Target> parse(String token) {
        if (token == null || token.length() > MAX_TOKEN_LENGTH) {
            return Optional.empty();
        }
        int dot = token.indexOf('.');
        if (dot <= 0 || dot == token.length() - 1) {
            return Optional.empty();
        }
        String encoded = token.substring(0, dot);
        byte[] presented;
        String payload;
        try {
            presented = DECODER.decode(token.substring(dot + 1));
            payload = new String(DECODER.decode(encoded), StandardCharsets.UTF_8);
        } catch (IllegalArgumentException e) {
            return Optional.empty();
        }
        String[] parts = payload.split("\\|", -1);
        if (parts.length != 3) {
            return Optional.empty();
        }
        TenantId tenant;
        UUID taskId;
        UUID recipientId;
        try {
            tenant = TenantId.of(parts[0]);
            taskId = UUID.fromString(parts[1]);
            recipientId = UUID.fromString(parts[2]);
        } catch (IllegalArgumentException e) {
            return Optional.empty();
        }
        // 密钥按租户派生，所以"拿 A 租户的令牌去退 B 租户的订"验签一定不过。
        if (!MessageDigest.isEqual(sign(tenant, encoded), presented)) {
            return Optional.empty();
        }
        return Optional.of(new Target(tenant, taskId, recipientId));
    }

    private byte[] sign(TenantId tenant, String encoded) {
        try {
            SecretKeySpec key = keys.unsubscribeKey(tenant);
            Mac mac = Mac.getInstance(ALGORITHM);
            mac.init(key);
            return mac.doFinal(encoded.getBytes(StandardCharsets.US_ASCII));
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("HMAC-SHA256 unavailable", e);
        }
    }
}
