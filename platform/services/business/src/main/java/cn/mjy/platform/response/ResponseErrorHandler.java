package cn.mjy.platform.response;

import java.util.Locale;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

/**
 * 答卷查询与导出接口的错误映射，只作用于本包的控制器。错误体带机器可读的 error 码；
 * 403 的 error 码就是 access 模块的判定原因。网关故障的细节只进日志，不回给调用方。
 */
@RestControllerAdvice(basePackages = "cn.mjy.platform.response")
public class ResponseErrorHandler {

    private static final Logger log = LoggerFactory.getLogger(ResponseErrorHandler.class);

    public record ResponseError(String error, String message) {
    }

    @ExceptionHandler(ResponseNotFoundException.class)
    ResponseEntity<ResponseError> notFound(ResponseNotFoundException e) {
        return respond(HttpStatus.NOT_FOUND, "not_found", e.getMessage());
    }

    @ExceptionHandler(ResponseAccessDeniedException.class)
    ResponseEntity<ResponseError> denied(ResponseAccessDeniedException e) {
        return respond(HttpStatus.FORBIDDEN, e.reason().name().toLowerCase(Locale.ROOT), e.getMessage());
    }

    @ExceptionHandler(InvalidResponseQueryException.class)
    ResponseEntity<ResponseError> invalid(InvalidResponseQueryException e) {
        return respond(HttpStatus.BAD_REQUEST, "invalid_request", e.getMessage());
    }

    @ExceptionHandler(ExportNotReadyException.class)
    ResponseEntity<ResponseError> notReady(ExportNotReadyException e) {
        return respond(HttpStatus.CONFLICT, "export_not_ready", e.getMessage());
    }

    @ExceptionHandler(ExportConflictException.class)
    ResponseEntity<ResponseError> conflict(ExportConflictException e) {
        return respond(HttpStatus.CONFLICT, e.code(), e.getMessage());
    }

    @ExceptionHandler(ExportGoneException.class)
    ResponseEntity<ResponseError> gone(ExportGoneException e) {
        return respond(HttpStatus.GONE, "export_expired", e.getMessage());
    }

    @ExceptionHandler({MethodArgumentTypeMismatchException.class, MissingServletRequestParameterException.class,
            HttpMessageNotReadableException.class})
    ResponseEntity<ResponseError> malformed(Exception e) {
        return respond(HttpStatus.BAD_REQUEST, "invalid_request", "malformed request");
    }

    @ExceptionHandler(ResponseAnswersUnavailableException.class)
    ResponseEntity<ResponseError> unavailable(ResponseAnswersUnavailableException e) {
        log.warn("response answers unavailable: {}", e.getMessage());
        return respond(HttpStatus.SERVICE_UNAVAILABLE, "answers_unavailable", "answers are temporarily unavailable");
    }

    private static ResponseEntity<ResponseError> respond(HttpStatus status, String code, String message) {
        return ResponseEntity.status(status).body(new ResponseError(code, message));
    }
}
