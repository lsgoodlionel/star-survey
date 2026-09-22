package cn.mjy.platform.identity.org;

import org.springframework.http.HttpStatus;

/**
 * 事件回调被拒。401 一律在任何写库之前抛出：验签失败、时间戳过期、无法解密、接收方或令牌不符。
 * 消息不含密钥、明文、密文。
 */
class OrgEventException extends RuntimeException {

    static final String INVALID_SIGNATURE = "invalid_event_signature";
    static final String MALFORMED = "malformed_event";
    static final String TOO_LARGE = "event_too_large";
    static final String NOT_CONFIGURED = "not_configured";

    private final HttpStatus status;
    private final String error;

    OrgEventException(HttpStatus status, String error, String message) {
        super(message);
        this.status = status;
        this.error = error;
    }

    static OrgEventException unauthenticated(String message) {
        return new OrgEventException(HttpStatus.UNAUTHORIZED, INVALID_SIGNATURE, message);
    }

    static OrgEventException malformed(String message) {
        return new OrgEventException(HttpStatus.BAD_REQUEST, MALFORMED, message);
    }

    static OrgEventException tooLarge() {
        return new OrgEventException(HttpStatus.PAYLOAD_TOO_LARGE, TOO_LARGE, "event body is too large");
    }

    static OrgEventException misconfigured() {
        return new OrgEventException(HttpStatus.SERVICE_UNAVAILABLE, NOT_CONFIGURED,
                "the event subscription secrets are not configured correctly");
    }

    HttpStatus status() {
        return status;
    }

    String error() {
        return error;
    }
}
