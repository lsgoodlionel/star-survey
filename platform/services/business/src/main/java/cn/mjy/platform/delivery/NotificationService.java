package cn.mjy.platform.delivery;

import cn.mjy.platform.delivery.NotificationRuleRepository.RuleRow;
import cn.mjy.platform.engine.ResponseProjectionQuery;
import cn.mjy.platform.engine.ResponseState;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.tenant.routing.SurveyRoute;
import cn.mjy.platform.tenant.routing.SurveyRouteService;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * 新答卷通知（R05-09）。
 *
 * <p><b>不逐份轰炸</b>：规则带最小间隔，一轮里把"自上次通知以来新增的份数"合并成一条
 * "新增 N 份答卷"。判断依据是引擎投影里该问卷的已完成答卷总数与上次通知时记下的数的差值——
 * 每份答卷最多被算进一条通知，重复的引擎事件不会重复触发（投影本身按答卷自然键去重）。
 *
 * <p><b>字段最小化且受收件人权限限制</b>：通知正文只有问卷标识与新增份数，没有任何答案内容；
 * 发送前按 access 重新判定收件成员此刻是否仍能查看该问卷的统计或原始答卷，不能则本轮跳过、
 * 也不推进计数（等他重新拿到权限时仍能看到累计增量）。
 *
 * <p>规则行在生成通知时加锁，因此多副本不会为同一条规则各发一次。
 */
@Service
public class NotificationService {

    static final String SYSTEM_ACTOR = "system:delivery-notification";
    /** 通知正文模板：只有问卷标识与新增份数。 */
    static final String BODY_TEMPLATE = "问卷 %s 新增 %d 份答卷（累计 %d 份）。";
    private static final int MIN_INTERVAL_SECONDS = 60;
    private static final int MAX_INTERVAL_SECONDS = 86_400;

    private static final Logger log = LoggerFactory.getLogger(NotificationService.class);

    private final TenantScope tenantScope;
    private final NotificationRuleRepository rules;
    private final DeliveryTaskRepository tasks;
    private final RecipientRepository recipients;
    private final SurveyRouteService routes;
    private final ResponseProjectionQuery projections;
    private final DeliveryAccess access;
    private final DeliveryAudit audit;

    NotificationService(TenantScope tenantScope, NotificationRuleRepository rules, DeliveryTaskRepository tasks,
            RecipientRepository recipients, SurveyRouteService routes, ResponseProjectionQuery projections,
            DeliveryAccess access, DeliveryAudit audit) {
        this.tenantScope = tenantScope;
        this.rules = rules;
        this.tasks = tasks;
        this.recipients = recipients;
        this.routes = routes;
        this.projections = projections;
        this.access = access;
        this.audit = audit;
    }

    /** 建规则的请求。 */
    public record NewRule(String actorId, DeliveryChannel channel, String address, Duration minInterval) {
    }

    public NotificationRuleView create(TenantContext ctx, UUID surveyId, NewRule request) {
        access.require(ctx, DeliveryAccess.SEND, surveyId);
        validate(request);
        String address = Addresses.normalise(request.channel(), request.address(), 1);
        return tenantScope.call(ctx.tenantId(), () -> {
            UUID id = UUID.randomUUID();
            if (!rules.insert(ctx.tenantId(), id, surveyId, request.actorId(), request.channel(), address,
                    (int) request.minInterval().toSeconds(), ctx.actorId())) {
                throw DeliveryExceptions.conflict("notification_rule_exists",
                        "this member already has a notification rule on that channel");
            }
            audit.record(ctx, DeliveryAudit.NOTIFICATION_RULE,
                    "rule/" + id + " survey/" + surveyId + " member=" + request.actorId()
                            + " channel=" + request.channel().code());
            return NotificationRuleView.of(rules.find(id).orElseThrow());
        });
    }

    public List<NotificationRuleView> list(TenantContext ctx, UUID surveyId) {
        access.require(ctx, DeliveryAccess.READ, surveyId);
        return tenantScope.call(ctx.tenantId(),
                () -> rules.listBySurvey(surveyId).stream().map(NotificationRuleView::of).toList());
    }

