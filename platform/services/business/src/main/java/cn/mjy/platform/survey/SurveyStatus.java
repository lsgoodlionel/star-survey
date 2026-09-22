package cn.mjy.platform.survey;

import com.fasterxml.jackson.annotation.JsonValue;
import java.util.Locale;

/**
 * 发布状态机（与 V500 的 CHECK 约束一致）：
 * <pre>
 * draft ──> publishing ──> published（终态：重新发布尚未实现，见 ADR 0009 已知限制 2）
 *                │  ├──> publish_failed ──────────> publishing（新 requestId）
 *                │  └──> pending_reconciliation ──> publishing（同一 requestId，靠网关幂等）
 * </pre>
 * publishing 超过 {@link SurveyPublishService#STALE_PUBLISHING} 仍未收尾（进程崩溃等）视同待核对。
 */
public enum SurveyStatus {
    DRAFT,
    PUBLISHING,
    PUBLISHED,
    PUBLISH_FAILED,
    PENDING_RECONCILIATION;

    @JsonValue
    public String code() {
        return name().toLowerCase(Locale.ROOT);
    }

    public static SurveyStatus fromCode(String code) {
        for (SurveyStatus status : values()) {
            if (status.code().equals(code)) {
                return status;
            }
        }
        throw new IllegalArgumentException("unknown survey status: " + code);
    }
}
