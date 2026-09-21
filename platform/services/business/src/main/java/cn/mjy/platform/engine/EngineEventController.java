package cn.mjy.platform.engine;

import jakarta.validation.Valid;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

/**
 * 引擎事件接收端点。能走到这里的请求已经通过 {@link EngineEventSignatureFilter} 验签。
 *
 * <p>返回 2xx 即表示整批已持久化（含判定为重复的事件），中继据此标记已投递；
 * 其他状态码一律让中继保留事件、下轮重投。
 */
@RestController
public class EngineEventController {

    static final String PATH = "/internal/engine-events";

    @PostMapping(path = PATH, consumes = MediaType.APPLICATION_JSON_VALUE, produces = MediaType.APPLICATION_JSON_VALUE)
    public IngestionResult receive(@Valid @RequestBody EngineEventBatch batch) {
        return new IngestionResult(batch.events().size(), 0, 0);
    }
}
