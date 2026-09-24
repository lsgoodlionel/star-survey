package cn.mjy.platform.asset;

import java.util.Locale;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;
import org.springframework.web.multipart.MaxUploadSizeExceededException;

/**
 * 资产接口的错误映射。跨租户走 404（不泄漏"存在与否"），权限不足按 access 给出的原因码 403。
 *
 * <p>只管作者端 {@code /v1/assets}：匿名取件端点自己给出逐字节相同的 404 / 410，不经过这里。
 */
@RestControllerAdvice(basePackages = "cn.mjy.platform.asset")
public class AssetErrorHandler {

    /** 统一错误体：稳定的错误码 + 可读说明。 */
    public record AssetError(String error, String message) {
    }

    private static final Logger log = LoggerFactory.getLogger(AssetErrorHandler.class);

    @ExceptionHandler(AssetNotFoundException.class)
    ResponseEntity<AssetError> notFound(AssetNotFoundException e) {
        return respond(HttpStatus.NOT_FOUND, "not_found", e.getMessage());
    }

    @ExceptionHandler(AssetAccessDeniedException.class)
    ResponseEntity<AssetError> denied(AssetAccessDeniedException e) {
        return respond(HttpStatus.FORBIDDEN, e.reason().name().toLowerCase(Locale.ROOT), e.getMessage());
    }

    @ExceptionHandler(AssetConflictException.class)
    ResponseEntity<AssetError> conflict(AssetConflictException e) {
        return respond(HttpStatus.CONFLICT, e.code(), e.getMessage());
    }

    @ExceptionHandler({InvalidAssetException.class, MethodArgumentTypeMismatchException.class,
            MissingServletRequestParameterException.class})
    ResponseEntity<AssetError> invalid(Exception e) {
        String message = e instanceof InvalidAssetException ? e.getMessage() : "malformed request";
        return respond(HttpStatus.BAD_REQUEST, "invalid_request", message);
    }

    /** 容器在读完请求体之前就抛这一条，所以它到不了服务层的大小闸门，必须单独接住。 */
    @ExceptionHandler(MaxUploadSizeExceededException.class)
    ResponseEntity<AssetError> tooLarge(MaxUploadSizeExceededException e) {
        return respond(HttpStatus.BAD_REQUEST, "invalid_request", "the uploaded file is too large");
    }

    /**
     * 存储写不进去、主密钥没配：503。详细原因只进服务端日志——里面可能有存储路径。
     */
    @ExceptionHandler(AssetUnavailableException.class)
    ResponseEntity<AssetError> unavailable(AssetUnavailableException e) {
        log.error("asset service unavailable", e);
        return respond(HttpStatus.SERVICE_UNAVAILABLE, "asset_service_unavailable",
                "the asset service is not available");
    }

    private static ResponseEntity<AssetError> respond(HttpStatus status, String code, String message) {
        return ResponseEntity.status(status).body(new AssetError(code, message));
    }
}
