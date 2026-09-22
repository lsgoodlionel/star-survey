package cn.mjy.platform.survey;

import cn.mjy.platform.access.DecisionReason;
import java.util.Objects;

/**
 * 权限判定不通过（403），携带 access 模块给出的原因。尤其是 {@link DecisionReason#APPROVAL_REQUIRED}：
 * 必须原样告诉调用方"需要走发布审核"，不能被当成一般的无权限，更不能被绕过。
 */
public class SurveyAccessDeniedException extends RuntimeException {

    private final DecisionReason reason;

    public SurveyAccessDeniedException(DecisionReason reason, String message) {
        super(message);
        this.reason = Objects.requireNonNull(reason, "reason");
    }

    public DecisionReason reason() {
        return reason;
    }
}
