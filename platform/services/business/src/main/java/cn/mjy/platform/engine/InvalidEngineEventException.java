package cn.mjy.platform.engine;

/** 信封能解析但内容平台不接受；整批拒收，引擎侧保留待重投。 */
public class InvalidEngineEventException extends RuntimeException {

    /** 拒收原因，同时作为响应里的错误码。 */
    public enum Reason {
        UNSUPPORTED_SCHEMA_VERSION("unsupported_schema_version"),
        UNSUPPORTED_EVENT_TYPE("unsupported_event_type"),
        INVALID_OCCURRED_AT("invalid_occurred_at");

        private final String code;

        Reason(String code) {
            this.code = code;
        }

        public String code() {
            return code;
        }
    }

    private final Reason reason;

    public InvalidEngineEventException(Reason reason, String detail) {
        super(reason.code() + ": " + detail);
        this.reason = reason;
    }

    public Reason reason() {
        return reason;
    }
}
