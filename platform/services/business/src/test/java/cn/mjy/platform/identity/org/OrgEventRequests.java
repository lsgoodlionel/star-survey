package cn.mjy.platform.identity.org;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;

import java.net.URI;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.stream.Collectors;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;

/**
 * 按各开放平台文档的算法构造回调请求（签名、时间戳、加密），供接口测试使用。
 * 加密本身与生产代码共用 {@link OrgEventCrypto}；其正确性另由 {@link OrgEventCryptoTest} 用官方向量证明。
 */
final class OrgEventRequests {

    private OrgEventRequests() {
    }

    // ---- 企业微信 ----

    static String wecomContactEvent(String corp, String changeType, String userId, String status) {
        String statusField = status == null ? "" : "<Status>" + status + "</Status>";
        return "<xml><ToUserName><![CDATA[" + corp + "]]></ToUserName><FromUserName><![CDATA[sys]]></FromUserName>"
                + "<CreateTime>" + Instant.now().getEpochSecond() + "</CreateTime>"
                + "<MsgType><![CDATA[event]]></MsgType><Event><![CDATA[change_contact]]></Event>"
                + "<ChangeType>" + changeType + "</ChangeType><UserID><![CDATA[" + userId + "]]></UserID>"
                + statusField + "<Nonce>" + UUID.randomUUID() + "</Nonce></xml>";
    }

    /** 企业微信事件 POST；receiver 通常为 corpid，timestamp 为秒。 */
    static MockHttpServletRequestBuilder wecomPost(String path, OrgLoginTestSupport.EventSecrets secrets,
            String receiver, String message, long timestamp) {
        String encrypt = OrgEventCrypto.sealWxBiz(secrets.key(), message, receiver);
        return wecomPostSealed(path, secrets.token(), encrypt, Long.toString(timestamp), receiver);
    }

    static MockHttpServletRequestBuilder wecomPostSealed(String path, String token, String encrypt, String timestamp,
            String corp) {
        String nonce = nonce();
        Map<String, String> query = new LinkedHashMap<>();
        query.put("msg_signature", OrgEventCrypto.sortedSha1(token, timestamp, nonce, encrypt));
        query.put("timestamp", timestamp);
        query.put("nonce", nonce);
        String body = "<xml><ToUserName><![CDATA[" + corp + "]]></ToUserName><Encrypt><![CDATA[" + encrypt
                + "]]></Encrypt><AgentID><![CDATA[1000002]]></AgentID></xml>";
        return post(uri(path, query)).contentType(MediaType.TEXT_XML).content(body);
    }

    static MockHttpServletRequestBuilder wecomVerify(String path, OrgLoginTestSupport.EventSecrets secrets,
            String receiver, String echo, long timestamp) {
        String echostr = OrgEventCrypto.sealWxBiz(secrets.key(), echo, receiver);
        String ts = Long.toString(timestamp);
        String nonce = nonce();
        Map<String, String> query = new LinkedHashMap<>();
        query.put("msg_signature", OrgEventCrypto.sortedSha1(secrets.token(), ts, nonce, echostr));
        query.put("timestamp", ts);
        query.put("nonce", nonce);
        query.put("echostr", echostr);
        return get(uri(path, query));
    }

    // ---- 钉钉 ----

    static String dingtalkLeave(String corp, String... userIds) {
        String ids = java.util.Arrays.stream(userIds).map(id -> "\"" + id + "\"").collect(Collectors.joining(","));
        return "{\"EventType\":\"user_leave_org\",\"TimeStamp\":\"" + System.currentTimeMillis()
                + "\",\"UserId\":[" + ids + "],\"CorpId\":\"" + corp + "\",\"n\":\"" + UUID.randomUUID() + "\"}";
    }

    /** 钉钉事件 POST；receiver 为应用 AppKey，timestamp 为毫秒。 */
    static MockHttpServletRequestBuilder dingtalkPost(String path, OrgLoginTestSupport.EventSecrets secrets,
            String receiver, String message, long timestampMillis) {
        String encrypt = OrgEventCrypto.sealWxBiz(secrets.key(), message, receiver);
        String ts = Long.toString(timestampMillis);
        String nonce = nonce();
        Map<String, String> query = new LinkedHashMap<>();
        query.put("msg_signature", OrgEventCrypto.sortedSha1(secrets.token(), ts, nonce, encrypt));
        query.put("timeStamp", ts);
        query.put("nonce", nonce);
        return post(uri(path, query)).contentType(MediaType.APPLICATION_JSON)
                .content("{\"encrypt\":\"" + encrypt + "\"}");
    }

    // ---- 飞书 ----

    static String feishuEvent(String eventId, String type, String token, String appId, String tenantKey,
            String userId, boolean resigned) {
        return """
                {"schema":"2.0","header":{"event_id":"%s","event_type":"%s","create_time":"%d","token":"%s",
                "app_id":"%s","tenant_key":"%s"},"event":{"object":{"open_id":"ou_x","union_id":"on_x",
                "user_id":"%s","status":{"is_frozen":false,"is_resigned":%s,"is_activated":true,
                "is_exited":false,"is_unjoin":false}}}}
                """.formatted(eventId, type, System.currentTimeMillis(), token, appId, tenantKey, userId, resigned);
    }

    static String feishuChallenge(String token, String challenge) {
        return "{\"challenge\":\"" + challenge + "\",\"token\":\"" + token + "\",\"type\":\"url_verification\"}";
    }

    /** 飞书 POST：加密体；signed 为真时带签名头，timestamp 为秒。 */
    static MockHttpServletRequestBuilder feishuPost(String path, String encryptKey, String message, boolean signed,
            long timestamp) {
        byte[] body = ("{\"encrypt\":\"" + OrgEventCrypto.sealFeishu(encryptKey, message) + "\"}")
                .getBytes(StandardCharsets.UTF_8);
        MockHttpServletRequestBuilder request = post(URI.create(path)).contentType(MediaType.APPLICATION_JSON)
                .content(body);
        if (signed) {
            String ts = Long.toString(timestamp);
            String nonce = nonce();
            request = request.header(FeishuEventReceiver.TIMESTAMP_HEADER, ts)
                    .header(FeishuEventReceiver.NONCE_HEADER, nonce)
                    .header(FeishuEventReceiver.SIGNATURE_HEADER,
                            OrgEventCrypto.feishuSignature(ts, nonce, encryptKey, body));
        }
        return request;
    }

    static long nowSeconds() {
        return Instant.now().getEpochSecond();
    }

    static String nonce() {
        return UUID.randomUUID().toString().replace("-", "").substring(0, 12);
    }

    static URI uri(String path, Map<String, String> query) {
        return URI.create(path + "?" + query.entrySet().stream()
                .map(e -> e.getKey() + "=" + URLEncoder.encode(e.getValue(), StandardCharsets.UTF_8))
                .collect(Collectors.joining("&")));
    }
}
