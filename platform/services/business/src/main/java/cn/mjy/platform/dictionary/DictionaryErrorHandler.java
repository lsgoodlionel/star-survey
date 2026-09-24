package cn.mjy.platform.dictionary;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

/** 字典模块的错误映射。错误码稳定、消息可读，不泄漏内部细节。 */
@RestControllerAdvice(basePackages = "cn.mjy.platform.dictionary")
class DictionaryErrorHandler {

    public record DictionaryError(String error, String message) {
    }

    @ExceptionHandler(DictionaryNotFoundException.class)
    ResponseEntity<DictionaryError> notFound(DictionaryNotFoundException e) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(new DictionaryError("not_found", e.getMessage()));
    }

    @ExceptionHandler(DictionaryConflictException.class)
    ResponseEntity<DictionaryError> conflict(DictionaryConflictException e) {
        return ResponseEntity.status(HttpStatus.CONFLICT)
                .body(new DictionaryError(e.code(), e.getMessage()));
    }

    @ExceptionHandler({InvalidDictionaryRequestException.class, MethodArgumentTypeMismatchException.class,
            MissingServletRequestParameterException.class, HttpMessageNotReadableException.class,
            MethodArgumentNotValidException.class})
    ResponseEntity<DictionaryError> invalid(Exception e) {
        // 框架异常的原文会带进内部类型名，统一压成一句话。
        String message = e instanceof InvalidDictionaryRequestException ? e.getMessage() : "malformed request";
        return ResponseEntity.badRequest().body(new DictionaryError("invalid_request", message));
    }
}
