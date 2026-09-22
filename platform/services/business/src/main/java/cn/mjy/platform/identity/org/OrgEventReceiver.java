package cn.mjy.platform.identity.org;

import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import org.springframework.http.MediaType;

/**
 * 一家开放平台的通讯录事件回调：验签、校验时间戳、解密、解析，给出离职成员与应答。
 * 实现<b>不写库</b>；任何校验失败都抛 {@link OrgEventException}（401），调用方因此不会产生副作用。
 */
interface OrgEventReceiver {

    /** 回调请求：查询参数（已解码，取首值）、请求头（名字小写）、原始请求体与收到时间。 */
    record Request(String method, Map<String, String> query, Map<String, String> headers, byte[] body,
            Instant receivedAt) {

        String param(String name) {
            return query.get(name);
        }

        String header(String name) {
            return headers.get(name.toLowerCase(Locale.ROOT));
        }
    }

    /** 事件订阅的两个密钥：Token（企业微信 Token / 钉钉 token / 飞书 Verification Token）与加密密钥。 */
    record Secrets(String token, String key) {

        @Override
        public String toString() {
            return "Secrets[redacted]";
        }
    }

    record Reply(MediaType contentType, String body) {
    }

    /**
     * 已验证的回调。eventId 为空表示握手（URL 校验）或无需处理的消息；否则为用于去重的提供方事件标识
     * （飞书 event_id；企业微信 / 钉钉没有事件标识，用解密后的完整消息体）。
     */
    record Verified(String eventId, String eventType, List<String> departedUserIds, Reply reply) {

        static Verified handshake(Reply reply) {
            return new Verified(null, "handshake", List.of(), reply);
        }
    }

    OrgProvider provider();

    Verified receive(OrgConnection connection, Secrets secrets, Request request, Duration maxSkew);

    /** 时间戳须在收到时间前后 maxSkew 之内（防重放；重复投递另由事件回执去重）。 */
    static void requireFresh(Instant sentAt, Instant receivedAt, Duration maxSkew) {
        if (Duration.between(sentAt, receivedAt).abs().compareTo(maxSkew) > 0) {
            throw OrgEventException.unauthenticated("event timestamp is outside the accepted window");
        }
    }

    /** 解析数字时间戳；缺失或不是数字按验签失败处理。 */
    static long parseTimestamp(String value) {
        if (value == null || !value.matches("\\d{1,16}")) {
            throw OrgEventException.unauthenticated("event timestamp is missing or invalid");
        }
        return Long.parseLong(value);
    }

    /** 事件类型只保留安全字符，便于入库与审计。 */
    static String safeType(String type) {
        if (type == null || !type.matches("[A-Za-z0-9_.:-]{1,128}")) {
            return "unknown";
        }
        return type;
    }
}
