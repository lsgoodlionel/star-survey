package cn.mjy.platform.delivery;

import cn.mjy.platform.delivery.DeliveryTaskRepository.TaskRow;
import cn.mjy.platform.delivery.RecipientRepository.RecipientRow;
import cn.mjy.platform.delivery.ReminderRuleRepository.RuleRow;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * 催答（WP-05 切片 05.3，R05-06）。
 *
 * <h2>"完成与催答竞态不重复提醒"是怎么保证的</h2>
 * 一次催答要真正发出去，必须连过两关，两关都查同一张完成记录表：
 * <ol>
 *   <li><b>生成关</b>（本类）：对每个候选人<b>单独开一个事务</b>，先 {@code SELECT … FOR UPDATE} 锁住首邀任务里
 *       这个人的收件人行，然后在<b>同一个事务、同一把锁</b>下依次做：查完成记录 → 查退订名单 → 催答次数加一
 *       → 把这个人写进本轮的催答任务。四步同成同败。于是：
 *       <ul>
 *         <li>完成在本事务的快照之前提交 → 查得到 → 不生成催答；</li>
 *         <li>完成在本事务提交之后才发生 → 催答已经生成，但还要过第二关；</li>
 *         <li>两个副本同时处理同一个人 → 行锁把它们串起来。<b>候选名单是锁外的粗筛</b>，可能已经陈旧，
 *             所以锁内要把次数上限与等待间隔<b>都</b>按刚读到的行重算一遍（{@code isDue}）；
 *             第二个副本据此看到"刚被催过、还没到下一次"，不会生成第二条。
 *             催答次数与"生成了一条催答"在同一事务里，不会出现"加了次数没发"或"发了没加"。</li>
 *       </ul></li>
 *   <li><b>发送关</b>（{@link DeliveryWorker}）：认领时在收件人行锁下再查一次完成记录，
 *       提交后、把消息交给渠道商之前<b>又查一次</b>。任一处查到已完成就标记跳过，不发。</li>
 * </ol>
 * 剩下的窗口只有"消息已经交给渠道商之后，完成才提交"——此时消息已经离开平台，
 * 没有任何分布式事务能把它追回来。这是这类系统能做到的上界，也是唯一剩下的重复提醒场景。
 *
 * <p>催答次数上限按人计（{@code delivery_recipient.reminders_sent}），与生成在同一事务里加一，
 * 因此崩溃重跑不会把某个人多催一次。
 */
@Service
public class ReminderService {

    /** 每个规则每轮最多生成多少条催答。 */
    private static final int PAGE = 200;
    static final String SYSTEM_ACTOR = "system:delivery-reminder";

    private static final Logger log = LoggerFactory.getLogger(ReminderService.class);

    private final TenantScope tenantScope;
    private final ReminderRuleRepository rules;
    private final DeliveryTaskRepository tasks;
    private final RecipientRepository recipients;
    private final CompletionRepository completions;
    private final UnsubscribeService unsubscribes;
    private final DeliveryAccess access;
    private final DeliveryAudit audit;
    private final Clock clock;

    ReminderService(TenantScope tenantScope, ReminderRuleRepository rules, DeliveryTaskRepository tasks,
            RecipientRepository recipients, CompletionRepository completions, UnsubscribeService unsubscribes,
            DeliveryAccess access, DeliveryAudit audit) {
        this.tenantScope = tenantScope;
        this.rules = rules;
        this.tasks = tasks;
        this.recipients = recipients;
        this.completions = completions;
        this.unsubscribes = unsubscribes;
        this.access = access;
        this.audit = audit;
        this.clock = Clock.systemUTC();
    }

    /** 建规则的请求。 */
    public record NewRule(
            UUID sourceTaskId,
            Duration firstDelay,
            Duration interval,
            int maxReminders,
            String subject,
            String bodyTemplate) {
    }

