package cn.mjy.platform.access;

import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.UUID;

/**
 * 资源列表的分页游标：上一页最后一个节点的 id，加版本前缀后 base64url 编码。
 * 对调用方不透明；解不开的游标一律 400，而不是悄悄从头开始。
 */
final class ResourceCursor {

    private static final String PREFIX = "r1:";

    private ResourceCursor() {
    }

    static String encode(UUID lastId) {
        byte[] raw = (PREFIX + lastId).getBytes(StandardCharsets.UTF_8);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(raw);
    }

    /** 空游标表示第一页，返回 null。 */
    static UUID decode(String cursor) {
        if (cursor == null || cursor.isBlank()) {
            return null;
        }
        try {
            String raw = new String(Base64.getUrlDecoder().decode(cursor), StandardCharsets.UTF_8);
            if (!raw.startsWith(PREFIX)) {
                throw new IllegalArgumentException("unknown cursor version");
            }
            return UUID.fromString(raw.substring(PREFIX.length()));
        } catch (IllegalArgumentException e) {
            throw new InvalidResourceRequestException("cursor is invalid");
        }
    }
}
