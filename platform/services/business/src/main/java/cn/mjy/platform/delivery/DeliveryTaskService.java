package cn.mjy.platform.delivery;

import cn.mjy.platform.delivery.DeliveryTaskRepository.TaskRow;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.security.SecureRandom;
import java.util.HexFormat;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.stereotype.Service;

/**
 * 批量投放任务（R05-04、R05-05、R05-06）：一条渠道、一份名单、一次性建好，之后由
 * {@link DeliveryWorker} 按节奏推进。
 *
 * <p>建任务只写库，不发送：任务与名单先落库，进程随时可以死，重启后从收件人状态接着做。
 *
 * <p>每个收件人有自己的应答者标识 {@code respondentKey}——有邀请码（引擎参与者 token）时就用邀请码，
 * 否则生成一个不可预测的随机键。它会作为签名参数 {@code rk} 出现在专属链接里，因此改不了、猜不到；
 * 催答与完成判定都按它进行（ADR 0016：token 是唯一可靠的身份）。
 */
@Service
public class DeliveryTaskService {

    /** 单次提交的名单上限：更大的名单应当拆成多个任务，否则一个事务会拖太久。 */
    public static final int MAX_RECIPIENTS = 5_000;
    static final String RESPONDENT_KEY_PARAM = "rk";
    static final String TOKEN_PARAM = "token";

    private static final SecureRandom RANDOM = new SecureRandom();

    private final TenantScope tenantScope;
    private final DeliveryTaskRepository tasks;
    private final RecipientRepository recipients;
    private final DeliveryAccess access;
    private final DeliveryAudit audit;
    private final DeliveryLinkService links;
    private final DeliveryProviders providers;

    DeliveryTaskService(TenantScope tenantScope, DeliveryTaskRepository tasks, RecipientRepository recipients,
            DeliveryAccess access, DeliveryAudit audit, DeliveryLinkService links, DeliveryProviders providers) {
        this.tenantScope = tenantScope;
        this.tasks = tasks;
        this.recipients = recipients;
        this.access = access;
        this.audit = audit;
        this.links = links;
        this.providers = providers;
    }

    /** 名单里的一行。{@code engineToken} 是发布后从引擎回读到的参与者 token（邀请码），可为空。 */
    public record NewRecipient(String address, String displayName, String engineToken, Map<String, String> params) {

        public NewRecipient {
            params = params == null ? Map.of() : Map.copyOf(params);
        }

        public static NewRecipient of(String address) {
            return new NewRecipient(address, null, null, Map.of());
        }
    }

    /** 建任务的请求。 */
    public record NewTask(
            UUID surveyId,
            DeliveryChannel channel,
            UUID linkId,
            String subject,
            String bodyTemplate,
            List<NewRecipient> recipients,
            String idempotencyKey) {

        public NewTask {
            recipients = recipients == null ? List.of() : List.copyOf(recipients);
        }
    }

    public TaskView create(TenantContext ctx, NewTask request) {
        access.require(ctx, DeliveryAccess.SEND, request.surveyId());
        requireChannelIsServiceable(request.channel());
        validate(request);
        return tenantScope.call(ctx.tenantId(), () -> {
            if (request.idempotencyKey() != null) {
                TaskRow existing = tasks.findByIdempotencyKey(ctx.actorId(), request.idempotencyKey()).orElse(null);
                if (existing != null) {
                    return TaskView.of(existing);
                }
            }
            UUID taskId = UUID.randomUUID();
            tasks.insert(ctx.tenantId(), taskId, request.surveyId(), request.linkId(), request.channel(),
                    TaskKind.INVITE, request.subject(), request.bodyTemplate(), null, request.idempotencyKey(),
                    ctx.actorId());
            int queued = addRecipients(ctx.tenantId(), taskId, request.channel(), request.recipients());
            tasks.setQueuedCount(taskId, queued);
            audit.record(ctx, DeliveryAudit.TASK_CREATE,
                    "task/" + taskId + " survey/" + request.surveyId() + " channel=" + request.channel().code()
                            + " recipients=" + queued);
            return TaskView.of(tasks.find(taskId).orElseThrow());
        });
    }

    public TaskView status(TenantContext ctx, UUID taskId) {
        return tenantScope.call(ctx.tenantId(), () -> {
            TaskRow row = row(taskId);
            access.require(ctx, DeliveryAccess.READ, row.surveyId());
            return TaskView.of(row);
        });
    }

