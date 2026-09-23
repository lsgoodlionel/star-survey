package cn.mjy.platform.delivery;

import cn.mjy.platform.delivery.DeliveryTaskService.NewRecipient;
import cn.mjy.platform.delivery.DeliveryTaskService.NewTask;
import cn.mjy.platform.response.ResponseFixture;
import cn.mjy.platform.response.ResponseFixture.Published;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

/** 投放测试的公共场景：一个已发布问卷、一条链接、一个带名单的首邀任务。 */
@Component
class DeliveryFixture {

    static final String EMAIL_BODY = "你好 {{name}}，请填写问卷：{{link}} 退订：{{unsubscribe}}";
    static final String SMS_BODY = "请填写问卷：{{link}}";
    static final String REMINDER_BODY = "你好 {{name}}，问卷还没填完：{{link}} 退订：{{unsubscribe}}";

    private final ResponseFixture responses;
    private final DeliveryLinkService links;
    private final DeliveryTaskService tasks;
    private final DeliveryWorker worker;
    private final TenantScope tenantScope;
    private final JdbcClient jdbc;

    DeliveryFixture(ResponseFixture responses, DeliveryLinkService links, DeliveryTaskService tasks,
            DeliveryWorker worker, TenantScope tenantScope, JdbcClient jdbc) {
        this.responses = responses;
        this.links = links;
        this.tasks = tasks;
        this.worker = worker;
        this.tenantScope = tenantScope;
        this.jdbc = jdbc;
    }

    ResponseFixture responses() {
        return responses;
    }

    Published publishedSurvey() {
        return responses.publishedSurvey();
    }

    LinkView link(Published p, String label) {
        return links.create(p.owner(), p.surveyId(),
                new DeliveryLinkService.NewLink(label, Map.of("src", label), Duration.ofDays(7), true));
    }

    /** 一个首邀任务：count 个邮箱收件人，每人一个邀请码。 */
    TaskView emailTask(Published p, int count) {
        return emailTask(p, count, null);
    }

    TaskView emailTask(Published p, int count, UUID linkId) {
        return tasks.create(p.owner(), new NewTask(p.surveyId(), DeliveryChannel.EMAIL, linkId,
                "请填写问卷", EMAIL_BODY, recipients(count), null));
    }

    List<NewRecipient> recipients(int count) {
        List<NewRecipient> list = new ArrayList<>();
        String suffix = UUID.randomUUID().toString().substring(0, 8);
        for (int i = 1; i <= count; i++) {
            list.add(new NewRecipient("user" + i + "." + suffix + "@example.com", "用户" + i,
                    "tok-" + suffix + "-" + i, Map.of()));
        }
        return list;
    }

    /** 把整个任务跑完（预算足够大），返回处理的收件人数。 */
    int run(TenantId tenant, UUID taskId) {
        return worker.sendPending(tenant, taskId, 1000);
    }

    TaskView status(TenantContext ctx, UUID taskId) {
        return tasks.status(ctx, taskId);
    }

    List<RecipientView> recipientViews(TenantContext ctx, UUID taskId) {
        return tasks.recipients(ctx, taskId);
    }

    /** 任务里某个收件人的邀请码（即应答者标识）：按地址前缀找。 */
    String respondentKeyOf(TenantId tenant, UUID taskId, int index) {
        return tenantScope.call(tenant, () -> jdbc.sql("""
                        SELECT respondent_key FROM delivery_recipient
                         WHERE task_id = :task AND address LIKE :prefix
                        """)
                .param("task", taskId)
                .param("prefix", "user" + index + ".%")
                .query(String.class)
                .single());
    }

    UUID recipientIdOf(TenantId tenant, UUID taskId, int index) {
        return tenantScope.call(tenant, () -> jdbc.sql("""
                        SELECT id FROM delivery_recipient WHERE task_id = :task AND address LIKE :prefix
                        """)
                .param("task", taskId)
                .param("prefix", "user" + index + ".%")
                .query(UUID.class)
                .single());
    }

    String addressOf(TenantId tenant, UUID taskId, int index) {
        return tenantScope.call(tenant, () -> jdbc.sql("""
                        SELECT address FROM delivery_recipient WHERE task_id = :task AND address LIKE :prefix
                        """)
                .param("task", taskId)
                .param("prefix", "user" + index + ".%")
                .query(String.class)
                .single());
    }

    /** 该任务派生出的催答任务（按生成顺序）。 */
    List<UUID> reminderTasks(TenantId tenant, UUID sourceTaskId) {
        return tenantScope.call(tenant, () -> jdbc.sql("""
                        SELECT id FROM delivery_task WHERE source_task_id = :source ORDER BY created_at, id
                        """)
                .param("source", sourceTaskId)
                .query(UUID.class)
                .list());
    }

    /** 把首邀的发出时刻往前挪，让催答的等待条件立刻成立（测试里不等真实时间）。 */
    void ageSentAt(TenantId tenant, UUID taskId, Duration by) {
        tenantScope.run(tenant, () -> jdbc.sql("""
                        UPDATE delivery_recipient
                           SET sent_at = sent_at - CAST(:shift AS interval),
                               last_reminder_at = last_reminder_at - CAST(:shift AS interval)
                         WHERE task_id = :task
                        """)
                .param("task", taskId)
                .param("shift", by.toSeconds() + " seconds")
                .update());
    }

    /** 某个问卷下生成的新答卷通知任务。 */
    List<UUID> notificationTasks(TenantId tenant, UUID surveyId) {
        return tenantScope.call(tenant, () -> jdbc.sql("""
                        SELECT id FROM delivery_task
                         WHERE survey_id = :survey AND kind = 'notification' ORDER BY created_at, id
                        """)
                .param("survey", surveyId)
                .query(UUID.class)
                .list());
    }

    /** 把链接的有效期挪到过去，免得等到真的过期。 */
    void expireLink(TenantId tenant, UUID linkId) {
        tenantScope.run(tenant, () -> jdbc.sql("""
                        UPDATE delivery_link SET expires_at = now() - interval '1 minute' WHERE id = :id
                        """)
                .param("id", linkId)
                .update());
    }

    long billedUnits(TenantId tenant, UUID taskId) {
        return tenantScope.call(tenant, () -> jdbc.sql("SELECT billed_units FROM delivery_task WHERE id = :id")
                .param("id", taskId)
                .query(Long.class)
                .single());
    }
}
