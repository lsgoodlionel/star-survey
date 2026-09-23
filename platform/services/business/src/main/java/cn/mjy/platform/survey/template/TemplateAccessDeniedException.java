package cn.mjy.platform.survey.template;

import cn.mjy.platform.access.DecisionReason;

/** 调用方是本租户成员，但没有这个操作要求的权限（403）。原因原样来自 access 模块的判定。 */
public class TemplateAccessDeniedException extends RuntimeException {

    private final DecisionReason reason;

    public TemplateAccessDeniedException(DecisionReason reason, String message) {
        super(message);
        this.reason = reason;
    }

    public DecisionReason reason() {
        return reason;
    }
}
