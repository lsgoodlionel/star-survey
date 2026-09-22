package cn.mjy.platform.identity.org;

/**
 * 开放平台调用失败。消息只含提供方、接口路径与错误码，绝不含授权码、令牌或密钥。
 */
class OrgProviderException extends RuntimeException {

    enum Kind {
        /** 授权码无效、已用过、已过期或不属于本应用。 */
        CODE_REJECTED,
        /** 应用凭据（corpsecret / AppSecret）被拒：配置错误。 */
        CREDENTIALS_REJECTED,
        /** 返回的组织与连接配置的组织不符。 */
        WRONG_ORGANISATION,
        /** 授权人不是该组织的成员（如企业微信非成员 openid、钉钉外部联系人）。 */
        NOT_A_MEMBER,
        /** 返回的身份缺少组织级用户标识（通常是应用缺少相应权限）。 */
        INCOMPLETE_IDENTITY,
        /** 网络错误、超时、5xx 或无法解析的应答。 */
        UNAVAILABLE
    }

    private final Kind kind;

    OrgProviderException(Kind kind, String message) {
        super(message);
        this.kind = kind;
    }

    Kind kind() {
        return kind;
    }
}