    public ReminderRuleView create(TenantContext ctx, UUID surveyId, NewRule request) {
        access.require(ctx, DeliveryAccess.SEND, surveyId);
        validate(request);
        return tenantScope.call(ctx.tenantId(), () -> {
            TaskRow source = tasks.find(request.sourceTaskId())
                    .orElseThrow(() -> DeliveryExceptions.notFound(
                            "delivery task not found: " + request.sourceTaskId()));
            if (!source.surveyId().equals(surveyId)) {
                throw DeliveryExceptions.invalid("the source task belongs to another survey");
            }
            if (source.kind() != TaskKind.INVITE) {
                throw DeliveryExceptions.invalid("reminders can only follow an invitation task");
            }
            if (source.channel() == DeliveryChannel.EMAIL
                    && !request.bodyTemplate().contains(MessageRenderer.UNSUBSCRIBE)) {
                throw DeliveryExceptions.invalid("email bodyTemplate must contain " + MessageRenderer.UNSUBSCRIBE);
            }
            if (rules.findBySourceTask(request.sourceTaskId()).isPresent()) {
                throw DeliveryExceptions.conflict("reminder_rule_exists",
                        "this invitation task already has a reminder rule");
            }
            UUID id = UUID.randomUUID();
            rules.insert(ctx.tenantId(), id, surveyId, request.sourceTaskId(),
                    (int) request.firstDelay().toSeconds(), (int) request.interval().toSeconds(),
                    request.maxReminders(), request.subject(), request.bodyTemplate(), ctx.actorId());
            audit.record(ctx, DeliveryAudit.REMINDER_RULE,
                    "rule/" + id + " survey/" + surveyId + " source/" + request.sourceTaskId()
                            + " max=" + request.maxReminders());
            return ReminderRuleView.of(rules.find(id).orElseThrow());
        });
    }

    public List<ReminderRuleView> list(TenantContext ctx, UUID surveyId) {
        access.require(ctx, DeliveryAccess.READ, surveyId);
        return tenantScope.call(ctx.tenantId(),
                () -> rules.listBySurvey(surveyId).stream().map(ReminderRuleView::of).toList());
    }

    public ReminderRuleView setEnabled(TenantContext ctx, UUID ruleId, boolean enabled) {
        return tenantScope.call(ctx.tenantId(), () -> {
            RuleRow rule = rules.find(ruleId)
                    .orElseThrow(() -> DeliveryExceptions.notFound("reminder rule not found: " + ruleId));
            access.require(ctx, DeliveryAccess.SEND, rule.surveyId());
            rules.setEnabled(ruleId, enabled);
            audit.record(ctx, DeliveryAudit.REMINDER_RULE, "rule/" + ruleId + " enabled=" + enabled);
            return ReminderRuleView.of(rules.find(ruleId).orElseThrow());
        });
    }

    /** 跑一轮：为该租户所有启用中的规则生成催答任务；返回新生成的催答条数。 */
    public int runOnce(TenantId tenant) {
        List<RuleRow> enabled = tenantScope.call(tenant, rules::enabled);
        int generated = 0;
        for (RuleRow rule : enabled) {
            try {
                generated += generate(tenant, rule);
            } catch (RuntimeException e) {
                log.error("reminder generation failed for rule {} of tenant {}", rule.id(), tenant, e);
            }
        }
        return generated;
    }

    private int generate(TenantId tenant, RuleRow rule) {
        List<UUID> candidates = tenantScope.call(tenant, () -> recipients.reminderCandidates(
                rule.sourceTaskId(), rule.maxReminders(), rule.firstDelaySeconds(), rule.intervalSeconds(), PAGE));
        if (candidates.isEmpty()) {
            return 0;
        }
        TaskRow source = tenantScope.call(tenant, () -> tasks.find(rule.sourceTaskId()).orElseThrow());
        UUID reminderTaskId = createReminderTask(tenant, rule, source);
        int generated = 0;
        for (UUID candidate : candidates) {
            if (Boolean.TRUE.equals(tenantScope.call(tenant,
                    () -> enqueue(tenant, rule, source, reminderTaskId, candidate)))) {
                generated++;
            }
        }
        int queued = generated;
        tenantScope.run(tenant, () -> {
            tasks.setQueuedCount(reminderTaskId, queued);
            if (queued == 0) {
                // 一个人都没留下（全部已完成或已退订）：任务当场收口，不留一个空任务在在途队列里。
                tasks.finish(reminderTaskId, TaskStatus.COMPLETED, null);
            }
        });
        return generated;
    }

