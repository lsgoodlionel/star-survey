package cn.mjy.platform.contacts;

import java.util.UUID;

/**
 * 联系人检索条件。全部可选；一律在调用者的数据范围内求交集，范围之外的行等同不存在。
 * cursor 是上一页最后一个联系人的 id（按 id 升序翻页）。
 */
public record ContactQuery(UUID listId, UUID orgUnitId, boolean includeDescendants, String tag,
        String cursor, Integer limit) {

    public static ContactQuery all() {
        return new ContactQuery(null, null, true, null, null, null);
    }

    public ContactQuery inList(UUID value) {
        return new ContactQuery(value, orgUnitId, includeDescendants, tag, cursor, limit);
    }

    public ContactQuery inUnit(UUID value, boolean descendants) {
        return new ContactQuery(listId, value, descendants, tag, cursor, limit);
    }

    public ContactQuery withTag(String value) {
        return new ContactQuery(listId, orgUnitId, includeDescendants, value, cursor, limit);
    }

    public ContactQuery withCursor(String value) {
        return new ContactQuery(listId, orgUnitId, includeDescendants, tag, value, limit);
    }

    public ContactQuery withLimit(Integer value) {
        return new ContactQuery(listId, orgUnitId, includeDescendants, tag, cursor, value);
    }
}
