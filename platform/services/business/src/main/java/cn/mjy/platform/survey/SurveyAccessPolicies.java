package cn.mjy.platform.survey;

import java.security.GeneralSecurityException;
import java.security.SecureRandom;
import java.time.DateTimeException;
import java.time.ZoneId;
import java.util.Arrays;
import java.util.Base64;
import java.util.List;
import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.PBEKeySpec;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * 定义里访问策略（{@code policy}，WP-04，ADR 0016，契约 survey-access-policy-v1）在平台侧的两件事：
 *
 * <ol>
 *   <li>明文访问密码 {@code access.password} 换成 {@code access.passwordHash}
 *       （{@code pbkdf2-sha256$<迭代>$<salt>$<hash>}），明文不落库、不进日志、不发往网关；</li>
 *   <li>时间窗没写时区时填入默认 {@code Asia/Shanghai}（租户级时区配置尚未提供）。</li>
 * </ol>
 *
 * <p>其余规则的语义校验由发布网关负责（422），平台不复制一份。选 PBKDF2 是因为 JDK、PHP、Python
 * 标准库都有，插件校验不需要额外依赖。
 */
final class SurveyAccessPolicies {

    static final String DEFAULT_TIMEZONE = "Asia/Shanghai";
    /** OWASP 2023 对 PBKDF2-HMAC-SHA256 的建议值。 */
    static final int ITERATIONS = 600_000;
    private static final int SALT_BYTES = 16;
    private static final int HASH_BITS = 256;
    private static final int MAX_PASSWORD_LENGTH = 128;
    private static final String SCHEME = "pbkdf2-sha256";
    private static final SecureRandom RANDOM = new SecureRandom();

    private SurveyAccessPolicies() {
    }

    /** 就地处理 {@code definition}（调用方传入的是自己的副本）；问题追加到 {@code problems}。 */
    static void normalize(ObjectNode definition, List<String> problems) {
        JsonNode policy = definition.get("policy");
        if (policy == null || policy.isNull()) {
            return;
        }
        if (!policy.isObject()) {
            problems.add("policy must be an object");
            return;
        }
        hashPassword(policy.get("access"), problems);
        defaultTimezone(policy.get("window"), problems);
    }

    private static void hashPassword(JsonNode access, List<String> problems) {
        if (!(access instanceof ObjectNode block) || !block.has("password")) {
            return;
        }
        JsonNode password = block.get("password");
        block.remove("password");
        if (password == null || !password.isString() || password.asString().isBlank()
                || password.asString().length() > MAX_PASSWORD_LENGTH) {
            // 不回显取值：错误信息会进应答与日志。
            problems.add("policy.access.password must be a non-blank string of at most "
                    + MAX_PASSWORD_LENGTH + " characters");
            return;
        }
        block.put("passwordHash", hash(password.asString()));
    }

    private static void defaultTimezone(JsonNode window, List<String> problems) {
        if (!(window instanceof ObjectNode block)) {
            return;
        }
        JsonNode timezone = block.get("timezone");
        if (timezone == null || timezone.isNull()) {
            block.put("timezone", DEFAULT_TIMEZONE);
            return;
        }
        if (!timezone.isString() || !isZone(timezone.asString())) {
            problems.add("policy.window.timezone must be an IANA time zone such as " + DEFAULT_TIMEZONE);
        }
    }

    private static boolean isZone(String name) {
        try {
            // 只认 IANA 区域名（含 "/" 或 UTC），不认 "+08:00" 这类固定偏移：偏移不带夏令时规则。
            return (name.contains("/") || "UTC".equals(name)) && ZoneId.of(name).getId().equals(name);
        } catch (DateTimeException e) {
            return false;
        }
    }

    static String hash(String password) {
        byte[] salt = new byte[SALT_BYTES];
        RANDOM.nextBytes(salt);
        char[] chars = password.toCharArray();
        PBEKeySpec spec = new PBEKeySpec(chars, salt, ITERATIONS, HASH_BITS);
        try {
            byte[] digest = SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).getEncoded();
            Base64.Encoder base64 = Base64.getEncoder();
            return SCHEME + "$" + ITERATIONS + "$" + base64.encodeToString(salt) + "$" + base64.encodeToString(digest);
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("PBKDF2WithHmacSHA256 is unavailable in this JVM", e);
        } finally {
            spec.clearPassword();
            Arrays.fill(chars, '\0');
        }
    }
}
