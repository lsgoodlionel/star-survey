package cn.mjy.platform.dashboard;

import java.util.Objects;
import java.util.UUID;

public record RecentWorkCommand(UUID surveyId, DashboardPage page, Integer version) {

    public RecentWorkCommand {
        Objects.requireNonNull(surveyId, "surveyId");
        Objects.requireNonNull(page, "page");
        if (page == DashboardPage.VERSION) {
            if (version == null || version <= 0) {
                throw new IllegalArgumentException("version page requires a positive version");
            }
        } else if (version != null) {
            throw new IllegalArgumentException("version is only valid for the version page");
        }
    }
}
