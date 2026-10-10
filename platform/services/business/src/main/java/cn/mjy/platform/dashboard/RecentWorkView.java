package cn.mjy.platform.dashboard;

import java.time.Instant;
import java.util.UUID;

public record RecentWorkView(
        UUID surveyId,
        String surveyName,
        DashboardPage page,
        String targetPath,
        Instant visitedAt) {
}
