package cn.mjy.platform.delivery;

import cn.mjy.platform.shared.TenantId;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;
import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.json.JsonMapper;

/**
 * delivery_recipient 与发送台账 delivery_send 的读写。必须在 {@code TenantScope} 内调用。
 *
 * <p>{@code address} 是租户联系数据：本类把它读出来交给消息渲染，除此之外不放进任何日志或异常。
 */
@Repository
class RecipientRepository {

    private static final String COLUMNS = """
            task_id, id, address, display_name, engine_token, respondent_key, params::text AS params,
            state, attempts, provider_message_id, last_error, reminders_sent, last_reminder_at, sent_at""";

    private static final TypeReference<Map<String, String>> PARAMS = new TypeReference<>() {
    };

    /** 一个收件人的存储形态。 */
    record RecipientRow(
            UUID taskId,
            UUID id,
            String address,
            String displayName,
            String engineToken,
            String respondentKey,
            Map<String, String> params,
            RecipientState state,
            int attempts,
            String providerMessageId,
            String lastError,
            int remindersSent,
            OffsetDateTime lastReminderAt,
            OffsetDateTime sentAt) {
    }

    private final JdbcClient jdbc;
    private final JsonMapper json;

    RecipientRepository(JdbcClient jdbc, JsonMapper json) {
        this.jdbc = jdbc;
        this.json = json;
    }

    /** 新增收件人；同一任务里地址重复时什么都不做并返回 false（名单里的重复行不会被投递两次）。 */
    boolean insert(TenantId tenant, UUID taskId, UUID id, String address, String displayName, String engineToken,
            String respondentKey, Map<String, String> params) {
        return jdbc.sql("""
                        INSERT INTO delivery_recipient (tenant_id, task_id, id, address, display_name,
                                                        engine_token, respondent_key, params)
                        VALUES (:tenant, :task, :id, :address, :name, :token, :key, CAST(:params AS jsonb))
                        ON CONFLICT (tenant_id, task_id, address) DO NOTHING
                        """)
                .param("tenant", tenant.value())
                .param("task", taskId)
                .param("id", id)
                .param("address", address)
                .param("name", displayName)
                .param("token", engineToken)
                .param("key", respondentKey)
                .param("params", json.writeValueAsString(params))
                .update() == 1;
    }

