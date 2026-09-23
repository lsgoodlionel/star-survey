package cn.mjy.platform.survey.template;

import java.time.Instant;
import java.util.UUID;

/** 模板动作历史的一行：谁在什么时候做了什么。只追加，不可改。 */
public record TemplateEventView(
        String event,
        String actor,
        Integer versionNo,
        String reason,
        UUID copiedSurveyId,
        Instant occurredAt) {
}
