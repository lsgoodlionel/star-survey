package cn.mjy.platform.survey.template;

/** 当前模板状态与服务端授权共同决定的作者端可用动作。 */
public record TemplateCapabilities(boolean canAddVersion, boolean canSubmit,
        boolean canReview, boolean canUnpublish) {
}
