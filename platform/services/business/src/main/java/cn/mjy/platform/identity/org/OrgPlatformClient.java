package cn.mjy.platform.identity.org;

import java.net.URI;

/** 一家开放平台的免登与通讯录查询。实现只在服务端调用开放平台，授权码与令牌不离开进程、不进日志。 */
interface OrgPlatformClient {

    /** 开放平台解析出的组织成员：组织作用域 + 组织级用户标识。 */
    record ProviderUser(String corpId, String userId) {
    }

    /** 通讯录查询结论。只有 DEPARTED 会触发撤权；查询失败或无法判定一律 UNKNOWN。 */
    enum DirectoryStatus { ACTIVE, DEPARTED, UNKNOWN }

    OrgProvider provider();

    /** 浏览器跳转到的授权页地址。qr 仅企业微信有别（扫码登录页）；其余提供方忽略。 */
    URI authorizeUrl(OrgConnection connection, String redirectUri, String state, boolean qr);

    /** 服务端用授权码兑换身份。 */
    ProviderUser exchange(OrgConnection connection, String secret, String code, String redirectUri);

    /** 查询组织成员当前是否在职。不抛异常：失败归为 UNKNOWN。 */
    DirectoryStatus lookup(OrgConnection connection, String secret, String userId);
}
