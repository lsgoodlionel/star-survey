package cn.mjy.platform.survey.template;

import java.time.Instant;
import java.util.UUID;

/**
 * 一个版本快照的元信息（不含定义本身——定义另有接口，按版本单取）。
 * {@code sourceSurveyId} 只作记录：快照与那份问卷之后的改动无关。
 */
public record TemplateVersionView(
        int versionNo,
        UUID sourceSurveyId,
        Integer sourceDraftVersion,
        String createdBy,
        Instant createdAt) {
}
