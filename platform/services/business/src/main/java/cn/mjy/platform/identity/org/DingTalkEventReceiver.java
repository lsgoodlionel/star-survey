package cn.mjy.platform.identity.org;

import java.security.SecureRandom;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * 钉钉企业内部应用 HTTP 事件订阅（通讯录事件）。依据：
 * <ul>
 *   <li>官方加解密库与示例 https://github.com/open-dingtalk/DingTalk-Callback-Crypto
 *       （open.dingtalk.com 的"配置事件订阅 / 回调事件消息体加解密"页面为前端渲染，未能直接抓取）</li>
 *   <li>通讯录事件 https://open.dingtalk.com/document/orgapp/address-book-events（user_leave_org 字段按接口惯例，未逐页核对）</li>
 * </ul>
 * 请求：{@code POST ?msg_signature(或 signature)&timeStamp(或 timestamp，毫秒)&nonce}，体 {@code {"encrypt":"…"}}；
 * 签名 = SHA-1(排序拼接 token、timeStamp、nonce、encrypt)；解密后 receiveid 须为连接的 AppKey
 * （企业内部应用事件订阅）或 corpId（旧版注册回调接口）。
 * 应答：加密的 "success"：{@code {"msg_signature","timeStamp","nonce","encrypt"}}；URL 校验事件 {@code check_url}
 * 同样以此应答。离职：{@code user_leave_org} 的 {@code UserId} 数组。
 */
@Component
class DingTalkEventReceiver implements OrgEventReceiver {

    private static final String SUCCESS = "success";
    private static final int NONCE_BYTES = 8;

    private final JsonMapper json;
    private final SecureRandom random = new SecureRandom();

    DingTalkEventReceiver(JsonMapper json) {
        this.json = json;
    }

    @Override
    public OrgProvider provider() {
        return OrgProvider.DINGTALK;
    }

    @Override
    public Verified receive(OrgConnection connection, Secrets secrets, Request request, Duration maxSkew) {
        String signature = first(request.param("msg_signature"), request.param("signature"));
        String timestamp = first(request.param("timeStamp"), request.param("timestamp"));
        String nonce = request.param("nonce");
        String encrypted = readJson(request.body()).path("encrypt").asString(null);
        if (nonce == null || encrypted == null) {
            throw OrgEventException.unauthenticated("event nonce or payload is missing");
        }
        String expected = OrgEventCrypto.sortedSha1(secrets.token(), timestamp == null ? "" : timestamp, nonce,
                encrypted);
        if (!OrgEventCrypto.signatureMatches(expected, signature)) {
            throw OrgEventException.unauthenticated("event signature does not match");
        }
        OrgEventReceiver.requireFresh(Instant.ofEpochMilli(OrgEventReceiver.parseTimestamp(timestamp)),
                request.receivedAt(), maxSkew);
        OrgEventCrypto.Envelope envelope = OrgEventCrypto.openWxBiz(secrets.key(), encrypted);
        String receiver = envelope.receiver();
        if (!connection.appId().equals(receiver) && !connection.corpId().equals(receiver)) {
            throw OrgEventException.unauthenticated("event was sealed for another application");
        }
        JsonNode event = readJson(envelope.message().getBytes(java.nio.charset.StandardCharsets.UTF_8));
        String corpId = event.path("CorpId").asString(null);
        if (corpId != null && !connection.corpId().equals(corpId)) {
            throw OrgEventException.unauthenticated("event addresses another organisation");
        }
        Reply reply = success(secrets, receiver, request.receivedAt());
        String type = event.path("EventType").asString(null);
        if ("check_url".equals(type)) {
            return Verified.handshake(reply);
        }
        List<String> departed = "user_leave_org".equals(type) ? userIds(event.path("UserId")) : List.of();
        return new Verified(envelope.message(), OrgEventReceiver.safeType(type), departed, reply);
    }

    private Reply success(Secrets secrets, String receiver, Instant now) {
        String timestamp = Long.toString(now.toEpochMilli());
        byte[] nonceBytes = new byte[NONCE_BYTES];
        random.nextBytes(nonceBytes);
        String nonce = HexFormat.of().formatHex(nonceBytes);
        String encrypted = OrgEventCrypto.sealWxBiz(secrets.key(), SUCCESS, receiver);
        Map<String, String> body = new LinkedHashMap<>();
        body.put("msg_signature", OrgEventCrypto.sortedSha1(secrets.token(), timestamp, nonce, encrypted));
        body.put("timeStamp", timestamp);
        body.put("nonce", nonce);
        body.put("encrypt", encrypted);
        return new Reply(MediaType.APPLICATION_JSON, json.writeValueAsString(body));
    }

    private static List<String> userIds(JsonNode node) {
        List<String> ids = new ArrayList<>();
        if (node.isArray()) {
            node.forEach(id -> {
                if (id.isString() && !id.asString().isBlank()) {
                    ids.add(id.asString());
                }
            });
        } else if (node.isString() && !node.asString().isBlank()) {
            ids.add(node.asString());
        }
        return List.copyOf(ids);
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

    private static String first(String a, String b) {
        return a != null ? a : b;
    }
}