    List<RecipientRow> list(UUID taskId) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM delivery_recipient WHERE task_id = :task ORDER BY id")
                .param("task", taskId)
                .query(this::map)
                .list();
    }

    /**
     * 还等着发的收件人：queued，加上<b>租约已过期</b>的 sending。
     *
     * <p>sending 有两种可能：某个副本正在调渠道商（认领事务已提交、行锁已释放），或者那个副本死了。
     * 数据库分不出来，所以按租约判断——{@code updated_at} 是认领时刻，超过租约仍是 sending 才当作遗留。
     * 租约要明显长于渠道商调用的超时，否则两个副本会对同一个人同时发送，
     * 而那已经不是"崩溃后恢复"，是每次正常发送都会出现的重复投递。
     */
    List<UUID> pending(UUID taskId, int limit, long leaseSeconds) {
        return jdbc.sql("""
                        SELECT id FROM delivery_recipient
                         WHERE task_id = :task
                           AND (state = 'queued'
                                OR (state = 'sending' AND updated_at < now() - CAST(:lease AS interval)))
                         ORDER BY id LIMIT :limit
                        """)
                .param("task", taskId)
                .param("lease", leaseSeconds + " seconds")
                .param("limit", limit)
                .query((rs, n) -> rs.getObject("id", UUID.class))
                .list();
    }

    int countPending(UUID taskId) {
        return jdbc.sql("""
                        SELECT COUNT(*) FROM delivery_recipient
                         WHERE task_id = :task AND state IN ('queued', 'sending')
                        """)
                .param("task", taskId)
                .query(Integer.class)
                .single();
    }

    Optional<RecipientRow> find(UUID taskId, UUID id) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM delivery_recipient WHERE task_id = :task AND id = :id")
                .param("task", taskId)
                .param("id", id)
                .query(this::map)
                .optional();
    }

    /**
     * 取收件人行并加锁（别的副本同时处理同一行时跳过）。认领、完成判定、催答计数都在这把锁下完成。
     */
    Optional<RecipientRow> lock(UUID taskId, UUID id) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM delivery_recipient"
                        + " WHERE task_id = :task AND id = :id FOR UPDATE SKIP LOCKED")
                .param("task", taskId)
                .param("id", id)
                .query(this::map)
                .optional();
    }

    void markSending(UUID taskId, UUID id) {
        jdbc.sql("""
                        UPDATE delivery_recipient
                           SET state = 'sending', attempts = attempts + 1, updated_at = now()
                         WHERE task_id = :task AND id = :id
                        """)
                .param("task", taskId)
                .param("id", id)
                .update();
    }

    void markOutcome(UUID taskId, UUID id, RecipientState state, String providerMessageId, String error) {
        jdbc.sql("""
                        UPDATE delivery_recipient
                           SET state = :state,
                               provider_message_id = COALESCE(:messageId, provider_message_id),
                               last_error = :error,
                               sent_at = CASE WHEN :state = 'sent' THEN now() ELSE sent_at END,
                               updated_at = now()
                         WHERE task_id = :task AND id = :id
                        """)
                .param("task", taskId)
                .param("id", id)
                .param("state", state.code())
                .param("messageId", providerMessageId)
                .param("error", error)
                .update();
    }

    /** 退回 queued：暂时性失败时重试，尝试次数已经记过了。 */
    void markRetryable(UUID taskId, UUID id, String error) {
        jdbc.sql("""
                        UPDATE delivery_recipient SET state = 'queued', last_error = :error, updated_at = now()
                         WHERE task_id = :task AND id = :id
                        """)
                .param("task", taskId)
                .param("id", id)
                .param("error", error)
                .update();
    }

    /**
     * 认领一次发送：幂等键随行固定。行已存在（上一次崩在发送途中）时返回既有的键，
     * 于是重发用的是同一个键，渠道商据此只投递一次。
     */
    String claimSend(TenantId tenant, UUID taskId, UUID recipientId, String idempotencyKey) {
        jdbc.sql("""
                        INSERT INTO delivery_send (tenant_id, task_id, recipient_id, idempotency_key)
                        VALUES (:tenant, :task, :recipient, :key)
                        ON CONFLICT (tenant_id, task_id, recipient_id) DO NOTHING
                        """)
                .param("tenant", tenant.value())
                .param("task", taskId)
                .param("recipient", recipientId)
                .param("key", idempotencyKey)
                .update();
        return jdbc.sql("""
                        SELECT idempotency_key FROM delivery_send
                         WHERE task_id = :task AND recipient_id = :recipient
                        """)
                .param("task", taskId)
                .param("recipient", recipientId)
                .query(String.class)
                .single();
    }

    void completeSend(UUID taskId, UUID recipientId, String provider, String providerMessageId) {
        jdbc.sql("""
                        UPDATE delivery_send
                           SET completed_at = now(), provider = :provider, provider_message_id = :messageId
                         WHERE task_id = :task AND recipient_id = :recipient
                        """)
                .param("task", taskId)
                .param("recipient", recipientId)
                .param("provider", provider)
                .param("messageId", providerMessageId)
                .update();
    }

    // --- 催答相关 ---

    /**
     * 该首邀任务里可能需要催答的收件人：已发出、催答次数未满、且距上一次（或首邀）已够久。
     * 这里只做粗筛；是否真的发，由 {@link #lock} 之后在同一事务里的复核决定。
     */
    List<UUID> reminderCandidates(UUID sourceTaskId, int maxReminders, long firstDelaySeconds,
            long intervalSeconds, int limit) {
        return jdbc.sql("""
                        SELECT id FROM delivery_recipient
                         WHERE task_id = :task AND state = 'sent' AND reminders_sent < :max
                           AND CASE WHEN reminders_sent = 0
                                    THEN COALESCE(sent_at, updated_at) + CAST(:firstDelay AS interval)
                                    ELSE last_reminder_at + CAST(:interval AS interval)
                               END <= now()
                         ORDER BY id LIMIT :limit
                        """)
                .param("task", sourceTaskId)
                .param("max", maxReminders)
                .param("firstDelay", firstDelaySeconds + " seconds")
                .param("interval", intervalSeconds + " seconds")
                .param("limit", limit)
                .query((rs, n) -> rs.getObject("id", UUID.class))
                .list();
    }

    void recordReminder(UUID taskId, UUID id) {
        jdbc.sql("""
                        UPDATE delivery_recipient
                           SET reminders_sent = reminders_sent + 1, last_reminder_at = now(), updated_at = now()
                         WHERE task_id = :task AND id = :id
                        """)
                .param("task", taskId)
                .param("id", id)
                .update();
    }

    private RecipientRow map(ResultSet rs, int rowNum) throws SQLException {
        return new RecipientRow(
                rs.getObject("task_id", UUID.class),
                rs.getObject("id", UUID.class),
                rs.getString("address"),
                rs.getString("display_name"),
                rs.getString("engine_token"),
                rs.getString("respondent_key"),
                json.readValue(rs.getString("params"), PARAMS),
                RecipientState.fromCode(rs.getString("state")),
                rs.getInt("attempts"),
                rs.getString("provider_message_id"),
                rs.getString("last_error"),
                rs.getInt("reminders_sent"),
                rs.getObject("last_reminder_at", OffsetDateTime.class),
                rs.getObject("sent_at", OffsetDateTime.class));
    }
}
