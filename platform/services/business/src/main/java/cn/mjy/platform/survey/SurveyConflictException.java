package cn.mjy.platform.survey;

import java.util.Objects;

/**
 * 请求与问卷当前状态冲突（409），code 是机器可读的原因：
 * {@code version_conflict}、{@code publish_in_progress}、{@code already_published}、
 * {@code no_active_engine_instance}、{@code route_conflict}。
 */
public class SurveyConflictException extends RuntimeException {

    static final String VERSION_CONFLICT = "version_conflict";
    static final String PUBLISH_IN_PROGRESS = "publish_in_progress";
    static final String ALREADY_PUBLISHED = "already_published";
    static final String NO_ACTIVE_ENGINE_INSTANCE = "no_active_engine_instance";

    private final String code;

    public SurveyConflictException(String code, String message) {
        super(message);
        this.code = Objects.requireNonNull(code, "code");
    }

    public String code() {
        return code;
    }
}