    private UUID createReminderTask(TenantId tenant, RuleRow rule, TaskRow source) {
        UUID id = UUID.randomUUID();
        tenantScope.run(tenant, () -> {
            tasks.insert(tenant, id, rule.surveyId(), source.linkId(), source.channel(), TaskKind.REMINDER,
                    rule.subject(), rule.bodyTemplate(), rule.sourceTaskId(), null, SYSTEM_ACTOR);
            audit.record(tenant, SYSTEM_ACTOR, "reminder-" + id, DeliveryAudit.REMINDER_RULE,
                    "task/" + id + " rule/" + rule.id() + " source/" + rule.sourceTaskId());
        });
        return id;
    }

    /**
     * 一个候选人：事务内、持首邀收件人行锁。完成判定、催答计数、写入催答名单三件事同成同败。
     *
     * @return 是否真的生成了一条催答
     */
    private boolean enqueue(TenantId tenant, RuleRow rule, TaskRow source, UUID reminderTaskId, UUID candidate) {
        RecipientRow recipient = recipients.lock(source.id(), candidate).orElse(null);
        if (recipient == null || recipient.state() != RecipientState.SENT) {
            return false;
        }
        if (recipient.remindersSent() >= rule.maxReminders()) {
            return false;
        }
        // 候选查询是锁外的粗筛，读到的可能是别人已经催过之后的陈旧快照：等待条件必须拿锁内的行重算一遍，
        // 否则两个副本各拿一份陈旧候选名单，就会把同一个人在同一个间隔里催两次。
        if (!isDue(rule, recipient)) {
            return false;
        }
        if (completions.isCompleted(rule.surveyId(), recipient.respondentKey())) {
            return false;
        }
        if (unsubscribes.isSuppressed(source.channel(), recipient.address())) {
            return false;
        }
        recipients.recordReminder(source.id(), candidate);
        return recipients.insert(tenant, reminderTaskId, UUID.randomUUID(), recipient.address(),
                recipient.displayName(), recipient.engineToken(), recipient.respondentKey(), recipient.params());
    }

    /** 与 {@code RecipientRepository.reminderCandidates} 同一个公式，只是这次用的是锁内读到的行。 */
    private boolean isDue(RuleRow rule, RecipientRow recipient) {
        Instant reference = recipient.remindersSent() == 0
                ? Optional.ofNullable(recipient.sentAt()).map(OffsetDateTime::toInstant).orElse(null)
                : Optional.ofNullable(recipient.lastReminderAt()).map(OffsetDateTime::toInstant).orElse(null);
        if (reference == null) {
            return false;
        }
        long wait = recipient.remindersSent() == 0 ? rule.firstDelaySeconds() : rule.intervalSeconds();
        return !reference.plusSeconds(wait).isAfter(Instant.now(clock));
    }

    private static void validate(NewRule request) {
        if (request.sourceTaskId() == null) {
            throw DeliveryExceptions.invalid("sourceTaskId is required");
        }
        if (request.firstDelay() == null || request.firstDelay().isNegative()) {
            throw DeliveryExceptions.invalid("firstDelay must not be negative");
        }
        if (request.interval() == null || request.interval().isZero() || request.interval().isNegative()) {
            throw DeliveryExceptions.invalid("interval must be positive");
        }
        if (request.maxReminders() < 1 || request.maxReminders() > 10) {
            throw DeliveryExceptions.invalid("maxReminders must be between 1 and 10");
        }
        if (request.bodyTemplate() == null || !request.bodyTemplate().contains(MessageRenderer.LINK)) {
            throw DeliveryExceptions.invalid("bodyTemplate must contain " + MessageRenderer.LINK);
        }
    }
}