    public NotificationRuleView setEnabled(TenantContext ctx, UUID ruleId, boolean enabled) {
        return tenantScope.call(ctx.tenantId(), () -> {
            RuleRow rule = rules.find(ruleId)
                    .orElseThrow(() -> DeliveryExceptions.notFound("notification rule not found: " + ruleId));
            access.require(ctx, DeliveryAccess.SEND, rule.surveyId());
            rules.setEnabled(ruleId, enabled);
            audit.record(ctx, DeliveryAudit.NOTIFICATION_RULE, "rule/" + ruleId + " enabled=" + enabled);
            return NotificationRuleView.of(rules.find(ruleId).orElseThrow());
        });
    }

    /** 跑一轮：为该租户所有到期规则生成通知任务；返回生成的通知条数。 */
    public int runOnce(TenantId tenant) {
        List<UUID> due = tenantScope.call(tenant, rules::due);
        int generated = 0;
        for (UUID ruleId : due) {
            try {
                generated += notify(tenant, ruleId);
            } catch (RuntimeException e) {
                log.error("new-response notification failed for rule {} of tenant {}", ruleId, tenant, e);
            }
        }
        return generated;
    }

    private int notify(TenantId tenant, UUID ruleId) {
        RuleRow rule = tenantScope.call(tenant, () -> rules.find(ruleId)).orElse(null);
        if (rule == null || !rule.enabled()) {
            return 0;
        }
        long completed = completedCount(tenant, rule.surveyId());
        if (completed <= rule.notifiedCompletedCount()) {
            return 0;
        }
        TenantContext recipient = new TenantContext(tenant, rule.actorId(), List.of(),
                "notify-" + UUID.randomUUID());
        if (!access.canStillSee(recipient, rule.surveyId())) {
            log.info("skipping new-response notification: member {} can no longer view survey {}",
                    rule.actorId(), rule.surveyId());
            return 0;
        }
        return Boolean.TRUE.equals(tenantScope.call(tenant, () -> enqueue(tenant, ruleId, completed))) ? 1 : 0;
    }

    /** 事务内、持规则行锁：计数推进与通知任务的生成同成同败，因此同一批新增只会通知一次。 */
    private boolean enqueue(TenantId tenant, UUID ruleId, long completed) {
        RuleRow rule = rules.lock(ruleId).orElse(null);
        if (rule == null || !rule.enabled() || completed <= rule.notifiedCompletedCount()) {
            return false;
        }
        long added = completed - rule.notifiedCompletedCount();
        UUID taskId = UUID.randomUUID();
        tasks.insert(tenant, taskId, rule.surveyId(), null, rule.channel(), TaskKind.NOTIFICATION,
                "问卷有新答卷", BODY_TEMPLATE.formatted(rule.surveyId(), added, completed), null, null,
                SYSTEM_ACTOR);
        recipients.insert(tenant, taskId, UUID.randomUUID(), rule.address(), null, null,
                DeliveryTaskService.randomRespondentKey(), Map.of());
        tasks.setQueuedCount(taskId, 1);
        rules.markNotified(ruleId, completed);
        audit.record(tenant, SYSTEM_ACTOR, "notify-" + taskId, DeliveryAudit.NOTIFICATION_RULE,
                "task/" + taskId + " rule/" + ruleId + " added=" + added);
        return true;
    }

    private long completedCount(TenantId tenant, UUID surveyId) {
        Optional<SurveyRoute> route = routes.findByPublicId(tenant, surveyId);
        if (route.isEmpty()) {
            return 0;
        }
        return tenantScope.call(tenant, () -> projections
                .countByState(route.get().engineInstanceId(), route.get().engineSid())
                .getOrDefault(ResponseState.ENGINE_COMPLETED, 0L));
    }

    private static void validate(NewRule request) {
        if (request.actorId() == null || request.actorId().isBlank() || request.actorId().length() > 256) {
            throw DeliveryExceptions.invalid("actorId must be 1 to 256 characters");
        }
        if (request.minInterval() == null
                || request.minInterval().toSeconds() < MIN_INTERVAL_SECONDS
                || request.minInterval().toSeconds() > MAX_INTERVAL_SECONDS) {
            throw DeliveryExceptions.invalid("minInterval must be between " + MIN_INTERVAL_SECONDS
                    + " and " + MAX_INTERVAL_SECONDS + " seconds");
        }
    }
}
