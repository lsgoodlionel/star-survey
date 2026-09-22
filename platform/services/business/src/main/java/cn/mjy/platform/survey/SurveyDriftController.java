package cn.mjy.platform.survey;

import cn.mjy.platform.shared.security.CurrentTenant;
import java.util.List;
import java.util.UUID;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 租户端漂移检查接口（ADR 0012 决定 5）。POST 立即检查并返回这次的记录（200，outcome 为 match / drift / error）；
 * GET 列出该版本最近的检查，新的在前。版本不存在 404，网关未配置 503。
 */
@RestController
@RequestMapping("/v1/surveys/{id}/versions/{version}/drift-checks")
public class SurveyDriftController {

    private final SurveyDriftService drift;
    private final CurrentTenant currentTenant;

    public SurveyDriftController(SurveyDriftService drift, CurrentTenant currentTenant) {
        this.drift = drift;
        this.currentTenant = currentTenant;
    }

    @PostMapping
    public DriftCheckView check(@PathVariable UUID id, @PathVariable int version) {
        return drift.check(currentTenant.require(), id, version);
    }

    @GetMapping
    public List<DriftCheckView> list(@PathVariable UUID id, @PathVariable int version) {
        return drift.checks(currentTenant.require(), id, version);
    }
}
