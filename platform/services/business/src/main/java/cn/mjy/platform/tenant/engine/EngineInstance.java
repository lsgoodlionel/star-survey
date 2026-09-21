package cn.mjy.platform.tenant.engine;

import cn.mjy.platform.shared.TenantId;
import java.time.OffsetDateTime;
import java.util.regex.Pattern;

/** 一套引擎（LimeSurvey）实例的登记。一个租户可以拥有多套；一个实例只属于一个租户。 */
public record EngineInstance(
        String id, TenantId tenantId, String baseUrl, EngineInstanceStatus status, OffsetDateTime createdAt) {

    /** 实例标识格式；与数据库 CHECK 约束一致。 */
    public static final String ID_REGEX = "[a-z0-9][a-z0-9-]{1,62}";

    /** 引擎基础地址：只接受 http/https 的绝对地址，不含空白与用户信息。 */
    public static final String BASE_URL_REGEX = "https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[^\\s@]*)?";

    private static final Pattern ID_PATTERN = Pattern.compile(ID_REGEX);

    public static boolean isWellFormedId(String id) {
        return id != null && ID_PATTERN.matcher(id).matches();
    }

    public EngineInstance withStatus(EngineInstanceStatus next) {
        return new EngineInstance(id, tenantId, baseUrl, next, createdAt);
    }
}
