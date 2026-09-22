package cn.mjy.platform.survey;

import java.time.Duration;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * 核对任务专用的读写。与 {@link SurveyRepository} 一样必须在 {@code TenantScope} 内调用：
 * 查询不带 tenant_id 条件，可见范围完全由行级安全决定——因此一个租户的扫描看不到别的租户的问卷。
 */
@Repository
class PublishReconciliationRepository {

    /**
     * 待核对的判定：pending_reconciliation，或 publishing 已超过陈旧阈值；且当前尝试未被标记人工复核、
     * 退避时间已到。{@code s} 为 survey，{@code a} 为其当前尝试。
     */
    private static final String CANDIDATE = """
            (s.status = 'pending_reconciliation'
               OR (s.status = 'publishing' AND s.publishing_started_at < now() - make_interval(secs => :stale)))
            AND a.manual_review_at IS NULL
            AND (a.next_reconcile_at IS NULL OR a.next_reconcile_at <= now())""";

    private final JdbcClient jdbc;

    PublishReconciliationRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /** 持锁后的复核结果：due 为真表示此刻（锁内）仍满足待核对条件。 */
    record LockedCandidate(UUID surveyId, UUID requestId, boolean due) {
    }

    /** 本租户内到期的待核对问卷，最早到期的在前。 */
    List<UUID> dueSurveys(Duration staleAfter, int limit) {
        return jdbc.sql("""
                        SELECT s.id FROM survey s
                          JOIN survey_publish_attempt a
                            ON a.tenant_id = s.tenant_id AND a.request_id = s.current_request_id
                         WHERE s.status IN ('publishing', 'pending_reconciliation') AND
                        """ + CANDIDATE + """

                         ORDER BY COALESCE(a.next_reconcile_at, a.created_at), s.id
                         LIMIT :limit
                        """)
                .param("stale", (double) staleAfter.toSeconds())
                .param("limit", limit)
                .query(UUID.class)
                .list();
    }

    /**
     * 取问卷行锁但不等待（FOR UPDATE SKIP LOCKED）：用户的发布或另一个核对副本正持有锁时直接跳过，
     * 下一轮再看。锁内重新判定是否仍待核对——扫描与加锁之间别人可能已经收尾或重新发起。
     */
    Optional<LockedCandidate> lockIfFree(UUID surveyId, Duration staleAfter) {
        return jdbc.sql("""
                        SELECT s.id, s.current_request_id, (
                        """ + CANDIDATE + """
                        ) AS due
                          FROM survey s
                          JOIN survey_publish_attempt a
                            ON a.tenant_id = s.tenant_id AND a.request_id = s.current_request_id
                         WHERE s.id = :id
                           FOR UPDATE OF s SKIP LOCKED
                        """)
                .param("stale", (double) staleAfter.toSeconds())
                .param("id", surveyId)
                .query((rs, row) -> new LockedCandidate(
                        rs.getObject("id", UUID.class),
                        rs.getObject("current_request_id", UUID.class),
                        rs.getBoolean("due")))
                .optional();
    }

    /** 本次重发之后的退避：结果仍未知时，下一次不早于此刻 + delay。 */
    void scheduleNext(UUID requestId, Duration delay) {
        jdbc.sql("""
                        UPDATE survey_publish_attempt SET next_reconcile_at = now() + make_interval(secs => :delay)
                         WHERE request_id = :request
                        """)
                .param("delay", (double) delay.toSeconds())
                .param("request", requestId)
                .update();
    }

    /** 标记人工复核；已标记过则不再改动，返回是否是本次标记的。 */
    boolean flagManualReview(UUID requestId) {
        return jdbc.sql("""
                        UPDATE survey_publish_attempt SET manual_review_at = now()
                         WHERE request_id = :request AND manual_review_at IS NULL
                        """)
                .param("request", requestId)
                .update() == 1;
    }
}
