package cn.mjy.platform.contacts;

import java.util.Locale;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

/**
 * 通讯录接口的错误映射。跨租户与越出数据范围都走 404（不泄漏"存在与否"），
 * 权限不足按 access 给出的原因码 403。错误体不含任何个人信息。
 */
@RestControllerAdvice(basePackages = "cn.mjy.platform.contacts")
public class ContactsErrorHandler {

    /** 统一错误体：稳定的错误码 + 可读说明。 */
    public record ContactError(String error, String message) {
    }

    @ExceptionHandler(ContactNotFoundException.class)
    ResponseEntity<ContactError> notFound(ContactNotFoundException e) {
        return respond(HttpStatus.NOT_FOUND, "not_found", e.getMessage());
    }

    @ExceptionHandler(ContactAccessDeniedException.class)
    ResponseEntity<ContactError> denied(ContactAccessDeniedException e) {
        return respond(HttpStatus.FORBIDDEN, e.reason().name().toLowerCase(Locale.ROOT), e.getMessage());
    }

    @ExceptionHandler(ContactConflictException.class)
    ResponseEntity<ContactError> conflict(ContactConflictException e) {
        return respond(HttpStatus.CONFLICT, e.code(), e.getMessage());
    }

    @ExceptionHandler({InvalidContactRequestException.class, MethodArgumentTypeMismatchException.class,
            MissingServletRequestParameterException.class, HttpMessageNotReadableException.class,
            MethodArgumentNotValidException.class})
    ResponseEntity<ContactError> invalid(Exception e) {
        String message = e instanceof InvalidContactRequestException ? e.getMessage() : "malformed request";
        return respond(HttpStatus.BAD_REQUEST, "invalid_request", message);
    }

    private static ResponseEntity<ContactError> respond(HttpStatus status, String code, String message) {
        return ResponseEntity.status(status).body(new ContactError(code, message));
    }
}
