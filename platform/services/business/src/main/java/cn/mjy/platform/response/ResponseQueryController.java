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

    /**
     * 明细的一页。复合筛选（R06-01）：状态、已发布版本号、首事件时间区间、完成时间区间，
     * 都可缺省、可任意组合；时间是 ISO-8601 时刻（如 {@code 2026-09-20T00:00:00Z}），区间闭合。
     */
    @GetMapping("/v1/surveys/{id}/responses")
    public ResponseEntity<ResponsePage> list(@PathVariable UUID id,
            @RequestParam(required = false) String state,
            @RequestParam(required = false) Integer version,
            @RequestParam(required = false) String startedFrom,
            @RequestParam(required = false) String startedTo,
            @RequestParam(required = false) String completedFrom,
            @RequestParam(required = false) String completedTo,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit) {
        ResponseQuery query = ResponseQuery.parse(state, version, startedFrom, startedTo, completedFrom, completedTo,
                cursor, limit);
        return noStore(responses.list(currentTenant.require(), id, query));
    }

    /** 同一查询的蓝图写法：问卷放在查询参数里。 */
    @GetMapping("/v1/responses")
    public ResponseEntity<ResponsePage> listBySurvey(@RequestParam UUID surveyId,
            @RequestParam(required = false) String state,
            @RequestParam(required = false) Integer version,
            @RequestParam(required = false) String startedFrom,
            @RequestParam(required = false) String startedTo,
            @RequestParam(required = false) String completedFrom,
            @RequestParam(required = false) String completedTo,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit) {
        return list(surveyId, state, version, startedFrom, startedTo, completedFrom, completedTo, cursor, limit);
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
