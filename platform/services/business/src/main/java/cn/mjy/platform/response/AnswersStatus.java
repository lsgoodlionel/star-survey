package cn.mjy.platform.response;

import com.fasterxml.jackson.annotation.JsonValue;
import java.util.Locale;

/** 一行答卷的作答值是否附上、为什么没附上。 */
public enum AnswersStatus {
    /** 已从引擎取到作答（敏感列可能已遮蔽）。 */
    AVAILABLE,
    /** 答卷已删除（墓碑），不再有作答。 */
    DELETED,
    /** 答卷属于已被替换的答卷表代次，引擎当前表里的同号答卷不是它（ADR 0013 决定 3）。 */
    ARCHIVED,
    /** 平台有投影，但引擎当前表里没有这份答卷（例如被直接删除、或事件先于数据可见）。 */
    MISSING;

    @JsonValue
    public String wire() {
        return name().toLowerCase(Locale.ROOT);
    }
}
