package cn.mjy.platform.survey;

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
 * 问卷接口的错误映射，只作用于本包的控制器。错误体带机器可读的 error 码；
 * 403 的 error 码就是 access 模块的判定原因（如 {@code approval_required}），调用方据此提示"去走发布审核"。
 * 刻意不处理 Spring Security 的 AccessDeniedException（令牌缺租户等），交给安全链统一返回 403。
 */
@RestControllerAdvice(basePackages = "cn.mjy.platform.survey")
public class SurveyErrorHandler {

    /** 错误体；problems 只在定义校验失败时非空。 */
    public record SurveyError(String error, String message, List<String> problems) {
    }

    @ExceptionHandler(SurveyNotFoundException.class)
    ResponseEntity<SurveyError> notFound(SurveyNotFoundException e) {
        return respond(HttpStatus.NOT_FOUND, "not_found", e.getMessage());
    }

    @ExceptionHandler(SurveyAccessDeniedException.class)
    ResponseEntity<SurveyError> denied(SurveyAccessDeniedException e) {
        return respond(HttpStatus.FORBIDDEN, e.reason().name().toLowerCase(Locale.ROOT), e.getMessage());
    }

    @ExceptionHandler(SurveyConflictException.class)
    ResponseEntity<SurveyError> conflict(SurveyConflictException e) {
        return respond(HttpStatus.CONFLICT, e.code(), e.getMessage());
    }

    @ExceptionHandler(InvalidDefinitionException.class)
    ResponseEntity<SurveyError> invalidDefinition(InvalidDefinitionException e) {
        return ResponseEntity.status(HttpStatus.UNPROCESSABLE_CONTENT)
                .body(new SurveyError("invalid_definition", "survey definition is invalid", e.problems()));
    }

    @ExceptionHandler(PublishUnavailableException.class)
    ResponseEntity<SurveyError> unavailable(PublishUnavailableException e) {
        return respond(HttpStatus.SERVICE_UNAVAILABLE, "publish_unavailable", e.getMessage());
    }

    @ExceptionHandler(InvalidSurveyRequestException.class)
    ResponseEntity<SurveyError> invalidRequest(InvalidSurveyRequestException e) {
        return respond(HttpStatus.BAD_REQUEST, "invalid_request", e.getMessage());
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    ResponseEntity<SurveyError> invalidBody(MethodArgumentNotValidException e) {
        String message = e.getBindingResult().getFieldErrors().stream()
                .map(f -> f.getField() + " " + f.getDefaultMessage())
                .sorted()
                .collect(Collectors.joining("; "));
        return respond(HttpStatus.BAD_REQUEST, "invalid_request", message);
    }

    @ExceptionHandler({HttpMessageNotReadableException.class, MethodArgumentTypeMismatchException.class})
    ResponseEntity<SurveyError> unreadable(Exception e) {
        return respond(HttpStatus.BAD_REQUEST, "invalid_request", "malformed request");
    }

    private static ResponseEntity<SurveyError> respond(HttpStatus status, String code, String message) {
        return ResponseEntity.status(status).body(new SurveyError(code, message, List.of()));
    }
}
