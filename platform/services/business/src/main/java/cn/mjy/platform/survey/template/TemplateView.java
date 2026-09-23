package cn.mjy.platform.survey.template;

import java.time.Instant;
import java.util.UUID;

/**
 * 模板的对外视图。{@code publishedVersion} 非空即代表"在模板库里、可以复制"，
 * 与 {@code status == PUBLISHED} 同义（V540 用 CHECK 约束保证两者一致）。
 */
public record TemplateView(
        UUID id,
        TemplateScope scope,
        String name,
        String description,
        TemplateStatus status,
        int currentVersion,
        Integer publishedVersion,
        String createdBy,
        String reviewedBy,
        Instant reviewedAt,
        String reviewReason,
        Instant updatedAt) {
}
