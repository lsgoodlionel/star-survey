package cn.mjy.platform.delivery;

import cn.mjy.platform.delivery.DeliveryTaskRepository.TaskRow;
import cn.mjy.platform.delivery.RecipientRepository.RecipientRow;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantDirectory;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.util.EnumMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * 投放任务的推进。一轮里依次：发送在途任务 → 对账完成情况 → 生成催答 → 生成新答卷通知。
 *
 * <p><b>跨租户枚举</b>：与 {@code PublishReconciler} 同法——经 {@link TenantDirectory} 只拿租户标识名单，
 * 逐个进入 {@link TenantScope} 查本租户的在途任务，行级安全照常生效。
 *
 * <p><b>不重复发送</b>（三道保障）：
 * <ol>
 *   <li>认领在事务里持收件人行锁（FOR UPDATE SKIP LOCKED），只有 queued/sending 的行会被认领，
 *       多副本不会同时认领同一行；</li>
 *   <li>认领时往发送台账写一行 (任务, 收件人)，幂等键固定为 {@code 任务:收件人}，此后重发一律取台账里那一个；</li>
 *   <li>认领与实际发送是<b>两个事务</b>。进程在中间死掉时收件人停在 sending，
 *       <b>租约</b>（{@code platform.delivery.send-lease}）过期后才会被重新认领——这样"另一个副本正在发"
 *       与"那个副本死了"才区分得开。重发用<b>同一个幂等键</b>，渠道商据此只投递一次；
 *       台账不可删除，所以"发过"这件事不会丢。</li>
 * </ol>
 * 第三条把"恰好一次"的责任交给渠道商的幂等机制——没有分布式事务能跨越平台与邮件服务器，
 * 这是能做到的最强保证，也是 {@link DeliveryProvider} 把幂等写进接口契约的原因。
 */
@Component
public class DeliveryWorker {

    /** 一次从库里取多少个待发收件人。 */
    private static final int CHUNK = 50;
    static final String SYSTEM_ACTOR = "system:delivery-worker";

    private static final Logger log = LoggerFactory.getLogger(DeliveryWorker.class);

    /** 一个收件人本轮的结果。 */
    public enum Outcome { SENT, FAILED, SKIPPED, RATE_LIMITED, BUSY }

    /** 认领结果：message 非空表示已认领、要调用渠道商；否则 outcome 说明为何不发。 */
    private record Claim(Outcome outcome, RecipientRow recipient, DeliveryMessage message) {

        static Claim not(Outcome outcome) {
            return new Claim(outcome, null, null);
        }
    }

    private final TenantDirectory tenants;
    private final TenantScope tenantScope;
    private final DeliveryTaskRepository tasks;
    private final RecipientRepository recipients;
    private final DeliveryProviders providers;
    private final MessageAssembler assembler;
    private final RateLimiter rateLimiter;
    private final UnsubscribeService unsubscribes;
    private final CompletionRepository completions;
    private final CompletionReconciler reconciler;
    private final ReminderService reminders;
    private final NotificationService notifications;
    private final DeliveryAudit audit;
    private final DeliveryProperties properties;

    DeliveryWorker(TenantDirectory tenants, TenantScope tenantScope, DeliveryTaskRepository tasks,
            RecipientRepository recipients, DeliveryProviders providers, MessageAssembler assembler,
            RateLimiter rateLimiter, UnsubscribeService unsubscribes, CompletionRepository completions,
            CompletionReconciler reconciler, ReminderService reminders, NotificationService notifications,
            DeliveryAudit audit, DeliveryProperties properties) {
        this.tenants = tenants;
        this.tenantScope = tenantScope;
        this.tasks = tasks;
        this.recipients = recipients;
        this.providers = providers;
        this.assembler = assembler;
        this.rateLimiter = rateLimiter;
        this.unsubscribes = unsubscribes;
        this.completions = completions;
        this.reconciler = reconciler;
        this.reminders = reminders;
        this.notifications = notifications;
        this.audit = audit;
        this.properties = properties;
    }

