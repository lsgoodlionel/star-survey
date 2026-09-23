package cn.mjy.platform.delivery;

import cn.mjy.platform.delivery.DeliveryTaskRepository.TaskRow;
import cn.mjy.platform.delivery.RecipientRepository.RecipientRow;
import cn.mjy.platform.shared.TenantId;
import java.time.Clock;
import java.time.Instant;
import java.util.Map;
import java.util.TreeMap;
import org.springframework.stereotype.Component;

/**
 * 把"任务模板 + 收件人"拼成一条待发送的消息。须在租户作用域内调用。
 *
 * <p>每位收件人拿到的是<b>专属</b>链接：任务链接上的渠道参数、收件人自己的预填参数、
 * 应答者标识 {@code rk}、以及邀请码 {@code token}（若有）一起重新签名。
 * 收件人改掉其中任何一项（例如把别人的 rk 填进来）都会让签名不成立。
 *
 * <p>幂等键固定为 {@code <任务>:<收件人>}：与发送台账里的键一致，重发时不会变。
 */
@Component
class MessageAssembler {

    private final DeliveryLinkService links;
    private final UnsubscribeTokens unsubscribeTokens;
    private final DeliveryProperties properties;
    private final Clock clock;

    MessageAssembler(DeliveryLinkService links, UnsubscribeTokens unsubscribeTokens,
            DeliveryProperties properties) {
        this.links = links;
        this.unsubscribeTokens = unsubscribeTokens;
        this.properties = properties;
        this.clock = Clock.systemUTC();
    }

    /**
     * @param idempotencyKey 来自发送台账的键（崩溃恢复时就是当初那一个），原样交给渠道商
     */
    DeliveryMessage build(TenantId tenant, TaskRow task, RecipientRow recipient, String idempotencyKey) {
        Map<String, String> base = new TreeMap<>();
        if (task.linkId() != null) {
            links.rowInScope(task.linkId()).ifPresent(link -> base.putAll(link.signedParams()));
        }
        base.putAll(recipient.params());
        Map<String, String> extra = new TreeMap<>();
        extra.put(DeliveryTaskService.RESPONDENT_KEY_PARAM, recipient.respondentKey());
        if (recipient.engineToken() != null && !recipient.engineToken().isBlank()) {
            extra.put(DeliveryTaskService.TOKEN_PARAM, recipient.engineToken());
        }
        Instant expiresAt = clock.instant().plus(properties.linkTtl());
        Map<String, String> signed = links.signRecipientParams(tenant, task.surveyId(), base, extra, expiresAt);
        String url = links.respondentUrl(tenant, task.surveyId(), signed);
        String unsubscribeUrl = properties.requirePublicBaseUrl() + UnsubscribeService.PATH
                + unsubscribeTokens.issue(tenant, task.id(), recipient.id());
        String body = MessageRenderer.render(task.bodyTemplate(), recipient.displayName(), url, unsubscribeUrl);
        return new DeliveryMessage(task.channel(), recipient.address(), task.subject(), body, idempotencyKey);
    }

    /** 首次认领时写进台账的键。 */
    static String idempotencyKey(TaskRow task, RecipientRow recipient) {
        return task.id() + ":" + recipient.id();
    }
}
