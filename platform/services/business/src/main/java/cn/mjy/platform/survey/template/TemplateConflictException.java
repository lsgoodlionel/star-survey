package cn.mjy.platform.survey.template;

/** 状态不允许这个操作（409）。{@code code} 是稳定的机器可读标识，直接作为应答里的 error。 */
public class TemplateConflictException extends RuntimeException {

    private final String code;

    public TemplateConflictException(String code, String message) {
        // 消息里带上代码：日志与应答都能一眼看出是哪种冲突，不必再去对照 code 字段。
        super(code + ": " + message);
        this.code = code;
    }

    public String code() {
        return code;
    }
}
