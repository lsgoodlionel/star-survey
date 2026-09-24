package cn.mjy.platform.asset;

import cn.mjy.platform.audit.AuditLogRepository;
import cn.mjy.platform.shared.TenantContext;
import java.util.UUID;
import org.springframework.stereotype.Component;

/**
 * 资产的审计写入。资源串里只放 UUID、版本号与动作码，<b>绝不</b>放原始文件名、存储键或取件票
 * ——文件名可能带个人信息，取件票本身就是凭据。须与业务写入在同一个租户事务里调用。
 */
@Component
class AssetAudit {

    static final String CREATE = "asset.create";
    static final String VERSION = "asset.version";
    static final String ARCHIVE = "asset.archive";
    static final String DELETE = "asset.delete";
    static final String REFERENCE = "asset.reference";

    private final AuditLogRepository audit;

    AssetAudit(AuditLogRepository audit) {
        this.audit = audit;
    }

    void record(TenantContext ctx, String action, UUID assetId, String detail) {
        audit.record(ctx.tenantId(), ctx.actorId(), action, "asset/" + assetId + " " + detail, ctx.traceId());
    }
}
