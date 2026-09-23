package cn.mjy.platform.contacts;

import java.time.OffsetDateTime;
import java.util.UUID;

/** 两个联系人之间的显式关联（不是合并）。 */
public record ContactLinkView(UUID id, UUID leftContactId, UUID rightContactId, String reason,
        OffsetDateTime linkedAt, OffsetDateTime unlinkedAt) {
}
