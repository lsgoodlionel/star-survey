package cn.mjy.platform.dashboard;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.security.CurrentTenant;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/v1/dashboard")
public class DashboardController {

    private final DashboardService dashboard;
    private final CurrentTenant currentTenant;

    public DashboardController(DashboardService dashboard, CurrentTenant currentTenant) {
        this.dashboard = dashboard;
        this.currentTenant = currentTenant;
    }

    @GetMapping
    public DashboardView get(@RequestParam(required = false) Integer surveyLimit,
            @RequestParam(required = false) Integer taskLimit) {
        DashboardQuery query = DashboardQuery.of(surveyLimit, taskLimit);
        TenantContext context = currentTenant.require();
        return dashboard.getDashboard(context, query.surveyLimit(), query.taskLimit());
    }

    @PostMapping("/recent-work")
    public ResponseEntity<Void> record(@RequestBody RecentWorkCommand command) {
        dashboard.recordRecentWork(currentTenant.require(), command);
        return ResponseEntity.noContent().build();
    }
}
