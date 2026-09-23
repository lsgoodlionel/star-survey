package cn.mjy.platform.contacts;

/**
 * 一行 / 一次写入被判定为不合法，带稳定的错误码（如 {@code invalid_email}）。
 * 导入时它变成该行的 {@code invalid} 结果，不中断整个作业；单条写入时翻译成 400。
 * 消息里只放字段名与错误码，<b>不放字段值</b>（个人信息不外泄）。
 */
class ContactRowRejectedException extends RuntimeException {

    private final String reason;

    ContactRowRejectedException(String reason) {
        super(reason);
        this.reason = reason;
    }

    String reason() {
        return reason;
    }
}
