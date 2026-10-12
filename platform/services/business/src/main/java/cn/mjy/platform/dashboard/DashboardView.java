package cn.mjy.platform.dashboard;

import java.time.Instant;
import java.util.List;

public record DashboardView(
        Instant generatedAt,
        List<String> visibleSections,
        DashboardSummary summary,
        List<DashboardTaskView> tasks,
        List<DashboardSurveyView> surveys,
        List<RecentWorkView> recentWork) {

    public DashboardView {
        visibleSections = List.copyOf(visibleSections);
        tasks = List.copyOf(tasks);
        surveys = List.copyOf(surveys);
        recentWork = List.copyOf(recentWork);
    }
}
