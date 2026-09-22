package cn.mjy.platform.response;

import java.util.List;
import java.util.UUID;

/** 统计查看者可见的汇总：按版本、按状态的答卷数，不含任何逐行数据。 */
public record ResponseSummary(UUID surveyId, List<VersionCounts> versions, Counts total) {

    public ResponseSummary {
        versions = List.copyOf(versions);
    }

    public record VersionCounts(int version, Counts counts) {
    }

    public record Counts(long inProgress, long engineCompleted, long deleted) {

        public static final Counts ZERO = new Counts(0, 0, 0);

        public Counts plus(Counts other) {
            return new Counts(inProgress + other.inProgress, engineCompleted + other.engineCompleted,
                    deleted + other.deleted);
        }
    }
}
