package cn.mjy.platform.shared.security;

import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * 链接与地址上的签名参数：平台签发、接收方不可篡改、不可增删、到期即失效。
 *
 * <p>本来是投放链接（R05-08）自己的东西，资产取件票（ADR 0019 决定 4）需要同一套语义，
 * 因此提到 {@code shared} 下——<b>签名实现只留一份</b>，两处共用同一批向量测试。：预填、归因等参数由平台签名，接收方不可篡改、不可增删、到期即失效。
 *
 * <p>规范串（签名对象）：
 * <pre>
 * 上下文 \n 过期时刻(秒) \n k1=v1&amp;k2=v2…（键按 UTF-8 字节序升序，键与值都做百分号编码）
 * </pre>
 * 键值都编码后再拼接，所以值里的 {@code &} 或 {@code =} 不可能伪造出另一组参数；
 * 上下文里带链接标识，因此一条链接的签名换到另一条链接上不成立。
 *
 * <p>签名是 base64url（无填充）的 HMAC-SHA256，用常量时间比较，避免逐字节猜。
 * 密钥按租户派生（见 {@link DeliveryKeys}），一个租户的密钥泄露不影响其他租户。
 */
public final class SignedParameters {

    public static final String SIGNATURE_PARAM = "sig";
    public static final String EXPIRY_PARAM = "exp";
    /** 参数名只允许字母数字、下划线与连字符，长度 1–32；避免在查询串里出现需要特别处理的字符。 */
    public static final String NAME_REGEX = "[A-Za-z][A-Za-z0-9_-]{0,31}";
    /** 单个参数值的长度上限，防止链接被撑爆。 */
    public static final int MAX_VALUE_LENGTH = 256;
    public static final int MAX_PARAMETERS = 16;

    private static final Set<String> RESERVED = Set.of(SIGNATURE_PARAM, EXPIRY_PARAM);
    private static final String ALGORITHM = "HmacSHA256";

    /** 校验结论；除 {@link #VALID} 外一律当作未签名参数处理。 */
    public enum Verdict {
        VALID,
        MISSING_SIGNATURE,
        MALFORMED,
        EXPIRED,
        BAD_SIGNATURE
    }

    private SignedParameters() {
    }

    public static boolean isReserved(String name) {
        return RESERVED.contains(name);
    }

    /**
     * 给一组业务参数签名，返回"业务参数 + exp + sig"的完整集合（可直接作为查询参数）。
     *
     * @throws IllegalArgumentException 参数名非法、用了保留名、数量或长度超限
     */
    public static Map<String, String> sign(SecretKeySpec key, String context, Map<String, String> params,
            Instant expiresAt) {
        Map<String, String> canonical = validated(params);
        long expiry = expiresAt.getEpochSecond();
        Map<String, String> signed = new LinkedHashMap<>(canonical);
        signed.put(EXPIRY_PARAM, Long.toString(expiry));
        signed.put(SIGNATURE_PARAM, encode(mac(key, canonicalString(context, expiry, canonical))));
        return Map.copyOf(signed);
    }

    /** 校验"业务参数 + exp + sig"的完整集合；{@code now} 用于判定过期（含等号那一秒仍有效）。 */
    public static Verdict verify(SecretKeySpec key, String context, Map<String, String> presented, Instant now) {
        String signature = presented.get(SIGNATURE_PARAM);
        if (signature == null || signature.isBlank()) {
            return Verdict.MISSING_SIGNATURE;
        }
        String expiryText = presented.get(EXPIRY_PARAM);
        if (expiryText == null) {
            return Verdict.MALFORMED;
        }
        long expiry;
        try {
            expiry = Long.parseLong(expiryText);
        } catch (NumberFormatException e) {
            return Verdict.MALFORMED;
        }
        Map<String, String> business = new TreeMap<>(presented);
        business.remove(SIGNATURE_PARAM);
        business.remove(EXPIRY_PARAM);
        byte[] expected = mac(key, canonicalString(context, expiry, business));
        byte[] actual = decode(signature);
        // 先比签名再看过期：过期与否本身不是秘密，但只有签名对了才值得给出 EXPIRED 这个更具体的结论。
        if (!MessageDigest.isEqual(expected, actual)) {
            return Verdict.BAD_SIGNATURE;
        }
        return now.getEpochSecond() > expiry ? Verdict.EXPIRED : Verdict.VALID;
    }

    /** 查询串形式（已排序、已编码），供拼接到作答链接后面。 */
    public static String toQueryString(Map<String, String> params) {
        StringBuilder query = new StringBuilder();
        new TreeMap<>(params).forEach((name, value) -> {
            if (!query.isEmpty()) {
                query.append('&');
            }
            query.append(percentEncode(name)).append('=').append(percentEncode(value));
        });
        return query.toString();
    }

    private static Map<String, String> validated(Map<String, String> params) {
        if (params.size() > MAX_PARAMETERS) {
            throw new IllegalArgumentException("at most " + MAX_PARAMETERS + " signed parameters are allowed");
        }
        Map<String, String> canonical = new TreeMap<>();
        params.forEach((name, value) -> {
            if (name == null || !name.matches(NAME_REGEX)) {
                throw new IllegalArgumentException("illegal parameter name: " + name);
            }
            if (isReserved(name)) {
                throw new IllegalArgumentException("parameter name is reserved: " + name);
            }
            if (value == null || value.length() > MAX_VALUE_LENGTH) {
                throw new IllegalArgumentException("parameter " + name + " must be at most "
                        + MAX_VALUE_LENGTH + " characters");
            }
            canonical.put(name, value);
        });
        return canonical;
    }

    private static String canonicalString(String context, long expiry, Map<String, String> params) {
        StringBuilder canonical = new StringBuilder(context).append('\n').append(expiry).append('\n');
        boolean first = true;
        for (Map.Entry<String, String> entry : new TreeMap<>(params).entrySet()) {
            if (!first) {
                canonical.append('&');
            }
            first = false;
            canonical.append(percentEncode(entry.getKey())).append('=').append(percentEncode(entry.getValue()));
        }
        return canonical.toString();
    }

    /** {@code application/x-www-form-urlencoded} 之上把 {@code +} 换回 {@code %20}，让查询串直接可用。 */
    static String percentEncode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8).replace("+", "%20");
    }

    private static byte[] mac(SecretKeySpec key, String message) {
        try {
            Mac mac = Mac.getInstance(ALGORITHM);
            mac.init(key);
            return mac.doFinal(message.getBytes(StandardCharsets.UTF_8));
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("HMAC-SHA256 unavailable", e);
        }
    }

    private static String encode(byte[] signature) {
        return Base64.getUrlEncoder().withoutPadding().encodeToString(signature);
    }

    private static byte[] decode(String signature) {
        try {
            return Base64.getUrlDecoder().decode(signature);
        } catch (IllegalArgumentException e) {
            return new byte[0];
        }
    }
}
