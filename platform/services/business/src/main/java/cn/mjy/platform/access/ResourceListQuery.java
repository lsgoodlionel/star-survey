package cn.mjy.platform.access;

import java.text.Normalizer;

/** 资源列表的业务筛选与排序；HTTP 字符串只在这里解析一次。 */
public record ResourceListQuery(String query, ResourceKind kind, ArchiveFilter archived, Sort sort) {

    public ResourceListQuery {
        query = normalizeQuery(query);
        archived = archived == null ? ArchiveFilter.ACTIVE : archived;
        sort = sort == null ? Sort.UPDATED_DESC : sort;
    }

    static ResourceListQuery parse(String query, String kind, String archived, String sort) {
        try {
            return new ResourceListQuery(query,
                    blank(kind) ? null : ResourceKind.fromCode(kind),
                    blank(archived) ? null : ArchiveFilter.fromCode(archived),
                    blank(sort) ? null : Sort.fromCode(sort));
        } catch (IllegalArgumentException e) {
            throw new InvalidResourceRequestException(e.getMessage());
        }
    }

    private static String normalizeQuery(String value) {
        if (blank(value)) {
            return null;
        }
        return Normalizer.normalize(value.strip(), Normalizer.Form.NFC);
    }

    private static boolean blank(String value) {
        return value == null || value.isBlank();
    }

    public enum ArchiveFilter {
        ACTIVE("active"),
        ARCHIVED("archived");

        private final String code;

        ArchiveFilter(String code) {
            this.code = code;
        }

        String code() {
            return code;
        }

        static ArchiveFilter fromCode(String code) {
            for (ArchiveFilter value : values()) {
                if (value.code.equals(code)) {
                    return value;
                }
            }
            throw new IllegalArgumentException("unknown archived filter: " + code);
        }
    }

    public enum Sort {
        UPDATED_DESC("updated_desc"),
        NAME_ASC("name_asc");

        private final String code;

        Sort(String code) {
            this.code = code;
        }

        String code() {
            return code;
        }

        static Sort fromCode(String code) {
            for (Sort value : values()) {
                if (value.code.equals(code)) {
                    return value;
                }
            }
            throw new IllegalArgumentException("unknown sort: " + code);
        }
    }
}
