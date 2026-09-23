package cn.mjy.platform.contacts;

import com.fasterxml.jackson.annotation.JsonValue;
import java.util.Locale;
import java.util.Optional;
import java.util.regex.Pattern;

/**
 * 名单的去重键（ADR 0017 决定 2）。建名单时由租户选定、之后不可改：
 * 换键等于重新划分已有行的去重分组，只能新建名单再导入。
 *
 * <p>去重比较的是**规范化值**：邮箱去空白并转小写；手机号去掉空格、连字符与括号，保留可选的 +；
 * 工号去空白、区分大小写；外部身份是 {@code provider|appId|externalId} 三段拼接——
 * app_id 进入键，所以"同一 openid 出现在两个应用下"永远是两个人。
 */
public enum DedupeKey {

    EMAIL,
    PHONE,
    EMPLOYEE_ID,
    EXTERNAL_ID;

    private static final Pattern EMAIL_FORMAT = Pattern.compile("^[^@\\s,;]+@[^@\\s,;]+\\.[^@\\s,;]{2,}$");
    private static final Pattern PHONE_FORMAT = Pattern.compile("^\\+?[0-9]{5,20}$");
    private static final Pattern EMPLOYEE_FORMAT = Pattern.compile("^[A-Za-z0-9._-]{1,64}$");
    private static final Pattern PHONE_NOISE = Pattern.compile("[\\s()\\-.]");

    @JsonValue
    public String code() {
        return name().toLowerCase(Locale.ROOT);
    }

    public static Optional<DedupeKey> fromCode(String code) {
        if (code == null) {
            return Optional.empty();
        }
        for (DedupeKey key : values()) {
            if (key.code().equals(code)) {
                return Optional.of(key);
            }
        }
        return Optional.empty();
    }

    static DedupeKey fromDb(String code) {
        return fromCode(code).orElseThrow(() -> new IllegalStateException("unknown dedupe key: " + code));
    }

    /** 该行缺这个键时的错误码。 */
    String missingReason() {
        return "missing_dedupe_key";
    }

    /** 值格式不对时的错误码。 */
    String invalidReason() {
        return switch (this) {
            case EMAIL -> "invalid_email";
            case PHONE -> "invalid_phone";
            case EMPLOYEE_ID -> "invalid_employee_id";
            case EXTERNAL_ID -> "invalid_external_id";
        };
    }

    static String normalizeEmail(String raw) {
        String trimmed = trim(raw);
        return trimmed == null ? null : trimmed.toLowerCase(Locale.ROOT);
    }

    static String normalizePhone(String raw) {
        String trimmed = trim(raw);
        return trimmed == null ? null : PHONE_NOISE.matcher(trimmed).replaceAll("");
    }

    static String trim(String raw) {
        if (raw == null) {
            return null;
        }
        String trimmed = raw.trim();
        return trimmed.isEmpty() ? null : trimmed;
    }

    static boolean isEmail(String normalized) {
        return normalized != null && normalized.length() <= 320 && EMAIL_FORMAT.matcher(normalized).matches();
    }

    static boolean isPhone(String normalized) {
        return normalized != null && PHONE_FORMAT.matcher(normalized).matches();
    }

    static boolean isEmployeeId(String normalized) {
        return normalized != null && EMPLOYEE_FORMAT.matcher(normalized).matches();
    }
}
