package cn.mjy.platform.delivery;

import java.util.List;
import java.util.Locale;
import java.util.stream.Collectors;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

/**
 * 投放接口的错误映射，只作用于本包的控制器。错误体带机器可读的 error 码。
 * 错误信息由本包自己构造，从不回显收件地址（见 {@link Redaction}）。
 * 刻意不处理 Spring Security 的 AccessDeniedException，交给安全链统一返回 403。
 */
@RestControllerAdvice(basePackages = "cn.mjy.platform.delivery")
public class DeliveryErrorHandler {

    /** 错误体；problems 只在批量校验失败时非空（例如名单里哪几行地址不合法，按序号而非地址指出）。 */
    public record DeliveryError(String error, String message, List<String> problems) {
    }

    @ExceptionHandler(DeliveryNotFoundException.class)
    ResponseEntity<DeliveryError> notFound(DeliveryNotFoundException e) {
        return respond(HttpStatus.NOT_FOUND, "not_found", e.getMessage());
    }

    @ExceptionHandler(DeliveryAccessDeniedException.class)
    ResponseEntity<DeliveryError> denied(DeliveryAccessDeniedException e) {
        return respond(HttpStatus.FORBIDDEN, e.reason().name().toLowerCase(Locale.ROOT), e.getMessage());
    }

    @ExceptionHandler(DeliveryConflictException.class)
    ResponseEntity<DeliveryError> conflict(DeliveryConflictException e) {
        return respond(HttpStatus.CONFLICT, e.code(), e.getMessage());
    }

    @ExceptionHandler(InvalidDeliveryRequestException.class)
    ResponseEntity<DeliveryError> invalid(InvalidDeliveryRequestException e) {
        return respond(HttpStatus.BAD_REQUEST, "invalid_request", e.getMessage());
    }

    /** 回执验签不过：401，且绝不透露是哪一步不过（避免把验签当预言机用）。 */
    @ExceptionHandler(ReceiptService.ReceiptRejectedException.class)
    ResponseEntity<DeliveryError> receiptRejected(ReceiptService.ReceiptRejectedException e) {
        return respond(HttpStatus.UNAUTHORIZED, "unauthorized", "receipt rejected");
    }

    @ExceptionHandler(DeliveryUnavailableException.class)
    ResponseEntity<DeliveryError> unavailable(DeliveryUnavailableException e) {
        return respond(HttpStatus.SERVICE_UNAVAILABLE, "delivery_unavailable", e.getMessage());
    }

    @ExceptionHandler(IllegalArgumentException.class)
    ResponseEntity<DeliveryError> illegalArgument(IllegalArgumentException e) {
        return respond(HttpStatus.BAD_REQUEST, "invalid_request", e.getMessage());
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    ResponseEntity<DeliveryError> invalidBody(MethodArgumentNotValidException e) {
        String message = e.getBindingResult().getFieldErrors().stream()
                .map(field -> field.getField() + " " + field.getDefaultMessage())
                .sorted()
                .collect(Collectors.joining("; "));
        return respond(HttpStatus.BAD_REQUEST, "invalid_request", message);
    }

    @ExceptionHandler({HttpMessageNotReadableException.class, MethodArgumentTypeMismatchException.class})
    ResponseEntity<DeliveryError> unreadable(Exception e) {
        return respond(HttpStatus.BAD_REQUEST, "invalid_request", "malformed request");
    }

    private static ResponseEntity<DeliveryError> respond(HttpStatus status, String code, String message) {
        return ResponseEntity.status(status).body(new DeliveryError(code, message, List.of()));
    }
}
