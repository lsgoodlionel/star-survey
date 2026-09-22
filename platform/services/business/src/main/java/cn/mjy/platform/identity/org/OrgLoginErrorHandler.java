package cn.mjy.platform.identity.org;

import cn.mjy.platform.tenant.api.ApiError;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/** 免登与组织连接的错误映射。只记录错误码与消息（其中不含授权码、令牌、密钥）。 */
@Order(Ordered.HIGHEST_PRECEDENCE)
@RestControllerAdvice(basePackageClasses = OrgLoginErrorHandler.class)
class OrgLoginErrorHandler {

    private static final Logger log = LoggerFactory.getLogger(OrgLoginErrorHandler.class);

    @ExceptionHandler(OrgLoginException.class)
    ResponseEntity<ApiError> loginFailed(OrgLoginException e) {
        String detail = e.getCause() == null ? "" : " (" + e.getCause().getMessage() + ")";
        if (e.status().is5xxServerError()) {
            log.warn("organisation login failed: {} {}{}", e.error(), e.getMessage(), detail);
        } else {
            log.info("organisation login refused: {} {}{}", e.error(), e.getMessage(), detail);
        }
        return ResponseEntity.status(e.status()).cacheControl(CacheControl.noStore())
                .body(new ApiError(e.error(), e.getMessage()));
    }

    /**
     * 事件回调被拒。具体原因（验签、时间戳、解密、格式、接收方、令牌）只写服务端日志；
     * 对匿名调用方一律同一个 401 与同一段响应体，不给出可用来做预言机或探测配置的差异。
     * 413 与 503 不取决于请求内容，照常返回。
     */
    @ExceptionHandler(OrgEventException.class)
    ResponseEntity<ApiError> eventRejected(OrgEventException e) {
        if (e.status().is5xxServerError()) {
            log.warn("organisation event rejected: {} {}", e.error(), e.getMessage());
            return ResponseEntity.status(e.status()).cacheControl(CacheControl.noStore())
                    .body(new ApiError(e.error(), e.getMessage()));
        }
        log.info("organisation event rejected: {} {}", e.error(), e.getMessage());
        if (e.status() == HttpStatus.PAYLOAD_TOO_LARGE) {
            return ResponseEntity.status(e.status()).cacheControl(CacheControl.noStore())
                    .body(new ApiError(e.error(), e.getMessage()));
        }
        return ResponseEntity.status(HttpStatus.UNAUTHORIZED).cacheControl(CacheControl.noStore())
                .body(new ApiError(OrgEventException.INVALID_SIGNATURE, "event rejected"));
    }
}
