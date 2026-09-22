package cn.mjy.platform.access;

import java.time.Instant;
import java.util.UUID;

/** 资源树上一个节点的只读视图。kind 为 project / folder / survey；项目的 parentId 为空。 */
public record ResourceView(UUID id, String kind, UUID parentId, String name, Instant createdAt) {

    ResourceKind resourceKind() {
        return ResourceKind.fromCode(kind);
    }
}
