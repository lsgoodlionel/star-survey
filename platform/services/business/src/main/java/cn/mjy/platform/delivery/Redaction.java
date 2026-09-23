package cn.mjy.platform.delivery;

/**
 * 联系方式脱敏。收件地址是租户数据：日志、错误信息、异常消息里一律只出现脱敏形式。
 * 脱敏保留足够的形状供排查（域名、末四位），但拿不回原值。
 */
public final class Redaction {

    private static final String MASK = "***";

    private Redaction() {
    }

    /** 邮箱保留首字母与域名，手机号保留末四位；认不出形状时整体打码。 */
    public static String address(String address) {
        if (address == null || address.isBlank()) {
            return MASK;
        }
        int at = address.indexOf('@');
        if (at > 0) {
            return address.charAt(0) + MASK + address.substring(at);
        }
        if (address.length() <= 4) {
            return MASK;
        }
        return MASK + address.substring(address.length() - 4);
    }

    /** 异常消息可能夹带地址（例如渠道商原样回显）：只留类型与错误码，不转述内容。 */
    public static String error(Throwable failure) {
        return failure.getClass().getSimpleName();
    }
}
