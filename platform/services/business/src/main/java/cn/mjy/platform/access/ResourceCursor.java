package cn.mjy.platform.access;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.time.DateTimeException;
import java.time.Instant;
import java.util.Base64;
import java.util.Objects;
import java.util.UUID;

/**
 * 资源列表的 r2 分页游标。绑定全部筛选条件并携带完整排序位置；对调用方不透明。
 */
final class ResourceCursor {

    private static final byte[] PREFIX = "r2".getBytes(StandardCharsets.US_ASCII);
    private static final int MAX_CURSOR_BYTES = 1_048_576;

    private ResourceCursor() {
    }

    static String encode(UUID parentId, ResourceListQuery query, ResourceView last) {
        try {
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            try (DataOutputStream out = new DataOutputStream(bytes)) {
                out.write(PREFIX);
                writeNullableUuid(out, parentId);
                writeNullableString(out, query.query());
                writeNullableString(out, query.kind() == null ? null : query.kind().code());
                writeString(out, query.archived().code());
                writeString(out, query.sort().code());
                out.writeInt(kindRank(last.resourceKind()));
                out.writeLong(last.updatedAt().getEpochSecond());
                out.writeInt(last.updatedAt().getNano());
                writeString(out, last.name());
                writeUuid(out, last.id());
            }
            return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes.toByteArray());
        } catch (IOException e) {
            throw new IllegalStateException("cannot encode resource cursor", e);
        }
    }

    /** 空游标表示第一页，返回 null。 */
    static Position decode(String cursor, UUID parentId, ResourceListQuery query) {
        if (cursor == null || cursor.isBlank()) {
            return null;
        }
        try {
            byte[] raw = Base64.getUrlDecoder().decode(cursor);
            if (raw.length > MAX_CURSOR_BYTES) {
                throw new IllegalArgumentException("cursor is too long");
            }
            try (DataInputStream in = new DataInputStream(new ByteArrayInputStream(raw))) {
                if (in.readByte() != PREFIX[0] || in.readByte() != PREFIX[1]) {
                    throw new IllegalArgumentException("unknown cursor version");
                }
                UUID encodedParent = readNullableUuid(in);
                String encodedQuery = readNullableString(in);
                String encodedKind = readNullableString(in);
                ResourceListQuery.ArchiveFilter encodedArchived =
                        ResourceListQuery.ArchiveFilter.fromCode(readString(in));
                ResourceListQuery.Sort encodedSort = ResourceListQuery.Sort.fromCode(readString(in));
                int rank = in.readInt();
                Instant updatedAt = Instant.ofEpochSecond(in.readLong(), in.readInt());
                String name = readString(in);
                UUID id = readUuid(in);
                if (in.available() != 0 || rank < 0 || rank > 2
                        || !Objects.equals(encodedParent, parentId)
                        || !Objects.equals(encodedQuery, query.query())
                        || !Objects.equals(encodedKind, query.kind() == null ? null : query.kind().code())
                        || encodedArchived != query.archived() || encodedSort != query.sort()) {
                    throw new IllegalArgumentException("cursor does not match this query");
                }
                return new Position(rank, updatedAt, name, id);
            }
        } catch (IllegalArgumentException | DateTimeException | IOException e) {
            throw new InvalidResourceRequestException("cursor is invalid");
        }
    }

    record Position(int kindRank, Instant updatedAt, String name, UUID id) {
    }

    static int kindRank(ResourceKind kind) {
        return switch (kind) {
            case PROJECT -> 0;
            case FOLDER -> 1;
            case SURVEY -> 2;
        };
    }

    private static void writeNullableUuid(DataOutputStream out, UUID value) throws IOException {
        out.writeBoolean(value != null);
        if (value != null) {
            writeUuid(out, value);
        }
    }

    private static UUID readNullableUuid(DataInputStream in) throws IOException {
        return in.readBoolean() ? readUuid(in) : null;
    }

    private static void writeUuid(DataOutputStream out, UUID value) throws IOException {
        out.writeLong(value.getMostSignificantBits());
        out.writeLong(value.getLeastSignificantBits());
    }

    private static UUID readUuid(DataInputStream in) throws IOException {
        return new UUID(in.readLong(), in.readLong());
    }

    private static void writeNullableString(DataOutputStream out, String value) throws IOException {
        out.writeBoolean(value != null);
        if (value != null) {
            writeString(out, value);
        }
    }

    private static String readNullableString(DataInputStream in) throws IOException {
        return in.readBoolean() ? readString(in) : null;
    }

    private static void writeString(DataOutputStream out, String value) throws IOException {
        byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
        out.writeInt(bytes.length);
        out.write(bytes);
    }

    private static String readString(DataInputStream in) throws IOException {
        int length = in.readInt();
        if (length < 0 || length > MAX_CURSOR_BYTES || length > in.available()) {
            throw new IllegalArgumentException("invalid cursor string length");
        }
        return new String(in.readNBytes(length), StandardCharsets.UTF_8);
    }
}