    /** 跑一轮：跨所有未关闭租户合计最多处理 batchSize 个收件人。单个租户出错不影响其余。 */
    public Map<Outcome, Integer> runOnce() {
        Map<Outcome, Integer> tally = new EnumMap<>(Outcome.class);
        int budget = properties.batchSize();
        for (TenantId tenant : tenants.openTenants()) {
            try {
                reconciler.runOnce(tenant);
                reminders.runOnce(tenant);
                notifications.runOnce(tenant);
                budget -= sendAll(tenant, tally, budget);
            } catch (RuntimeException e) {
                log.error("delivery round failed for tenant {}", tenant, e);
                tally.merge(Outcome.FAILED, 0, Integer::sum);
            }
            if (budget <= 0) {
                break;
            }
        }
        if (!tally.isEmpty()) {
            log.info("delivery round finished: {}", tally);
        }
        return tally;
    }

    private int sendAll(TenantId tenant, Map<Outcome, Integer> tally, int budget) {
        int used = 0;
        for (UUID taskId : dueTasks(tenant, budget)) {
            if (used >= budget) {
                break;
            }
            used += sendPending(tenant, taskId, budget - used, tally);
        }
        return used;
    }

    /** 推进一个任务，最多处理 budget 个收件人；返回实际处理的个数。测试直接调用本方法。 */
    public int sendPending(TenantId tenant, UUID taskId, int budget) {
        return sendPending(tenant, taskId, budget, new EnumMap<>(Outcome.class));
    }

    private int sendPending(TenantId tenant, UUID taskId, int budget, Map<Outcome, Integer> tally) {
        TaskRow task = tenantScope.call(tenant, () -> tasks.find(taskId)).orElse(null);
        if (task == null || !task.status().isOpen()) {
            return 0;
        }
        Optional<DeliveryProvider> provider = providers.find(task.channel());
        if (provider.isEmpty()) {
            tenantScope.run(tenant, () -> tasks.finish(taskId, TaskStatus.FAILED, "provider_not_configured"));
            return 0;
        }
        if (Boolean.FALSE.equals(tenantScope.call(tenant, () -> tasks.markRunning(taskId)))) {
            return 0;
        }
        int done = 0;
        boolean limited = false;
        while (done < budget && !limited) {
            int room = Math.min(budget - done, CHUNK);
            List<UUID> batch = tenantScope.call(tenant,
                    () -> recipients.pending(taskId, room, properties.sendLease().toSeconds()));
            if (batch.isEmpty()) {
                break;
            }
            for (UUID recipientId : batch) {
                if (done >= budget) {
                    break;
                }
                Outcome outcome = deliver(tenant, task, provider.get(), recipientId);
                tally.merge(outcome, 1, Integer::sum);
                done++;
                if (outcome == Outcome.RATE_LIMITED) {
                    limited = true;
                    break;
                }
            }
        }
        settleTask(tenant, taskId, limited);
        return done;
    }

    private List<UUID> dueTasks(TenantId tenant, int limit) {
        try {
            return tenantScope.call(tenant, () -> tasks.dueTasks(limit));
        } catch (RuntimeException e) {
            log.error("could not scan delivery tasks of tenant {}", tenant, e);
            return List.of();
        }
    }

    private void settleTask(TenantId tenant, UUID taskId, boolean limited) {
        tenantScope.run(tenant, () -> {
            if (!limited && recipients.countPending(taskId) == 0) {
                tasks.finish(taskId, TaskStatus.COMPLETED, null);
            } else {
                tasks.reschedule(taskId, properties.backoff());
            }
        });
    }

    /** 一个收件人：认领（事务）→ 发送前复核（事务）→ 调渠道商（不在事务里）→ 收尾（事务）。 */
    private Outcome deliver(TenantId tenant, TaskRow task, DeliveryProvider provider, UUID recipientId) {
        Claim claim = tenantScope.call(tenant, () -> claim(tenant, task, recipientId));
        if (claim.message() == null) {
            return claim.outcome();
        }
        // 发送前再查一次：认领事务提交之后、消息真正交出去之前，完成或退订可能刚刚落库。
        if (Boolean.TRUE.equals(tenantScope.call(tenant, () -> shouldSkip(task, claim.recipient())))) {
            tenantScope.run(tenant, () -> skip(task, claim.recipient()));
            return Outcome.SKIPPED;
        }
        SendResult result;
        try {
            result = provider.send(claim.message());
        } catch (RuntimeException e) {
            // 结果未知：收件人停在 sending，下一轮用同一个幂等键重发，渠道商据此只投递一次。
            log.error("delivery provider {} failed for task {} recipient {}: {}",
                    provider.name(), task.id(), recipientId, Redaction.error(e));
            throw e;
        }
        return tenantScope.call(tenant, () -> settle(tenant, task, provider, claim.recipient(), result));
    }

