package cn.mjy.platform.dictionary;

import com.fasterxml.jackson.annotation.JsonValue;
import java.util.Locale;

/**
 * 字典版本的两个状态。没有「下架」：已发布的版本永远可读，因为可能还有已发布问卷引用着它
 * （ADR 0019 决定 2）。要让一本字典停止被新问卷引用，办法是不再把它设成当前版本。
 */
public enum DictionaryVersionStatus {

    /** 草稿：节点可以反复重灌，对租户不可见。 */
    DRAFT,
    /** 已发布：节点与摘要都不再改变。 */
    PUBLISHED;

    @JsonValue
    public String code() {
        return name().toLowerCase(Locale.ROOT);
    }

    static DictionaryVersionStatus fromDb(String value) {
        return valueOf(value.toUpperCase(Locale.ROOT));
    }
}
