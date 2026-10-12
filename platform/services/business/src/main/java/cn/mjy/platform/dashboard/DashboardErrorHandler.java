package cn.mjy.platform.dashboard;

import org.springframework.dao.DataAccessException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

@RestControllerAdvice(basePackages = "cn.mjy.platform.dashboard")
public class DashboardErrorHandler {

    public record DashboardError(String error, String message) {
    }

    @ExceptionHandler(DashboardNotFoundException.class)
    ResponseEntity<DashboardError> notFound(DashboardNotFoundException e) {
        return respond(HttpStatus.NOT_FOUND, "not_found", "dashboard target not found");
    }

    @ExceptionHandler(DashboardForbiddenException.class)
    ResponseEntity<DashboardError> forbidden(DashboardForbiddenException e) {
        return respond(HttpStatus.FORBIDDEN, "dashboard_forbidden", "dashboard access is forbidden");
    }

    @ExceptionHandler({DashboardInvalidRequestException.class, HttpMessageNotReadableException.class,
            MethodArgumentTypeMismatchException.class})
    ResponseEntity<DashboardError> invalid(Exception e) {
        return respond(HttpStatus.UNPROCESSABLE_CONTENT, "invalid_dashboard_request",
                "dashboard request is invalid");
    }

    @ExceptionHandler({DashboardUnavailableException.class, DataAccessException.class})
    ResponseEntity<DashboardError> unavailable(Exception e) {
        return respond(HttpStatus.SERVICE_UNAVAILABLE, "dashboard_temporarily_unavailable",
                "dashboard is temporarily unavailable");
    }

    private static ResponseEntity<DashboardError> respond(HttpStatus status, String code, String message) {
        return ResponseEntity.status(status).body(new DashboardError(code, message));
    }
}

final class DashboardInvalidRequestException extends RuntimeException {
    DashboardInvalidRequestException(String message) {
        super(message);
    }
}

final class DashboardNotFoundException extends RuntimeException {
    DashboardNotFoundException(String message) {
        super(message);
    }
}

final class DashboardForbiddenException extends RuntimeException {
    DashboardForbiddenException(String message) {
        super(message);
    }
}

final class DashboardUnavailableException extends RuntimeException {
    DashboardUnavailableException(String message, Throwable cause) {
        super(message, cause);
    }
}
