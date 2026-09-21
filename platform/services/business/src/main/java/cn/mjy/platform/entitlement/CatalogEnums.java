package cn.mjy.platform.entitlement;

import java.util.Locale;

/** 数据库里以小写文本存放的枚举值与 Java 枚举之间的转换。 */
final class CatalogEnums {

    private CatalogEnums() {
    }

    static <E extends Enum<E>> E fromDatabase(Class<E> type, String value) {
        return Enum.valueOf(type, value.toUpperCase(Locale.ROOT));
    }

    static String toDatabase(Enum<?> value) {
        return value.name().toLowerCase(Locale.ROOT);
    }
}
