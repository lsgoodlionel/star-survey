package cn.mjy.platform.identity;

import cn.mjy.platform.shared.TenantId;
import java.util.UUID;

/** 外部身份到平台主体的绑定。主体标识由平台生成，一个外部身份三元组只对应一个主体、一个租户。 */
public record IdentityBinding(UUID principalId, TenantId tenantId, ExternalIdentity identity) {
}
