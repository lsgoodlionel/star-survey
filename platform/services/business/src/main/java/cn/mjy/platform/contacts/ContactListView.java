package cn.mjy.platform.contacts;

import java.time.OffsetDateTime;
import java.util.UUID;

/** 联系人名单：固定身份类别与去重键。 */
public record ContactListView(UUID id, String name, ContactKind kind, DedupeKey dedupeKey,
        OffsetDateTime createdAt) {
}
