package cn.mjy.platform.tenant.api;

import java.util.stream.Collectors;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.HandlerMethodValidationException;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

/**
 * 租户与身份车道的错误映射，只作用于这两个包的控制器。
 * 刻意不处理 AccessDeniedException：交给 Spring Security 统一返回 403。
 */
@RestControllerAdvice(basePackages = {"cn.mjy.platform.tenant", "cn.mjy.platform.identity"})
public class ApiErrorHandler {

    private static final Logger LOG = LoggerFactory.getLogger(ApiErrorHandler.class);

    @ExceptionHandler(NotFoundException.class)
    ResponseEntity<ApiError> notFound(NotFoundException e) {
        return respond(HttpStatus.NOT_FOUND, "not_found", e.getMessage());
    }

    @ExceptionHandler(IllegalTransitionException.class)
    ResponseEntity<ApiError> illegalTransition(IllegalTransitionException e) {
        return respond(HttpStatus.CONFLICT, "illegal_transition", e.getMessage());
    }

    @ExceptionHandler(ConflictException.class)
    ResponseEntity<ApiError> conflict(ConflictException e) {
        return respond(HttpStatus.CONFLICT, "conflict", e.getMessage());
    }

    @ExceptionHandler(DuplicateKeyException.class)
    ResponseEntity<ApiError> duplicate(DuplicateKeyException e) {
        // 不回显数据库消息：其中可能带有别的租户的键值。
        LOG.info("duplicate key rejected: {}", e.getMostSpecificCause().getMessage());
        return respond(HttpStatus.CONFLICT, "conflict", "resource already exists");
    }

    @ExceptionHandler(InvalidRequestException.class)
    ResponseEntity<ApiError> invalid(InvalidRequestException e) {
        return respond(HttpStatus.BAD_REQUEST, "invalid_request", e.getMessage());
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    ResponseEntity<ApiError> invalidBody(MethodArgumentNotValidException e) {
        String message = e.getBindingResult().getFieldErrors().stream()
                .map(f -> f.getField() + " " + f.getDefaultMessage())
                .sorted()
                .collect(Collectors.joining("; "));
        return respond(HttpStatus.BAD_REQUEST, "invalid_request", message);
    }

    @ExceptionHandler({HandlerMethodValidationException.class, MissingServletRequestParameterException.class})
    ResponseEntity<ApiError> invalidParameters(Exception e) {
        return respond(HttpStatus.BAD_REQUEST, "invalid_request", "request parameters are missing or invalid");
    }

    @ExceptionHandler({HttpMessageNotReadableException.class, MethodArgumentTypeMismatchException.class})
    ResponseEntity<ApiError> unreadable(Exception e) {
        return respond(HttpStatus.BAD_REQUEST, "invalid_request", "malformed request");
    }

    private static ResponseEntity<ApiError> respond(HttpStatus status, String code, String message) {
        return ResponseEntity.status(status).body(new ApiError(code, message));
    }
}
