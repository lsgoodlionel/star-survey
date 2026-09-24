package cn.mjy.platform.dictionary;

import cn.mjy.platform.audit.AuditLogRepository;
import cn.mjy.platform.shared.TenantId;
import java.util.UUID;
import org.springframework.stereotype.Component;

/**
 * 字典的审计。字典是运营资产，动作一律记在控制平面租户名下（与运营模板同一口径）。
 * resource 里只有字典代码、版本名与摘要——它们本来就是对所有租户公开的标识，不含任何人的数据。
 */
@Component
class DictionaryAudit {

    static final String CREATE = "platform.dictionary.create";
    static final String DRAFT = "platform.dictionary.draft";
    static final String LOAD = "platform.dictionary.load";
    static final String PUBLISH = "platform.dictionary.publish";

    /** 运营操作落账用的控制平面租户：不是任何真实租户，真实租户的审计里看不到它。 */
    static final TenantId CONTROL_PLANE = new TenantId(new UUID(0, 0));

    private final AuditLogRepository auditLog;

    DictionaryAudit(AuditLogRepository auditLog) {
        this.auditLog = auditLog;
    }

    /** 调用方必须已经在 {@link #CONTROL_PLANE} 的 TenantScope 里：audit_log 有行级安全。 */
    void record(String operator, String traceId, String action, String dictionaryCode, String details) {
        auditLog.record(CONTROL_PLANE, operator, action,
                "platform-dictionary/" + dictionaryCode + (details.isEmpty() ? "" : " " + details), traceId);
    }
}
