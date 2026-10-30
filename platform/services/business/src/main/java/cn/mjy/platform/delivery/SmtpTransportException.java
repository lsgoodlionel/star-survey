package cn.mjy.platform.delivery;

/** 已脱敏的 SMTP 失败分类；绝不保存服务端原始报文，因为它可能回显邮箱地址。 */
final class SmtpTransportException extends RuntimeException {

    private final Kind kind;

    SmtpTransportException(Kind kind) {
        super(kind.code);
        this.kind = kind;
    }

    Kind kind() {
        return kind;
    }

    enum Kind {
        TEMPORARY("smtp_temporary_failure"),
        RECIPIENT_REJECTED("smtp_recipient_rejected"),
        AUTHENTICATION("smtp_authentication_failed"),
        CONFIGURATION("smtp_configuration_failed"),
        UNKNOWN("smtp_unknown_failure");

        private final String code;

        Kind(String code) {
            this.code = code;
        }

        String code() {
            return code;
        }
    }
}
