package cn.mjy.platform.response;

import cn.mjy.platform.shared.security.CurrentTenant;
import java.util.UUID;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 答卷查询接口（蓝图 API 表 {@code GET /v1/responses}）。租户与操作者只取自已验签令牌。
 * 明细含个人数据，响应一律 {@code Cache-Control: no-store}。
 */
@RestController
public class ResponseQueryController {

    private final ResponseQueryService responses;
    private final CurrentTenant currentTenant;

    public ResponseQueryController(ResponseQueryService responses, CurrentTenant currentTenant) {
        this.responses = responses;
        this.currentTenant = currentTenant;
    }

    @GetMapping("/v1/surveys/{id}/responses")
    public ResponseEntity<ResponsePage> list(@PathVariable UUID id,
            @RequestParam(required = false) String state,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit) {
        return noStore(responses.list(currentTenant.require(), id, state, cursor, limit));
    }

    /** 同一查询的蓝图写法：问卷放在查询参数里。 */
    @GetMapping("/v1/responses")
    public ResponseEntity<ResponsePage> listBySurvey(@RequestParam UUID surveyId,
            @RequestParam(required = false) String state,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit) {
        return list(surveyId, state, cursor, limit);
    }

    @GetMapping("/v1/surveys/{id}/responses/summary")
    public ResponseEntity<ResponseSummary> summary(@PathVariable UUID id) {
        return noStore(responses.summary(currentTenant.require(), id));
    }

    @GetMapping("/v1/surveys/{id}/fields")
    public FieldDictionary fields(@PathVariable UUID id) {
        return responses.fields(currentTenant.require(), id);
    }

    private static <T> ResponseEntity<T> noStore(T body) {
        return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(body);
    }
}