    /** 事务内、持收件人行锁。 */
    private Claim claim(TenantId tenant, TaskRow task, UUID recipientId) {
        RecipientRow recipient = recipients.lock(task.id(), recipientId).orElse(null);
        if (recipient == null || !recipient.state().isPending()) {
            return Claim.not(Outcome.BUSY);
        }
        if (shouldSkip(task, recipient)) {
            skip(task, recipient);
            return Claim.not(Outcome.SKIPPED);
        }
        if (recipient.attempts() >= properties.maxAttempts()) {
            recipients.markOutcome(task.id(), recipient.id(), RecipientState.FAILED, null, "max_attempts");
            tasks.countOutcome(task.id(), RecipientState.FAILED);
            return Claim.not(Outcome.FAILED);
        }
        if (!rateLimiter.tryAcquire(tenant, task.channel())) {
            return Claim.not(Outcome.RATE_LIMITED);
        }
        recipients.markSending(task.id(), recipient.id());
        // 台账里已有的键优先：上一次崩在发送途中时，重发必须用当时那个键，而不是现在重新算出来的。
        String key = recipients.claimSend(tenant, task.id(), recipient.id(),
                MessageAssembler.idempotencyKey(task, recipient));
        return new Claim(Outcome.SENT, recipient, assembler.build(tenant, task, recipient, key));
    }

    /** 退订一律跳过；催答另外查完成记录——已完成的人不再被催（验收"完成与催答竞态不重复提醒"）。 */
    private boolean shouldSkip(TaskRow task, RecipientRow recipient) {
        if (unsubscribes.isSuppressed(task.channel(), recipient.address())) {
            return true;
        }
        return task.kind() == TaskKind.REMINDER
                && completions.isCompleted(task.surveyId(), recipient.respondentKey());
    }

    private void skip(TaskRow task, RecipientRow recipient) {
        RecipientState state = unsubscribes.isSuppressed(task.channel(), recipient.address())
                ? RecipientState.UNSUBSCRIBED
                : RecipientState.SKIPPED;
        if (recipients.find(task.id(), recipient.id()).map(row -> row.state().isPending()).orElse(false)) {
            recipients.markOutcome(task.id(), recipient.id(), state, null, null);
            tasks.countOutcome(task.id(), state);
        }
    }

    private Outcome settle(TenantId tenant, TaskRow task, DeliveryProvider provider, RecipientRow recipient,
            SendResult result) {
        return switch (result.status()) {
            case ACCEPTED -> {
                recipients.markOutcome(task.id(), recipient.id(), RecipientState.SENT,
                        result.providerMessageId(), null);
                recipients.completeSend(task.id(), recipient.id(), provider.name(), result.providerMessageId());
                tasks.countOutcome(task.id(), RecipientState.SENT);
                audit.record(tenant, SYSTEM_ACTOR, "delivery-" + task.id(),
                        task.kind() == TaskKind.REMINDER ? DeliveryAudit.REMINDER_SEND : DeliveryAudit.SEND,
                        DeliveryAudit.describeSend(task.id(), recipient.id(), task.channel(), "accepted"));
                yield Outcome.SENT;
            }
            case REJECTED -> {
                recipients.markOutcome(task.id(), recipient.id(), RecipientState.FAILED, null, result.errorCode());
                tasks.countOutcome(task.id(), RecipientState.FAILED);
                audit.record(tenant, SYSTEM_ACTOR, "delivery-" + task.id(), DeliveryAudit.SEND,
                        DeliveryAudit.describeSend(task.id(), recipient.id(), task.channel(),
                                "rejected:" + result.errorCode()));
                yield Outcome.FAILED;
            }
            case RETRY -> {
                if (recipient.attempts() + 1 >= properties.maxAttempts()) {
                    recipients.markOutcome(task.id(), recipient.id(), RecipientState.FAILED, null,
                            result.errorCode());
                    tasks.countOutcome(task.id(), RecipientState.FAILED);
                    yield Outcome.FAILED;
                }
                recipients.markRetryable(task.id(), recipient.id(), result.errorCode());
                yield Outcome.BUSY;
            }
        };
    }
}
