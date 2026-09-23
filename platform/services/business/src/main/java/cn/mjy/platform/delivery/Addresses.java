package cn.mjy.platform.delivery;

import java.util.regex.Pattern;

/**
 * 收件地址的形状校验（系统边界上的输入校验）。
 *
 * <p>邮箱只做保守的结构校验，不追求 RFC 5322 全集：过于宽松的正则会把明显的错别字放进名单，
 * 过于严格又会误伤合法地址，真正的判定交给渠道商的回执（退信）。
 * 手机号按中国大陆号段：11 位、1 开头、第二位 3–9；带 {@code +86} 前缀时先剥掉。
 *
 * <p>校验失败的错误信息里<b>不回显地址本身</b>，只说第几行不合法。
 */
final class Addresses {

    private static final Pattern EMAIL =
            Pattern.compile("[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64}@[A-Za-z0-9-]{1,63}(\\.[A-Za-z0-9-]{1,63})+");
    private static final Pattern PHONE = Pattern.compile("1[3-9]\\d{9}");

    private Addresses() {
    }

    /** 归一化并校验；返回入库用的地址。 */
    static String normalise(DeliveryChannel channel, String raw, int index) {
        if (raw == null || raw.isBlank()) {
            throw DeliveryExceptions.invalid("recipient " + index + " has no address");
        }
        String trimmed = raw.trim();
        return switch (channel) {
            case EMAIL -> {
                String lower = trimmed.toLowerCase(java.util.Locale.ROOT);
                if (lower.length() > 320 || !EMAIL.matcher(lower).matches()) {
                    throw DeliveryExceptions.invalid("recipient " + index + " is not a valid email address");
                }
                yield lower;
            }
            case SMS -> {
                String digits = trimmed.startsWith("+86") ? trimmed.substring(3) : trimmed;
                digits = digits.replace(" ", "").replace("-", "");
                if (!PHONE.matcher(digits).matches()) {
                    throw DeliveryExceptions.invalid("recipient " + index + " is not a valid mobile number");
                }
                yield digits;
            }
        };
    }
}
