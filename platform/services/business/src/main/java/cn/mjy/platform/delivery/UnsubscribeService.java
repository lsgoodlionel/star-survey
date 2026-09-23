package cn.mjy.platform.delivery;

import cn.mjy.platform.delivery.DeliveryTaskRepository.TaskRow;
import cn.mjy.platform.delivery.RecipientRepository.RecipientRow;
import cn.mjy.platform.delivery.UnsubscribeTokens.Target;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * 退订（R05-04）。收件人点退订链接即可，不需要登录——令牌本身就是凭证。
 *
 * <p>退订按 (租户, 渠道, 地址) 落在租户的退订名单上，<b>不可撤销</b>（表上没有 UPDATE/DELETE 权限）。
 * 之后同一租户的任何任务——首邀、催答、新答卷通知——在发送前都会查这张名单并跳过，
 * 因此"后续任务把退订又发回去"在数据层面就不可能发生。
 *
 * <p>令牌无效、任务或收件人不存在，一律回同一个 404：否则就能拿令牌去探测哪些任务存在。
 */
@Service
public class UnsubscribeService {

    /** 退订地址前缀；令牌直接跟在后面。 */
    public static final String PATH = "/d/u/";
    static final String SYSTEM_ACTOR = "system:delivery-unsubscribe";
    static final String REASON_SELF = "self";
    static final String REASON_COMPLAINT = "complaint";
    static final String REASON_BOUNCE = "bounce";

    private static final Logger log = LoggerFactory.getLogger(UnsubscribeService.class);

    private final TenantScope tenantScope;
    private final UnsubscribeTokens tokens;
    private final DeliveryTaskRepository tasks;
    private final RecipientRepository recipients;
    private final SuppressionRepository suppressions;
    private final DeliveryAudit audit;

    UnsubscribeService(TenantScope tenantScope, UnsubscribeTokens tokens, DeliveryTaskRepository tasks,
            RecipientRepository recipients, SuppressionRepository suppressions, DeliveryAudit audit) {
        this.tenantScope = tenantScope;
        this.tokens = tokens;
        this.tasks = tasks;
        this.recipients = recipients;
        this.suppressions = suppressions;
        this.audit = audit;
    }

    /** 退订结果；{@code alreadyUnsubscribed} 为真表示这次是重复点击（同样成功，不报错）。 */
    public record Result(String channel, boolean alreadyUnsubscribed) {
    }

    public Result unsubscribe(String token, String traceId) {
        Target target = tokens.parse(token)
                .orElseThrow(() -> DeliveryExceptions.notFound("unsubscribe link is not valid"));
        return tenantScope.call(target.tenant(), () -> {
            TaskRow task = tasks.find(target.taskId())
                    .orElseThrow(() -> DeliveryExceptions.notFound("unsubscribe link is not valid"));
            RecipientRow recipient = recipients.find(target.taskId(), target.recipientId())
                    .orElseThrow(() -> DeliveryExceptions.notFound("unsubscribe link is not valid"));
            boolean added = suppress(target.tenant(), task.channel(), recipient.address(), REASON_SELF,
                    task.id(), traceId);
            if (recipient.state().isPending()) {
                recipients.markOutcome(task.id(), recipient.id(), RecipientState.UNSUBSCRIBED, null, null);
                tasks.countOutcome(task.id(), RecipientState.UNSUBSCRIBED);
            }
            return new Result(task.channel().code(), !added);
        });
    }

    /**
     * 登记退订。须在租户作用域内调用；返回是否是新登记。
     * 日志只记渠道与来源任务，<b>不记地址</b>。
     */
    boolean suppress(TenantId tenant, DeliveryChannel channel, String address, String reason, UUID taskId,
            String traceId) {
        boolean added = suppressions.add(tenant, channel, address, reason, taskId);
        if (added) {
            audit.record(tenant, SYSTEM_ACTOR, traceId, DeliveryAudit.UNSUBSCRIBE,
                    "channel=" + channel.code() + " reason=" + reason + " task/" + taskId);
            log.info("delivery unsubscribe recorded: tenant={} channel={} reason={}", tenant, channel.code(), reason);
        }
        return added;
    }

    /** 该地址是否已退订。须在租户作用域内调用。 */
    boolean isSuppressed(DeliveryChannel channel, String address) {
        return suppressions.isSuppressed(channel, address);
    }
}
