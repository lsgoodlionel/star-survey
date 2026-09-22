package cn.mjy.platform.survey;

import java.util.Objects;

/**
 * 审批流程自身的规则拒绝（403），与权限判定无关：只有申请人本人能撤回（code {@code not_applicant}）。
 * 权限判定的拒绝仍用 {@link SurveyAccessDeniedException}，原因来自 access 模块。
 */
public class PublishApprovalForbiddenException extends RuntimeException {

    static final String NOT_APPLICANT = "not_applicant";

    private final String code;

    public PublishApprovalForbiddenException(String code, String message) {
        super(message);
        this.code = Objects.requireNonNull(code, "code");
    }

    public String code() {
        return code;
    }
}
