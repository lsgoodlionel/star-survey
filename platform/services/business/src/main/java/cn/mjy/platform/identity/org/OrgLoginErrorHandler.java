package cn.mjy.platform.identity.org;

import cn.mjy.platform.tenant.api.ApiError;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.http.CacheControl;
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

    /** 事件回调被拒：只记录错误码与消息（不含密钥、明文、密文），不回显任何请求内容。 */
    @ExceptionHandler(OrgEventException.class)
    ResponseEntity<ApiError> eventRejected(OrgEventException e) {
        if (e.status().is5xxServerError()) {
            log.warn("organisation event rejected: {} {}", e.error(), e.getMessage());
        } else {
            log.info("organisation event rejected: {} {}", e.error(), e.getMessage());
        }
        return ResponseEntity.status(e.status()).cacheControl(CacheControl.noStore())
                .body(new ApiError(e.error(), e.getMessage()));
    }
}
