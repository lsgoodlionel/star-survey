package cn.mjy.platform.engine;

import jakarta.validation.Valid;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

/**
 * 引擎事件接收端点。能走到这里的请求已经通过 {@link EngineEventSignatureFilter} 验签，
 * 发送方实例取自过滤器写入的请求属性（不读请求头、不信请求体）。
 *
 * <p>返回 2xx 即表示整批已持久化（含判定为重复的事件），中继据此标记已投递；
 * 其他状态码一律让中继保留事件、下轮重投（错误码见 {@link EngineEventExceptionHandler}）。
 */
@RestController
public class EngineEventController {

    static final String PATH = "/internal/engine-events";

    private final EngineEventIngestion ingestion;

    public EngineEventController(EngineEventIngestion ingestion) {
        this.ingestion = ingestion;
    }

    @PostMapping(path = PATH, consumes = MediaType.APPLICATION_JSON_VALUE, produces = MediaType.APPLICATION_JSON_VALUE)
    public IngestionResult receive(
            @RequestAttribute(EngineEventSignatureFilter.AUTHENTICATED_INSTANCE_ATTRIBUTE) String authenticatedInstance,
            @Valid @RequestBody EngineEventBatch batch) {
        return ingestion.ingest(authenticatedInstance, batch);
    }
}
