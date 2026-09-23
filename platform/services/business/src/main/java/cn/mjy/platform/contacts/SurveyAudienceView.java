package cn.mjy.platform.contacts;

import java.time.OffsetDateTime;
import java.util.UUID;

/**
 * 一份问卷的受众条件（名单 / 部门 / 标签的交集，至少一项非空）。
 * 这是"选谁"的规则，不是名册；某一版实际邀请到了谁记在 {@link ParticipationView} 里。
 */
public record SurveyAudienceView(UUID surveyId, UUID listId, UUID orgUnitId, boolean includeDescendants,
        String tag, String updatedBy, OffsetDateTime updatedAt) {

    /** 翻成联系人检索条件；数据范围由 {@link ContactDirectoryService} 在 SQL 里另行收窄。 */
    ContactQuery query() {
        return ContactQuery.all().inList(listId).inUnit(orgUnitId, includeDescendants).withTag(tag);
    }
}
