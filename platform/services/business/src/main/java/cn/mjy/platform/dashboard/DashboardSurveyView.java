package cn.mjy.platform.dashboard;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

public record DashboardSurveyView(
        UUID surveyId,
        String name,
        int draftVersion,
        Integer publishedVersion,
        String publishState,
        Long completedResponses,
        Instant updatedAt,
        List<String> actions) {

    public DashboardSurveyView {
        actions = List.copyOf(actions);
    }
}
