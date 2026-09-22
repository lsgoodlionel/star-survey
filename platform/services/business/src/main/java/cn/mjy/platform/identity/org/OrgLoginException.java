package cn.mjy.platform.identity.org;

import org.springframework.http.HttpStatus;

/**
 * 免登失败。error 是给客户端的机器可读码；message 不含授权码、令牌、密钥，
 * 且对 state 类失败（重放、过期、换租户、换浏览器）一律同一个码，不给出区分线索。
 */
class OrgLoginException extends RuntimeException {

    static final String INVALID_STATE = "invalid_login_state";
    static final String PROVIDER_REJECTED = "provider_rejected";
    static final String PROVIDER_UNAVAILABLE = "provider_unavailable";
    static final String PROVIDER_MISCONFIGURED = "provider_misconfigured";
    static final String WRONG_ORGANISATION = "wrong_organisation";
    static final String NOT_AUTHORIZED = "not_authorized";
    static final String SEAT_LIMIT = "seat_limit";
    static final String NOT_CONFIGURED = "not_configured";

    private final HttpStatus status;
    private final String error;

    OrgLoginException(HttpStatus status, String error, String message) {
        this(status, error, message, null);
    }

    /** cause 是开放平台错误：只用于服务端日志（其消息同样不含凭据），不回给客户端。 */
    OrgLoginException(HttpStatus status, String error, String message, OrgProviderException cause) {
        super(message, cause);
        this.status = status;
        this.error = error;
    }

    static OrgLoginException invalidState() {
        return new OrgLoginException(HttpStatus.BAD_REQUEST, INVALID_STATE, "login state is invalid or expired");
    }

    static OrgLoginException notAuthorized() {
        return new OrgLoginException(HttpStatus.FORBIDDEN, NOT_AUTHORIZED,
                "this organisation member is not authorised for the tenant");
    }

    static OrgLoginException notConfigured(String what) {
        return new OrgLoginException(HttpStatus.SERVICE_UNAVAILABLE, NOT_CONFIGURED, what + " is not configured");
    }

    static OrgLoginException from(OrgProviderException e) {
        return switch (e.kind()) {
            case CODE_REJECTED -> new OrgLoginException(HttpStatus.UNAUTHORIZED, PROVIDER_REJECTED,
                    "the authorisation code was rejected", e);
            case WRONG_ORGANISATION -> new OrgLoginException(HttpStatus.FORBIDDEN, WRONG_ORGANISATION,
                    "authorised in a different organisation", e);
            case NOT_A_MEMBER -> notAuthorized();
            case CREDENTIALS_REJECTED, INCOMPLETE_IDENTITY -> new OrgLoginException(HttpStatus.BAD_GATEWAY,
                    PROVIDER_MISCONFIGURED, "the organisation connection is misconfigured", e);
            case UNAVAILABLE -> new OrgLoginException(HttpStatus.BAD_GATEWAY, PROVIDER_UNAVAILABLE,
                    "the organisation platform is unavailable", e);
        };
    }

    HttpStatus status() {
        return status;
    }

    String error() {
        return error;
    }
}
