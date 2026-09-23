package cn.mjy.platform.delivery;

import cn.mjy.platform.delivery.ReceiptRepository.SendMatch;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.time.Clock;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.HexFormat;
import java.util.List;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Pattern;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * 渠道商回执（R05-04、R05-05）。
 *
 * <h2>认证</h2>
 * 回调不带平台令牌，认证全靠签名：
 * {@code X-Mjy-Signature = hex(HMAC-SHA256(该租户该渠道商的共享密钥, X-Mjy-Timestamp + "." + 原始请求体))}。
 * 密钥按 (租户, 渠道商) 从主密钥派生（{@link DeliveryKeys}），由租户管理员读出后配置到渠道商后台；
 * 一个租户的密钥泄露只影响该租户。签名按<b>收到的原始字节</b>计算，绝不对解析后重新序列化的 JSON 计算。
 *
 * <h2>防重放</h2>
 * 时间戳须在 ±{@value #REPLAY_WINDOW_SECONDS} 秒内；窗口内的重放不会产生重复效果，
 * 因为每条回执按 (渠道商, 事件号) 唯一落库——这才是去重的依据，时间窗只是让截获的旧请求尽快失效。
 *
 * <h2>重复回执不重复计费</h2>
 * 一条回执的全部副作用（计费加一、收件人状态变更、退订登记）都<b>只在唯一键插入成功的那一次</b>执行，
 * 且与插入在同一个事务里。渠道商按"至少一次"重投多少遍，{@code billed_units} 都只加一次。
 */
@Service
public class ReceiptService {

    public static final String PATH = "/d/receipts/";
    public static final String TIMESTAMP_HEADER = "X-Mjy-Timestamp";
    public static final String SIGNATURE_HEADER = "X-Mjy-Signature";
    static final long REPLAY_WINDOW_SECONDS = 300;
    /** 单次回调的请求体上限：未认证的大请求不应把内存吃掉。 */
    static final int MAX_BODY_BYTES = 256 * 1024;
    static final int MAX_EVENTS = 200;
    static final String SYSTEM_ACTOR = "system:delivery-receipt";

    private static final Set<String> KINDS = Set.of("delivered", "bounced", "failed", "complaint", "unsubscribed");
    /** 只有"已送达"计费：未送达的消息渠道商通常不收费，多收不如少收。 */
    private static final String BILLABLE_KIND = "delivered";
    private static final Pattern PROVIDER = Pattern.compile("[a-z0-9][a-z0-9-]{0,63}");
    private static final Pattern UNIX_SECONDS = Pattern.compile("\\d{1,12}");
    private static final int SIGNATURE_BYTES = 32;
    private static final String ALGORITHM = "HmacSHA256";

    private static final Logger log = LoggerFactory.getLogger(ReceiptService.class);

    /** 一次回调的处理结果：收到几条、其中几条是首次（另外几条是重复投递）。 */
    public record Result(int received, int applied) {
    }

    private final TenantScope tenantScope;
    private final ReceiptRepository receipts;
    private final DeliveryTaskRepository tasks;
    private final RecipientRepository recipients;
    private final UnsubscribeService unsubscribes;
    private final DeliveryKeys keys;
    private final DeliveryAudit audit;
    private final JsonMapper json;
    private final Clock clock;

    ReceiptService(TenantScope tenantScope, ReceiptRepository receipts, DeliveryTaskRepository tasks,
            RecipientRepository recipients, UnsubscribeService unsubscribes, DeliveryKeys keys,
            DeliveryAudit audit, JsonMapper json) {
        this.tenantScope = tenantScope;
        this.receipts = receipts;
        this.tasks = tasks;
        this.recipients = recipients;
        this.unsubscribes = unsubscribes;
        this.keys = keys;
        this.audit = audit;
        this.json = json;
        this.clock = Clock.systemUTC();
    }

    /** 验签失败一律抛 {@link ReceiptRejectedException}，调用方回 401 且不留任何副作用。 */
    public Result accept(TenantId tenant, String provider, String timestamp, String signature, byte[] body) {
        if (!PROVIDER.matcher(provider == null ? "" : provider).matches()) {
            throw new ReceiptRejectedException("unknown provider");
        }
        if (body == null || body.length == 0 || body.length > MAX_BODY_BYTES) {
            throw new ReceiptRejectedException("payload out of bounds");
        }
        verify(tenant, provider, timestamp, signature, body);
        List<Event> events = parse(body);
        int applied = 0;
        for (Event event : events) {
            applied += apply(tenant, provider, event) ? 1 : 0;
        }
        return new Result(events.size(), applied);
    }

    /** 一条回执的全部副作用都在这一个事务里，且以唯一键插入成功为前提。 */
    private boolean apply(TenantId tenant, String provider, Event event) {
        return Boolean.TRUE.equals(tenantScope.call(tenant, () -> {
            SendMatch match = match(event).orElse(null);
            if (match == null) {
                // 匹配不上的回执照常回 200：让渠道商别再重投，但不留记录、不计费。
                log.warn("delivery receipt from {} matched no send record", provider);
                return false;
            }
            boolean billable = BILLABLE_KIND.equals(event.kind());
            if (!receipts.insert(tenant, UUID.randomUUID(), provider, event.eventId(), event.kind(), match,
                    billable, event.occurredAt())) {
                return false;
            }
            if (billable) {
                tasks.addBilledUnits(match.taskId(), 1);
            }
            applyToRecipient(tenant, provider, event, match);
            audit.record(tenant, SYSTEM_ACTOR, "receipt-" + event.eventId(), DeliveryAudit.RECEIPT,
                    "task/" + match.taskId() + " recipient/" + match.recipientId()
                            + " provider=" + provider + " kind=" + event.kind());
            return true;
        }));
    }

    private void applyToRecipient(TenantId tenant, String provider, Event event, SendMatch match) {
        recipients.find(match.taskId(), match.recipientId()).ifPresent(recipient -> {
            switch (event.kind()) {
                case "bounced", "failed" -> {
                    recipients.markOutcome(match.taskId(), match.recipientId(), RecipientState.BOUNCED, null,
                            event.kind());
                    tasks.find(match.taskId()).ifPresent(task -> unsubscribes.suppress(tenant, task.channel(),
                            recipient.address(), UnsubscribeService.REASON_BOUNCE, match.taskId(),
                            "receipt-" + event.eventId()));
                }
                case "complaint", "unsubscribed" -> tasks.find(match.taskId())
                        .ifPresent(task -> unsubscribes.suppress(tenant, task.channel(), recipient.address(),
                                UnsubscribeService.REASON_COMPLAINT, match.taskId(), "receipt-" + event.eventId()));
                default -> log.debug("receipt {} from {} needs no recipient state change", event.kind(), provider);
            }
        });
    }

    private Optional<SendMatch> match(Event event) {
        if (event.idempotencyKey() != null) {
            Optional<SendMatch> byKey = receipts.findByIdempotencyKey(event.idempotencyKey());
            if (byKey.isPresent()) {
                return byKey;
            }
        }
        return event.providerMessageId() == null
                ? Optional.empty()
                : receipts.findByProviderMessageId(event.providerMessageId());
    }

    private void verify(TenantId tenant, String provider, String timestamp, String signature, byte[] body) {
        if (timestamp == null || signature == null || !UNIX_SECONDS.matcher(timestamp).matches()) {
            throw new ReceiptRejectedException("missing or malformed signature headers");
        }
        long skew = Math.abs(clock.instant().getEpochSecond() - Long.parseLong(timestamp));
        if (skew > REPLAY_WINDOW_SECONDS) {
            throw new ReceiptRejectedException("timestamp out of window");
        }
        byte[] presented;
        try {
            presented = HexFormat.of().parseHex(signature.trim());
        } catch (IllegalArgumentException e) {
            throw new ReceiptRejectedException("malformed signature");
        }
        if (presented.length != SIGNATURE_BYTES
                || !MessageDigest.isEqual(expected(tenant, provider, timestamp, body), presented)) {
            throw new ReceiptRejectedException("bad signature");
        }
    }

    private byte[] expected(TenantId tenant, String provider, String timestamp, byte[] body) {
        try {
            SecretKeySpec key = keys.receiptKey(tenant, provider);
            Mac mac = Mac.getInstance(ALGORITHM);
            mac.init(key);
            mac.update((timestamp + ".").getBytes(StandardCharsets.US_ASCII));
            mac.update(body);
            return mac.doFinal();
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("HMAC-SHA256 unavailable", e);
        }
    }

    private List<Event> parse(byte[] body) {
        JsonNode root;
        try {
            root = json.readTree(body);
        } catch (JacksonException e) {
            throw new ReceiptRejectedException("malformed payload");
        }
        JsonNode events = root.path("events");
        if (!events.isArray() || events.isEmpty() || events.size() > MAX_EVENTS) {
            throw new ReceiptRejectedException("payload must carry 1 to " + MAX_EVENTS + " events");
        }
        return events.valueStream().map(ReceiptService::toEvent).toList();
    }

    private static Event toEvent(JsonNode node) {
        String eventId = node.path("eventId").asString(null);
        String kind = node.path("kind").asString(null);
        if (eventId == null || eventId.isBlank() || eventId.length() > 128 || !KINDS.contains(kind)) {
            throw new ReceiptRejectedException("event is missing eventId or carries an unknown kind");
        }
        OffsetDateTime occurredAt = null;
        String raw = node.path("occurredAt").asString(null);
        if (raw != null && !raw.isBlank()) {
            try {
                occurredAt = OffsetDateTime.ofInstant(Instant.parse(raw), ZoneOffset.UTC);
            } catch (RuntimeException e) {
                throw new ReceiptRejectedException("occurredAt is not an ISO-8601 instant");
            }
        }
        return new Event(eventId, kind, node.path("idempotencyKey").asString(null),
                node.path("messageId").asString(null), occurredAt);
    }

    /** 回执里的一条事件。 */
    private record Event(String eventId, String kind, String idempotencyKey, String providerMessageId,
            OffsetDateTime occurredAt) {
    }

    /** 401：签名、时间戳或请求体不合格。消息只说形状，不回显任何内容。 */
    public static class ReceiptRejectedException extends RuntimeException {

        public ReceiptRejectedException(String message) {
            super(message);
        }
    }
}