    public List<RecipientView> recipients(TenantContext ctx, UUID taskId) {
        return tenantScope.call(ctx.tenantId(), () -> {
            TaskRow row = row(taskId);
            access.require(ctx, DeliveryAccess.READ, row.surveyId());
            return recipients.list(taskId).stream().map(RecipientView::of).toList();
        });
    }

    /** 取消：已发出的不会撤回，只是不再继续发剩下的。幂等。 */
    public TaskView cancel(TenantContext ctx, UUID taskId) {
        return tenantScope.call(ctx.tenantId(), () -> {
            TaskRow row = row(taskId);
            access.require(ctx, DeliveryAccess.SEND, row.surveyId());
            if (tasks.cancel(taskId)) {
                audit.record(ctx, DeliveryAudit.TASK_CANCEL, "task/" + taskId);
            }
            return TaskView.of(tasks.find(taskId).orElseThrow());
        });
    }

    /** 把名单写进收件人表；返回实际入库的条数（重复地址只算一次）。须在租户作用域内调用。 */
    int addRecipients(TenantId tenant, UUID taskId, DeliveryChannel channel, List<NewRecipient> list) {
        int queued = 0;
        int index = 0;
        for (NewRecipient recipient : list) {
            index++;
            String address = Addresses.normalise(channel, recipient.address(), index);
            String respondentKey = recipient.engineToken() == null || recipient.engineToken().isBlank()
                    ? randomRespondentKey()
                    : recipient.engineToken();
            if (recipients.insert(tenant, taskId, UUID.randomUUID(), address, recipient.displayName(),
                    recipient.engineToken(), respondentKey, recipient.params())) {
                queued++;
            }
        }
        return queued;
    }

    /** 不可预测的应答者标识：没有邀请码时也不能让人猜出别人的标识，否则完成状态可以被冒充登记。 */
    static String randomRespondentKey() {
        byte[] bytes = new byte[16];
        RANDOM.nextBytes(bytes);
        return HexFormat.of().formatHex(bytes);
    }

    TaskRow row(UUID taskId) {
        return tasks.find(taskId).orElseThrow(() -> DeliveryExceptions.notFound("delivery task not found: " + taskId));
    }

    private void requireChannelIsServiceable(DeliveryChannel channel) {
        providers.require(channel);
    }

    private void validate(NewTask request) {
        if (request.surveyId() == null) {
            throw DeliveryExceptions.invalid("surveyId is required");
        }
        if (request.bodyTemplate() == null || request.bodyTemplate().isBlank()
                || request.bodyTemplate().length() > 4000) {
            throw DeliveryExceptions.invalid("bodyTemplate must be 1 to 4000 characters");
        }
        if (!request.bodyTemplate().contains(MessageRenderer.LINK)) {
            throw DeliveryExceptions.invalid("bodyTemplate must contain " + MessageRenderer.LINK);
        }
        // 邮件必须带退订入口：这是反垃圾的硬要求，也是退订名单能生效的前提。
        if (request.channel() == DeliveryChannel.EMAIL
                && !request.bodyTemplate().contains(MessageRenderer.UNSUBSCRIBE)) {
            throw DeliveryExceptions.invalid("email bodyTemplate must contain " + MessageRenderer.UNSUBSCRIBE);
        }
        if (request.recipients().isEmpty()) {
            throw DeliveryExceptions.invalid("at least one recipient is required");
        }
        if (request.recipients().size() > MAX_RECIPIENTS) {
            throw DeliveryExceptions.invalid("at most " + MAX_RECIPIENTS + " recipients per task");
        }
        Set<String> tokens = new LinkedHashSet<>();
        int index = 0;
        for (NewRecipient recipient : request.recipients()) {
            index++;
            if (recipient.engineToken() != null && !recipient.engineToken().isBlank()
                    && !tokens.add(recipient.engineToken())) {
                // 两个人共用一个邀请码，限次与完成判定都会串（ADR 0016 决定 6）。
                throw DeliveryExceptions.invalid("recipient " + index + " reuses an invitation code");
            }
            if (recipient.params().keySet().stream().anyMatch(SignedParameters::isReserved)
                    || recipient.params().containsKey(RESPONDENT_KEY_PARAM)
                    || recipient.params().containsKey(TOKEN_PARAM)) {
                throw DeliveryExceptions.invalid("recipient " + index + " uses a reserved parameter name");
            }
        }
    }
}
