package cn.mjy.platform.dashboard;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;
import java.util.Arrays;
import java.util.UUID;

public enum DashboardPage {
    EDIT("edit"),
    IMPORT("import"),
    PREVIEW("preview"),
    PUBLISH("publish"),
    RESPONSES("responses"),
    VERSION("version");

    private final String code;

    DashboardPage(String code) {
        this.code = code;
    }

    @JsonValue
    public String code() {
        return code;
    }

    @JsonCreator(mode = JsonCreator.Mode.DELEGATING)
    public static DashboardPage fromCode(String code) {
        return Arrays.stream(values())
                .filter(page -> page.code.equals(code))
                .findFirst()
                .orElseThrow(() -> new IllegalArgumentException("unknown dashboard page: " + code));
    }

    String targetPath(UUID surveyId, Integer version) {
        String base = "/surveys/" + surveyId + "/";
        return this == VERSION ? base + "versions/" + version : base + code;
    }
}
