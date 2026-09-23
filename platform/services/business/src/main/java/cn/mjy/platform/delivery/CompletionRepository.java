package cn.mjy.platform.delivery;

import cn.mjy.platform.shared.TenantId;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * 应答者完成记录。必须在 {@code TenantScope} 内调用。
 *
 * <p>催答的全部判断都落在 {@link #isCompleted}：读到一行就不催。记录只追加不删除，
 * 所以"已完成"这个事实一旦成立就不会回退——即使答卷随后被删除，也不该再去催这个人。
 */
@Repository
class CompletionRepository {

    private final JdbcClient jdbc;

    CompletionRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    boolean isCompleted(UUID surveyId, String respondentKey) {
        return jdbc.sql("""
                        SELECT EXISTS (SELECT 1 FROM delivery_completion
                                        WHERE survey_id = :survey AND respondent_key = :key)
                        """)
                .param("survey", surveyId)
                .param("key", respondentKey)
                .query(Boolean.class)
                .single();
    }

    /** 登记完成；已登记时返回 false（保留首次的完成时刻）。 */
    boolean record(TenantId tenant, UUID surveyId, String respondentKey, Instant completedAt, String source) {
        return jdbc.sql("""
                        INSERT INTO delivery_completion (tenant_id, survey_id, respondent_key, completed_at, source)
                        VALUES (:tenant, :survey, :key, :completedAt, :source)
                        ON CONFLICT (tenant_id, survey_id, respondent_key) DO NOTHING
                        """)
                .param("tenant", tenant.value())
                .param("survey", surveyId)
                .param("key", respondentKey)
                .param("completedAt", OffsetDateTime.ofInstant(completedAt, ZoneOffset.UTC))
                .param("source", source)
                .update() == 1;
    }
}
