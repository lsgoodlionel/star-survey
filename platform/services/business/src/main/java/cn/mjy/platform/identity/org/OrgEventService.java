package cn.mjy.platform.identity.org;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.tenant.api.NotFoundException;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.function.Function;
import java.util.stream.Collectors;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * 通讯录事件回调的处理（ADR 0014 增补二）：
 * <ol>
 *   <li>找到连接（须配置了事件订阅），按"租户 + 名字"解析 Token 与加密密钥；</li>
 *   <li>交给对应开放平台的 {@link OrgEventReceiver} 验签、校验时间戳、解密、解析——失败即 401，此前没有任何写入；</li>
 *   <li>握手直接应答；事件按 (连接, 事件标识摘要) 去重：已有回执则只应答；否则对每个离职成员调用
 *       {@link OrgDirectorySyncService#memberDeparted}（撤销绑定、会话，移除成员），成功后写回执。</li>
 * </ol>
 * 处理失败不写回执，开放平台重试时会重新处理；撤权幂等，并发的重复投递至多多写零条回执。
 */
@Service
class OrgEventService {

    private static final Logger log = LoggerFactory.getLogger(OrgEventService.class);
    static final String EVENT_PATH = "/v1/org-events";

    private final TenantScope tenantScope;
    private final OrgConnectionService connections;
    private final OrgDirectorySyncService sync;
    private final OrgEventReceiptRepository receipts;
    private final SecretResolver secrets;
    private final OrgEventProperties properties;
    private final OrgLoginProperties loginProperties;
    private final Map<OrgProvider, OrgEventReceiver> receivers;

    OrgEventService(TenantScope tenantScope, OrgConnectionService connections, OrgDirectorySyncService sync,
            OrgEventReceiptRepository receipts, SecretResolver secrets, OrgEventProperties properties,
            OrgLoginProperties loginProperties, List<OrgEventReceiver> receivers) {
        this.tenantScope = tenantScope;
        this.connections = connections;
        this.sync = sync;
        this.receipts = receipts;
        this.secrets = secrets;
        this.properties = properties;
        this.loginProperties = loginProperties;
        this.receivers = receivers.stream().collect(Collectors.toUnmodifiableMap(OrgEventReceiver::provider,
                Function.identity()));
    }

    /** 须在开放平台登记的事件回调地址；未配置平台对外地址时为空。 */
    String eventUrl(TenantId tenant, UUID connectionId) {
        if (!loginProperties.isCallbackConfigured()) {
            return null;
        }
        return loginProperties.callbackBaseUrl().replaceAll("/+$", "") + EVENT_PATH + "/" + tenant + "/"
                + connectionId;
    }

    int maxBodyBytes() {
        return properties.maxBodyBytes();
    }

    OrgEventReceiver.Reply receive(TenantId tenant, UUID connectionId, OrgEventReceiver.Request request) {
        OrgConnection connection = connections.find(tenant, connectionId).filter(OrgConnection::receivesEvents)
                .orElseThrow(() -> new NotFoundException("organisation connection not found"));
        OrgEventReceiver.Secrets secret = new OrgEventReceiver.Secrets(
                secrets.resolve(tenant, connection.eventTokenRef()).orElseThrow(OrgEventException::misconfigured),
                secrets.resolve(tenant, connection.eventKeyRef()).orElseThrow(OrgEventException::misconfigured));
        OrgEventReceiver receiver = receivers.get(connection.provider());
        if (receiver == null) {
            throw OrgEventException.misconfigured();
        }
        OrgEventReceiver.Verified verified = receiver.receive(connection, secret, request,
                properties.maxClockSkew());
        if (verified.eventId() != null) {
            process(connection, verified);
        }
        return verified.reply();
    }

    private void process(OrgConnection connection, OrgEventReceiver.Verified event) {
        TenantId tenant = connection.tenantId();
        String key = OrgEventCrypto.sha256Hex(event.eventId());
        if (tenantScope.call(tenant, () -> receipts.exists(connection.id(), key))) {
            log.info("duplicate {} event {} on connection {} ignored", connection.provider().code(),
                    event.eventType(), connection.id());
            return;
        }
        String trace = UUID.randomUUID().toString();
        int departed = 0;
        for (String userId : event.departedUserIds()) {
            if (sync.memberDeparted(tenant, connection.id(), userId, trace)) {
                departed++;
            }
        }
        int revoked = departed;
        tenantScope.run(tenant, () -> receipts.record(tenant, connection.id(), key, event.eventType(), revoked));
        log.info("{} event {} on connection {}: {} reported departed, {} newly revoked (trace {})",
                connection.provider().code(), event.eventType(), connection.id(), event.departedUserIds().size(),
                revoked, trace);
    }
}
