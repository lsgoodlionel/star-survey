package cn.mjy.platform.dashboard;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.time.Instant;
import java.util.List;
import java.util.Objects;
import org.springframework.dao.DataAccessException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

@Service
public class DashboardService {

    private final TenantScope tenantScope;
    private final DashboardRepository dashboard;
    private final DashboardRecentWorkRepository recentWork;

    public DashboardService(TenantScope tenantScope, DashboardRepository dashboard,
            DashboardRecentWorkRepository recentWork) {
        this.tenantScope = tenantScope;
        this.dashboard = dashboard;
        this.recentWork = recentWork;
    }

    @Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
    public DashboardView getDashboard(TenantContext context, int surveyLimit, int taskLimit) {
        Objects.requireNonNull(context, "context");
        DashboardQuery query = new DashboardQuery(surveyLimit, taskLimit);
        try {
            return tenantScope.call(context.tenantId(), () -> {
                DashboardRepository.Aggregate aggregate = dashboard.load(
                        context.tenantId(), context.actorId(), query.surveyLimit(), query.taskLimit());
                if (!aggregate.activeMember()) {
                    throw new DashboardForbiddenException("dashboard access is forbidden");
                }
                List<RecentWorkView> recent = recentWork.findVisible(
                        context.tenantId(), context.actorId(), DashboardQuery.MAX_LIMIT);
                return new DashboardView(aggregate.generatedAt(), aggregate.visibleSections(), aggregate.summary(),
                        aggregate.tasks(), aggregate.surveys(), recent);
            });
        } catch (DataAccessException e) {
            throw new DashboardUnavailableException("dashboard dependency failed", e);
        }
    }

    public void recordRecentWork(TenantContext context, RecentWorkCommand command) {
        Objects.requireNonNull(context, "context");
        Objects.requireNonNull(command, "command");
        try {
            if (!recentWork.upsert(context.tenantId(), context.actorId(), command, Instant.now())) {
                throw new DashboardNotFoundException("dashboard target not found");
            }
        } catch (DataAccessException e) {
            throw new DashboardUnavailableException("dashboard dependency failed", e);
        }
    }
}
