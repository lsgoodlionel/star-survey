package cn.mjy.platform.asset;

import cn.mjy.platform.access.AccessDecisionService;
import cn.mjy.platform.access.Decision;
import cn.mjy.platform.access.Permission;
import cn.mjy.platform.shared.TenantContext;
import java.util.Objects;
import org.springframework.stereotype.Component;

/**
 * 资产与 access 模块的唯一接点。资产库是<b>租户级</b>的，不是资源树节点：
 * 看素材要租户级 {@code view}，上传/停用/删除要租户级 {@code edit}。
 *
 * <p>"某个项目的素材只给某些人用"要等资源树支持挂资产（ADR 0019 已知限制 7）。
 */
@Component
class AssetAccess {

    private final AccessDecisionService access;

    AssetAccess(AccessDecisionService access) {
        this.access = access;
    }

    void requireRead(TenantContext ctx) {
        require(ctx, Permission.VIEW);
    }

    void requireWrite(TenantContext ctx) {
        require(ctx, Permission.EDIT);
    }

    private void require(TenantContext ctx, Permission permission) {
        Objects.requireNonNull(ctx, "ctx");
        Decision decision = access.canInTenant(ctx, permission);
        if (!decision.allowed()) {
            throw new AssetAccessDeniedException(decision.reason(),
                    permission.code() + " denied: " + decision.detail());
        }
    }
}
