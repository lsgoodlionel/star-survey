package cn.mjy.platform.survey.template;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/**
 * 模板库的异常 → HTTP。只管本子包自己的异常，不碰 {@code cn.mjy.platform.survey} 那两个处理器
 * 已经负责的类型（并行车道各改各的文件）。
 *
 * <p>跨租户一律 404：调用方分不出"没有"与"不属于你"。
 */
@RestControllerAdvice(basePackages = "cn.mjy.platform.survey.template")
class TemplateErrorHandler {

    /** 应答体：error 是稳定的机器可读标识，message 只给人看。 */
    public record TemplateError(String error, String message) {
    }

    @ExceptionHandler(TemplateNotFoundException.class)
    ResponseEntity<TemplateError> notFound(TemplateNotFoundException e) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(new TemplateError("not_found", e.getMessage()));
    }

    @ExceptionHandler(TemplateAccessDeniedException.class)
    ResponseEntity<TemplateError> denied(TemplateAccessDeniedException e) {
        return ResponseEntity.status(HttpStatus.FORBIDDEN)
                .body(new TemplateError(e.reason().name().toLowerCase(), e.getMessage()));
    }

    @ExceptionHandler(TemplateConflictException.class)
    ResponseEntity<TemplateError> conflict(TemplateConflictException e) {
        return ResponseEntity.status(HttpStatus.CONFLICT)
                .body(new TemplateError(e.code(), e.getMessage()));
    }

    /**
     * 只处理本子包自己的请求异常。Bean Validation 与报文解析失败由
     * {@code SurveyErrorHandler} 统一处理——它的 basePackages 覆盖本子包，
     * 在这里再接一遍只会让两个处理器争同一个异常。
     */
    @ExceptionHandler(InvalidTemplateRequestException.class)
    ResponseEntity<TemplateError> invalid(InvalidTemplateRequestException e) {
        return ResponseEntity.badRequest().body(new TemplateError("invalid_request", e.getMessage()));
    }
}
