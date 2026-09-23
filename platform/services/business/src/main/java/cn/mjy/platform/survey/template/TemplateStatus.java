package cn.mjy.platform.survey.template;

import com.fasterxml.jackson.annotation.JsonValue;

/**
 * 模板的审核状态。取值与 V540 的 CHECK 约束一致。
 *
 * <pre>
 *   draft ──> pending ──> published ──> pending（加了新版本后重新送审）
 *               │            └──> unpublished（下架）
 *               └──> rejected ──> pending
 *   unpublished ──> pending
 * </pre>
 *
 * <p>只有 {@link #PUBLISHED} 的模板出现在租户的模板库里、可以被复制。送审期间模板暂时退出模板库
 * （published_version 一并清空）：模板不像上线问卷，没有"正在作答的人"，
 * 用一个状态同时表达可见性比维护两套版本指针简单得多，也不可能出现"可见但没有版本"的中间态。
 */
public enum TemplateStatus {
    DRAFT,
    PENDING,
    PUBLISHED,
    REJECTED,
    UNPUBLISHED;

    @JsonValue
    public String code() {
        return name().toLowerCase();
    }

    public static TemplateStatus fromCode(String code) {
        return valueOf(code.toUpperCase());
    }
}
