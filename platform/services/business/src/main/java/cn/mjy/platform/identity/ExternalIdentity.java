package cn.mjy.platform.identity;

import java.util.Objects;

/**
 * 外部身份三元组：提供方（wecom、wechat_mp、dingtalk、feishu 等）、企业/应用标识、该应用下的外部用户标识。
 *
 * 三者合起来才唯一：微信 openid 只在一个公众号/小程序内唯一，同一个人在两个应用下 openid 不同，
 * 不同的人在两个应用下也可能碰巧拿到相同的字符串。所以只按 externalId 合并主体是错误的（openid 作用域陷阱）。
 */
public record ExternalIdentity(String provider, String appId, String externalId) {

    public static final String PROVIDER_REGEX = "[a-z][a-z0-9_]{1,31}";
    public static final int MAX_APP_ID = 128;
    public static final int MAX_EXTERNAL_ID = 256;

    public ExternalIdentity {
        Objects.requireNonNull(provider, "provider");
        Objects.requireNonNull(appId, "appId");
        Objects.requireNonNull(externalId, "externalId");
    }
}
