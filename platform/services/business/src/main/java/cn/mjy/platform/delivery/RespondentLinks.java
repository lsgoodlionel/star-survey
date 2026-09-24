package cn.mjy.platform.delivery;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.security.SignedParameters;
import cn.mjy.platform.tenant.engine.EngineInstance;
import cn.mjy.platform.tenant.engine.EngineInstanceService;
import cn.mjy.platform.tenant.routing.SurveyRoute;
import cn.mjy.platform.tenant.routing.SurveyRouteService;
import java.util.Map;
import java.util.TreeMap;
import java.util.UUID;
import org.springframework.stereotype.Component;

/**
 * 作答地址的唯一拼装点：问卷公开 UUID → 当前路由（引擎实例 + sid）→ 引擎地址。
 *
 * <p>地址不落库，每次现算：问卷改版重新发布后路由指向新的 sid（ADR 0012），同一条投放链接、同一个二维码
 * 自动指向新版本——"扫码定位正确版本"（R05-01）靠的就是这一点，而不是把地址写死在链接里。
 *
 * <p>只通过 {@link SurveyRouteService} 与 {@link EngineInstanceService} 的公开方法读取，
 * 不直接读别的模块的表；两者都在租户作用域内执行，因此查不到别的租户的路由。
 */
@Component
class RespondentLinks {

    private final SurveyRouteService routes;
    private final EngineInstanceService engines;

    RespondentLinks(SurveyRouteService routes, EngineInstanceService engines) {
        this.routes = routes;
        this.engines = engines;
    }

    /** 问卷是否已经有对外路由（即是否发布过）。 */
    boolean isPublished(TenantId tenant, UUID surveyId) {
        return routes.findByPublicId(tenant, surveyId).isPresent();
    }

    /**
     * 作答地址；query 会按键排序后拼到问号后面（已百分号编码）。
     *
     * @throws DeliveryConflictException 问卷尚未发布，没有可作答的地址
     */
    String respondentUrl(TenantId tenant, UUID surveyId, Map<String, String> query) {
        SurveyRoute route = routes.findByPublicId(tenant, surveyId)
                .orElseThrow(() -> DeliveryExceptions.conflict("survey_not_published",
                        "survey has no live respondent route: " + surveyId));
        EngineInstance instance = engines.find(tenant, route.engineInstanceId())
                .orElseThrow(() -> DeliveryExceptions.conflict("survey_not_published",
                        "engine instance of survey " + surveyId + " is no longer registered"));
        String base = instance.baseUrl().replaceAll("/+$", "") + "/index.php/" + route.engineSid();
        if (query == null || query.isEmpty()) {
            return base;
        }
        return base + "?" + SignedParameters.toQueryString(new TreeMap<>(query));
    }
}
