package cn.mjy.platform.engine;

import cn.mjy.platform.engine.InvalidEngineEventException.Reason;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataAccessException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/**
 * 引擎事件端点的错误响应，只作用于 {@link EngineEventController}。
 *
 * <p>所有错误都直接写出响应体，不走 {@code sendError} → /error 的转发：/error 属于主安全链（要求 JWT），
 * 转发过去会把真实错误码变成 401，中继日志就看不出原因了。
 *
 * <table>
 *   <caption>错误码</caption>
 *   <tr><th>状态</th><th>error</th><th>含义（全部导致中继保留事件、下轮重投）</th></tr>
 *   <tr><td>400</td><td>invalid_batch / invalid_occurred_at</td><td>请求体不是合法的批次</td></tr>
 *   <tr><td>422</td><td>unknown_engine_instance</td><td>实例未登记或已停用，登记后自动补投</td></tr>
 *   <tr><td>422</td><td>unsupported_schema_version / unsupported_event_type</td><td>平台尚不认识，升级后补投</td></tr>
 *   <tr><td>503</td><td>temporarily_unavailable</td><td>数据库或目录暂不可用</td></tr>
 * </table>
 */
@RestControllerAdvice(assignableTypes = EngineEventController.class)
class EngineEventExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(EngineEventExceptionHandler.class);

    @ExceptionHandler({HttpMessageNotReadableException.class, MethodArgumentNotValidException.class})
    ResponseEntity<Map<String, String>> malformed(Exception e) {
        log.warn("rejected malformed engine event batch: {}", e.getMessage());
        return error(HttpStatus.BAD_REQUEST, "invalid_batch");
    }

    @ExceptionHandler(InvalidEngineEventException.class)
    ResponseEntity<Map<String, String>> invalid(InvalidEngineEventException e) {
        log.warn("rejected engine event batch: {}", e.getMessage());
        HttpStatus status = e.reason() == Reason.INVALID_OCCURRED_AT
                ? HttpStatus.BAD_REQUEST
                : HttpStatus.UNPROCESSABLE_CONTENT;
        return error(status, e.reason().code());
    }

    @ExceptionHandler(UnknownEngineInstanceException.class)
    ResponseEntity<Map<String, String>> unknownInstance(UnknownEngineInstanceException e) {
        log.warn("rejected engine event batch from unregistered instances {}", e.instances());
        return error(HttpStatus.UNPROCESSABLE_CONTENT, "unknown_engine_instance");
    }

    @ExceptionHandler({DataAccessException.class, EngineDirectoryUnavailableException.class})
    ResponseEntity<Map<String, String>> unavailable(RuntimeException e) {
        log.error("engine event batch could not be stored; the relay will retry", e);
        return error(HttpStatus.SERVICE_UNAVAILABLE, "temporarily_unavailable");
    }

    private static ResponseEntity<Map<String, String>> error(HttpStatus status, String code) {
        return ResponseEntity.status(status).body(Map.of("error", code));
    }
}
