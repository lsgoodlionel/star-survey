package cn.mjy.platform.contacts;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

/** 对外的联系人视图。含个人信息，调用方不得写入日志。 */
public record ContactView(UUID id, ContactKind kind, UUID listId, UUID orgUnitId, String displayName,
        String email, String phone, String employeeId, ExternalContactIdentity identity,
        String source, String status, List<String> tags, OffsetDateTime createdAt, OffsetDateTime updatedAt) {

    public ContactView {
        tags = List.copyOf(tags);
    }

    public boolean isActive() {
        return "active".equals(status);
    }
}
