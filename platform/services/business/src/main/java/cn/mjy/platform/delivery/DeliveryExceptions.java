package cn.mjy.platform.delivery;

import cn.mjy.platform.access.DecisionReason;

/** 本包异常的工厂，集中说明各自对应的 HTTP 语义（映射见 {@link DeliveryErrorHandler}）。 */
final class DeliveryExceptions {

    private DeliveryExceptions() {
    }

    /** 404：不存在，或属于别的租户——两者在行级安全下不可区分，也不应区分。 */
    static DeliveryNotFoundException notFound(String what) {
        return new DeliveryNotFoundException(what);
    }

    static DeliveryAccessDeniedException denied(DecisionReason reason, String message) {
        return new DeliveryAccessDeniedException(reason, message);
    }

    static InvalidDeliveryRequestException invalid(String message) {
        return new InvalidDeliveryRequestException(message);
    }

    static DeliveryConflictException conflict(String code, String message) {
        return new DeliveryConflictException(code, message);
    }
}
