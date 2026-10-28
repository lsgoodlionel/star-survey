package cn.mjy.platform.dashboard;

import java.time.Instant;
import java.util.UUID;

public record DashboardTaskView(
        String taskKey,
        String kind,
        UUID surveyId,
        String surveyName,
        String status,
        Instant updatedAt,
        String targetPath) {
}
