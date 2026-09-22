package cn.mjy.platform.identity.org;

import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * 飞书事件订阅（将事件发送至开发者服务器，v2.0 结构）。文档：
 * <ul>
 *   <li>配置请求地址 / url_verification
 *       https://open.feishu.cn/document/ukTMukTMukTM/uYDNxYjL2QTM24iN0EjN/event-subscription-configure-/choose-a-subscription-mode/send-notifications-to-developers-server</li>
 *   <li>Encrypt Key 解密与签名校验
 *       https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/encrypt-key-encryption-configuration-case</li>
 *   <li>员工离职 contact.user.deleted_v3 https://open.feishu.cn/document/server-docs/contact-v3/user/events/deleted</li>
 *   <li>员工信息变化 contact.user.updated_v3 https://open.feishu.cn/document/server-docs/contact-v3/user/events/updated</li>
 * </ul>
 * 平台要求连接同时配置 Verification Token 与 Encrypt Key，因此只接受加密体 {@code {"encrypt":"…"}}：
 * <ul>
 *   <li>事件必须带 X-Lark-Request-Timestamp / Nonce / Signature，签名 = SHA-256(timestamp + nonce + Encrypt Key + 原始体)，
 *       时间戳（秒）在窗口内；解密后 header.token 等于 Verification Token，header.app_id / tenant_key 等于连接；</li>
 *   <li>url_verification 不一定带签名头：带则同样验签；解密后 token 须相符，1 秒内原样返回 challenge。
 *       握手不产生任何副作用。</li>
 * </ul>
 * 离职：deleted_v3，或 updated_v3 且 status.is_resigned / is_frozen 为真（与拉取同步判定一致）；用户标识取
 * event.object.user_id（需"获取用户 user ID"权限，与免登主体一致，不用 open_id）。
 */
@Component
class FeishuEventReceiver implements OrgEventReceiver {

    static final String TIMESTAMP_HEADER = "X-Lark-Request-Timestamp";
    static final String NONCE_HEADER = "X-Lark-Request-Nonce";
    static final String SIGNATURE_HEADER = "X-Lark-Signature";
    private static final Reply ACK = new Reply(MediaType.APPLICATION_JSON, "{}");

    private final JsonMapper json;

    FeishuEventReceiver(JsonMapper json) {
        this.json = json;
    }

    @Override
    public OrgProvider provider() {
        return OrgProvider.FEISHU;
    }

    @Override
    public Verified receive(OrgConnection connection, Secrets secrets, Request request, Duration maxSkew) {
        String encrypted = readJson(request.body()).path("encrypt").asString(null);
        if (encrypted == null) {
            throw OrgEventException.unauthenticated("event body is not encrypted");
        }
        boolean signed = request.header(SIGNATURE_HEADER) != null;
        if (signed) {
            requireSignature(secrets, request, maxSkew);
        }
        JsonNode payload = readJson(OrgEventCrypto.openFeishu(secrets.key(), encrypted)
                .getBytes(StandardCharsets.UTF_8));
        if ("url_verification".equals(payload.path("type").asString(null))) {
            requireToken(secrets, payload.path("token").asString(null));
            String challenge = payload.path("challenge").asString("");
            return Verified.handshake(new Reply(MediaType.APPLICATION_JSON,
                    json.writeValueAsString(Map.of("challenge", challenge))));
        }
        if (!signed) {
            throw OrgEventException.unauthenticated("event signature is missing");
        }
        return parseEvent(connection, secrets, payload);
    }

    private Verified parseEvent(OrgConnection connection, Secrets secrets, JsonNode payload) {
        JsonNode header = payload.path("header");
        if (!"2.0".equals(payload.path("schema").asString(null))) {
            // 1.0 结构的事件：令牌在顶层；不处理，只确认收到。
            requireToken(secrets, payload.path("token").asString(null));
            return new Verified(null, "v1", List.of(), ACK);
        }
        requireToken(secrets, header.path("token").asString(null));
        if (!connection.appId().equals(header.path("app_id").asString(null))) {
            throw OrgEventException.unauthenticated("event is for another application");
        }
        String tenantKey = header.path("tenant_key").asString(null);
        if (tenantKey != null && !connection.corpId().equals(tenantKey)) {
            throw OrgEventException.unauthenticated("event addresses another organisation");
        }
        String eventId = header.path("event_id").asString(null);
        if (eventId == null || eventId.isBlank()) {
            throw OrgEventException.malformed("event has no event_id");
        }
        String type = header.path("event_type").asString(null);
        return new Verified(eventId, OrgEventReceiver.safeType(type), departed(type, payload.path("event")), ACK);
    }

    private static List<String> departed(String type, JsonNode event) {
        JsonNode user = event.path("object");
        String userId = user.path("user_id").asString(null);
        if (userId == null || userId.isBlank()) {
            return List.of();
        }
        if ("contact.user.deleted_v3".equals(type)) {
            return List.of(userId);
        }
        JsonNode status = user.path("status");
        boolean gone = status.path("is_resigned").asBoolean(false) || status.path("is_frozen").asBoolean(false);
        return "contact.user.updated_v3".equals(type) && gone ? List.of(userId) : List.of();
    }

    private static void requireSignature(Secrets secrets, Request request, Duration maxSkew) {
        String timestamp = request.header(TIMESTAMP_HEADER);
        String nonce = request.header(NONCE_HEADER);
        if (nonce == null || timestamp == null) {
            throw OrgEventException.unauthenticated("event signature headers are missing");
        }
        String expected = OrgEventCrypto.feishuSignature(timestamp, nonce, secrets.key(), request.body());
        if (!OrgEventCrypto.signatureMatches(expected, request.header(SIGNATURE_HEADER))) {
            throw OrgEventException.unauthenticated("event signature does not match");
        }
        OrgEventReceiver.requireFresh(Instant.ofEpochSecond(OrgEventReceiver.parseTimestamp(timestamp)),
                request.receivedAt(), maxSkew);
    }

    private static void requireToken(Secrets secrets, String token) {
        if (!OrgEventCrypto.tokenMatches(secrets.token(), token)) {
            throw OrgEventException.unauthenticated("event verification token does not match");
        }
    }

    private JsonNode readJson(byte[] body) {
        try {
            JsonNode node = body.length == 0 ? null : json.readTree(body);
            if (node == null || !node.isObject()) {
                throw OrgEventException.malformed("event body is not a JSON object");
            }
            return node;
        } catch (JacksonException e) {
            throw OrgEventException.malformed("event body is not valid JSON");
        }
    }
}
