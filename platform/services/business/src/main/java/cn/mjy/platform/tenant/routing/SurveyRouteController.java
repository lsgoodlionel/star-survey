package cn.mjy.platform.tenant.routing;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.security.CurrentTenant;
import cn.mjy.platform.tenant.api.NotFoundException;
import cn.mjy.platform.tenant.engine.EngineInstance;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Positive;
import java.util.UUID;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** 租户端：登记与查询本租户的问卷路由。租户只取自已验签令牌。 */
@RestController
@RequestMapping("/v1/survey-routes")
public class SurveyRouteController {

    private final SurveyRouteService routes;
    private final CurrentTenant currentTenant;

    public SurveyRouteController(SurveyRouteService routes, CurrentTenant currentTenant) {
        this.routes = routes;
        this.currentTenant = currentTenant;
    }

    @PostMapping
    public ResponseEntity<RouteView> create(@Valid @RequestBody CreateRoute request) {
        TenantContext context = currentTenant.require();
        return routes.create(context.tenantId(), request.engineInstanceId(), request.engineSid(),
                        context.actorId(), context.traceId())
                .toResponse(RouteView::of);
    }

    @GetMapping("/{publicId}")
    public RouteView get(@PathVariable UUID publicId) {
        return routes.findByPublicId(currentTenant.require().tenantId(), publicId)
                .map(RouteView::of)
                .orElseThrow(() -> notFound());
    }

    /** 反向查找：引擎事件只带 (实例, sid)，据此找到对外 UUID。 */
    @GetMapping(params = {"engineInstanceId", "engineSid"})
    public RouteView findByEngine(@RequestParam @NotBlank String engineInstanceId,
                                  @RequestParam @Positive int engineSid) {
        return routes.findByEngine(currentTenant.require().tenantId(), engineInstanceId, engineSid)
                .map(RouteView::of)
                .orElseThrow(() -> notFound());
    }

    private static NotFoundException notFound() {
        return new NotFoundException("survey route not found");
    }

    public record CreateRoute(
            @NotBlank @Pattern(regexp = EngineInstance.ID_REGEX) String engineInstanceId,
            @NotNull @Positive Integer engineSid) {
    }

    public record RouteView(String publicId, String tenantId, String engineInstanceId, int engineSid) {

        static RouteView of(SurveyRoute route) {
            return new RouteView(route.publicId().toString(), route.tenantId().toString(),
                    route.engineInstanceId(), route.engineSid());
        }
    }
}
