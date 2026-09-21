package cn.mjy.platform.engine;

import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.time.Clock;
import java.time.Duration;
import java.util.HexFormat;
import java.util.regex.Pattern;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

/**
 * 校验引擎投递请求的签名，与 {@code plugins/MjyPlatformBridge/MjyHttpEventTransport.php} 对齐：
 * {@code X-Mjy-Signature = hex(HMAC-SHA256(实例密钥, X-Mjy-Timestamp + "." + 原始请求体))}，
 * 其中实例由 {@code X-Mjy-Engine-Instance} 声明，实例密钥由 {@link EngineEventKeys} 从主密钥派生。
 * 签名有效即证明发送方持有"该实例"的密钥；批次内事件是否都属于该实例由接收处理再核对。
 *
 * <p>实例标识要放进 HTTP 头，只接受 1–{@value #MAX_INSTANCE_LENGTH} 个可见 ASCII 字符
 * （Servlet 容器按 ISO-8859-1 解读头部字节，非 ASCII 在两端会得到不同的字符串）。
 *
 * <p>签名按收到的原始字节计算，绝不对解析后再序列化的 JSON 计算（PHP 会把 {@code /} 转义成
 * {@code \/}，任何重新序列化都会得到不同的字节）。
 *
 * <p>重放窗口取 ±{@value #REPLAY_WINDOW_SECONDS} 秒，理由：
 * <ul>
 *   <li>发送端在发起 curl 之前一刻才取 {@code time()}，且超时为 15 秒；每轮重试都重新签名，
 *       所以合法请求到达时与签名时刻只差网络耗时加两端时钟偏差；</li>
 *   <li>私有化部署的主机未必严格对时，留出分钟级偏差以免把整条投递链路卡死；</li>
 *   <li>窗口内的重放不会产生重复效果：收件箱按 eventId 去重，重放的每个事件都只会被判为重复。
 *       窗口的作用是让截获的旧请求很快失效，而不是承担去重。</li>
 * </ul>
 * 未来时间同样按窗口拒绝，防止攻击者拿到一个"提前签好"的请求长期使用。
 *
 * <p>主密钥来自环境变量 {@code PLATFORM_ENGINE_EVENTS_SECRET}，至少 32 字节。缺失或过短时不阻止应用启动
 * （其他模块照常服务），但本接口拒绝一切请求并在启动时告警——失败即关闭；
 * 引擎侧事件保持未投递，配好密钥后自动补投，不丢事件。
 */
@Component
public class EventSignatureVerifier {

    public static final String INSTANCE_HEADER = "X-Mjy-Engine-Instance";
    public static final String TIMESTAMP_HEADER = "X-Mjy-Timestamp";
    public static final String SIGNATURE_HEADER = "X-Mjy-Signature";
    static final long REPLAY_WINDOW_SECONDS = 300;
    public static final Duration REPLAY_WINDOW = Duration.ofSeconds(REPLAY_WINDOW_SECONDS);
    /** 与事件信封里 engineInstanceId 的长度上限一致。 */
    static final int MAX_INSTANCE_LENGTH = EngineEventBatch.MAX_ID_LENGTH;

    private static final Logger log = LoggerFactory.getLogger(EventSignatureVerifier.class);
    private static final Pattern UNIX_SECONDS = Pattern.compile("\\d{1,12}");
    private static final Pattern HEADER_SAFE_INSTANCE = Pattern.compile("[\\x21-\\x7E]{1," + MAX_INSTANCE_LENGTH + "}");
    private static final int SIGNATURE_BYTES = 32;

    /** 校验结论。除 {@link #VALID} 外都应拒绝请求。 */
    public enum Verdict {
        VALID,
        NOT_CONFIGURED,
        MISSING_ENGINE_INSTANCE,
        MALFORMED_ENGINE_INSTANCE,
        MISSING_HEADERS,
        MALFORMED_TIMESTAMP,
        TIMESTAMP_OUT_OF_WINDOW,
        BAD_SIGNATURE
    }

    private final EngineEventKeys keys;
    private final Clock clock;

    @Autowired
    public EventSignatureVerifier(EngineEventKeys keys) {
        this(keys, Clock.systemUTC());
        if (!keys.isConfigured()) {
            log.warn("{} is missing or shorter than {} bytes; /internal/engine-events will reject every request",
                    EngineEventKeys.MASTER_SECRET_PROPERTY, EngineEventKeys.MIN_MASTER_SECRET_BYTES);
        }
    }

    EventSignatureVerifier(EngineEventKeys keys, Clock clock) {
        this.keys = keys;
        this.clock = clock;
    }

    public boolean isConfigured() {
        return keys.isConfigured();
    }

    /**
     * @param engineInstanceId 请求头声明的实例；签名必须是用该实例的派生密钥做的
     */
    public Verdict verify(String engineInstanceId, String timestamp, String signature, byte[] body) {
        if (!keys.isConfigured()) {
            return Verdict.NOT_CONFIGURED;
        }
        if (isBlank(engineInstanceId)) {
            return Verdict.MISSING_ENGINE_INSTANCE;
        }
        if (!HEADER_SAFE_INSTANCE.matcher(engineInstanceId).matches()) {
            return Verdict.MALFORMED_ENGINE_INSTANCE;
        }
        if (isBlank(timestamp) || isBlank(signature)) {
            return Verdict.MISSING_HEADERS;
        }
        if (!UNIX_SECONDS.matcher(timestamp).matches()) {
            return Verdict.MALFORMED_TIMESTAMP;
        }
        long skew = Math.abs(clock.instant().getEpochSecond() - Long.parseLong(timestamp));
        if (skew > REPLAY_WINDOW_SECONDS) {
            return Verdict.TIMESTAMP_OUT_OF_WINDOW;
        }
        return signatureMatches(keys.signingKey(engineInstanceId), timestamp, signature, body)
                ? Verdict.VALID
                : Verdict.BAD_SIGNATURE;
    }

    private static boolean signatureMatches(SecretKeySpec key, String timestamp, String signature, byte[] body) {
        byte[] presented = decodeHex(signature);
        if (presented.length != SIGNATURE_BYTES) {
            return false;
        }
        // 常量时间比较，避免按字节逐步猜出签名。
        return MessageDigest.isEqual(expectedSignature(key, timestamp, body), presented);
    }

    private static byte[] expectedSignature(SecretKeySpec key, String timestamp, byte[] body) {
        try {
            Mac mac = Mac.getInstance(key.getAlgorithm());
            mac.init(key);
            mac.update((timestamp + ".").getBytes(StandardCharsets.US_ASCII));
            mac.update(body);
            return mac.doFinal();
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("HMAC-SHA256 unavailable", e);
        }
    }

    private static byte[] decodeHex(String signature) {
        try {
            return HexFormat.of().parseHex(signature.trim());
        } catch (IllegalArgumentException e) {
            return new byte[0];
        }
    }

    private static boolean isBlank(String value) {
        return value == null || value.isBlank();
    }
}
