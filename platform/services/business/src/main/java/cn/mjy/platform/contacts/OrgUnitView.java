package cn.mjy.platform.contacts;

import java.util.UUID;

/** 部门节点。parentId 为空表示根部门。 */
public record OrgUnitView(UUID id, UUID parentId, String name, String externalRef) {
}
