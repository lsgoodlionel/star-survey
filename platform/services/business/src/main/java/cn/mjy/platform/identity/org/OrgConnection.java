package cn.mjy.platform.identity.org;

import cn.mjy.platform.identity.ExternalIdentity;
import cn.mjy.platform.shared.TenantId;
import java.util.UUID;

/**
 * 租户的一个组织连接。corpId 是组织的作用域（企业微信 corpid / 钉钉 corpId / 飞书 tenant_key），
 * appId 是该组织里的应用（agentid / AppKey / App ID）。secretRef 只是密钥名，不含密钥。
 * eventTokenRef / eventKeyRef 是通讯录事件订阅的两个密钥名（Token 与加密密钥），都为空表示不接收事件。
 */
public record OrgConnection(UUID id, TenantId tenantId, OrgProvider provider, String corpId, String appId,
        String secretRef, UnknownUserPolicy unknownUserPolicy, boolean enabled, String eventTokenRef,
        String eventKeyRef) {

    public OrgConnection {
        if ((eventTokenRef == null) != (eventKeyRef == null)) {
            throw new IllegalArgumentException("event token and key references are configured together");
        }
    }

    /** 是否配置了通讯录事件订阅。 */
    public boolean receivesEvents() {
        return eventTokenRef != null;
    }

    /**
     * 组织成员的外部身份：用组织级用户标识（企业微信 userid / 钉钉 userid / 飞书 user_id）、以组织为作用域。
     * 同一组织下不同应用拿到的是同一个人；不同组织下相同的字符串是不同的人（不合并）。
     */
    public ExternalIdentity identityOf(String userId) {
        return new ExternalIdentity(provider.code(), corpId, userId);
    }
}
