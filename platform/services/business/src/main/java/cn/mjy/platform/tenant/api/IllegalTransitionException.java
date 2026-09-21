package cn.mjy.platform.tenant.api;

/** 状态机不允许的迁移。消息里写明起止状态，便于运营排查。 */
public class IllegalTransitionException extends ConflictException {

    public IllegalTransitionException(String subject, String from, String to) {
        super("illegal " + subject + " transition: " + from + " -> " + to);
    }
}
