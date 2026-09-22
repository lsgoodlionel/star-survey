package cn.mjy.platform.access;

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
 * 资源树接口的错误映射，只作用于 access 包的控制器。403 的 error 码就是判定原因（如 {@code no_matching_grant}）。
 * 刻意不处理 Spring Security 的 AccessDeniedException（令牌缺租户等），交给安全链统一返回 403。
 */
@RestControllerAdvice(basePackages = "cn.mjy.platform.access")
public class ResourceTreeErrorHandler {

    /** 错误体：机器可读的 error 码 + 给人看的 message。 */
    public record ResourceError(String error, String message) {
    }

    @ExceptionHandler(ResourceNotFoundException.class)
    ResponseEntity<ResourceError> notFound(ResourceNotFoundException e) {
        return respond(HttpStatus.NOT_FOUND, "not_found", e.getMessage());
    }

    @ExceptionHandler(ResourceAccessDeniedException.class)
    ResponseEntity<ResourceError> denied(ResourceAccessDeniedException e) {
        return respond(HttpStatus.FORBIDDEN, e.reason().name().toLowerCase(Locale.ROOT), e.getMessage());
    }

    @ExceptionHandler(ResourceConflictException.class)
    ResponseEntity<ResourceError> conflict(ResourceConflictException e) {
        return respond(HttpStatus.CONFLICT, e.code(), e.getMessage());
    }

    @ExceptionHandler(InvalidResourceRequestException.class)
    ResponseEntity<ResourceError> invalid(InvalidResourceRequestException e) {
        return respond(HttpStatus.BAD_REQUEST, "invalid_request", e.getMessage());
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    ResponseEntity<ResourceError> invalidBody(MethodArgumentNotValidException e) {
        String message = e.getBindingResult().getFieldErrors().stream()
                .map(f -> f.getField() + " " + f.getDefaultMessage())
                .sorted()
                .collect(Collectors.joining("; "));
        return respond(HttpStatus.BAD_REQUEST, "invalid_request", message);
    }

    @ExceptionHandler({HttpMessageNotReadableException.class, MethodArgumentTypeMismatchException.class})
    ResponseEntity<ResourceError> unreadable(Exception e) {
        return respond(HttpStatus.BAD_REQUEST, "invalid_request", "malformed request");
    }

    private static ResponseEntity<ResourceError> respond(HttpStatus status, String code, String message) {
        return ResponseEntity.status(status).body(new ResourceError(code, message));
    }
}
