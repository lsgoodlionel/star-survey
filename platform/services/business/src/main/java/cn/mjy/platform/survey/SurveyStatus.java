package cn.mjy.platform.survey;

import com.fasterxml.jackson.annotation.JsonValue;
import java.util.Locale;

/**
 * 发布状态机（与 V500 / V530 的 CHECK 约束一致）：
 * <pre>
 * draft ──> publishing ──> published ──（草稿改动后重新发布，新 requestId）──> publishing
 *                │  ├──> publish_failed ──────────> publishing（新 requestId）
 *                │  └──> pending_reconciliation ──> publishing（同一 requestId，靠网关幂等）
 * </pre>
 * status 描述的是<b>最近一次发布尝试</b>；在线与否由 {@code publishedVersion} 决定（ADR 0012）。
 * publishing / publish_failed / pending_reconciliation 搭配非空的 publishedVersion，就是
 * "第 N 版在线、第 N+1 版在途（或失败、待核对）"这一子状态：此时公开路由仍指向第 N 版，
 * 直到第 N+1 版落库的同一事务里切换。published 必有 publishedVersion（V530 约束）。
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
