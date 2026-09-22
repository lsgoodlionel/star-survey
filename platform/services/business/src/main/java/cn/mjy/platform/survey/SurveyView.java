package cn.mjy.platform.survey;

import java.util.UUID;

/**
 * 问卷概览：公开 UUID、标题（取自当前草稿）、发布状态、草稿版本、当前已发布版本号与最近一次发布尝试。
 * lastPublish 在从未发起过发布时为空。
 */
public record SurveyView(
        UUID id,
        String title,
        SurveyStatus status,
        int draftVersion,
        Integer publishedVersion,
        PublishAttemptView lastPublish) {
}
