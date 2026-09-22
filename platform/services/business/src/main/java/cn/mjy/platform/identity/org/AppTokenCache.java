package cn.mjy.platform.identity.org;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.HexFormat;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Supplier;
import org.springframework.stereotype.Component;

/**
 * 应用级 access_token 缓存（企业微信 gettoken、钉钉 oauth2/accessToken、飞书 tenant_access_token）。
 * 企业微信要求缓存、不得频繁获取；提前 {@link #SAFETY_MARGIN} 过期。键里含密钥的摘要，换密钥后自动重取；
 * 缓存只在内存里，不落库、不记日志。
 */
@Component
class AppTokenCache {

    record AppToken(String value, Duration lifetime) {
    }

    static final Duration SAFETY_MARGIN = Duration.ofMinutes(5);
    private static final Duration MIN_LIFETIME = Duration.ofSeconds(30);

    private record Entry(String value, Instant expiresAt) {
    }

    private final Map<String, Entry> entries = new ConcurrentHashMap<>();
    private final Clock clock = Clock.systemUTC();

    String get(OrgConnection connection, String secret, Supplier<AppToken> fetch) {
        String key = key(connection, secret);
        Entry cached = entries.get(key);
        Instant now = clock.instant();
        if (cached != null && cached.expiresAt().isAfter(now)) {
            return cached.value();
        }
        AppToken fresh = fetch.get();
        Duration usable = fresh.lifetime().minus(SAFETY_MARGIN);
        entries.put(key, new Entry(fresh.value(), now.plus(usable.compareTo(MIN_LIFETIME) < 0 ? MIN_LIFETIME : usable)));
        return fresh.value();
    }

    /** 开放平台报告令牌失效时丢弃，下次重取。 */
    void invalidate(OrgConnection connection, String secret) {
        entries.remove(key(connection, secret));
    }

    private static String key(OrgConnection connection, String secret) {
        return connection.provider().code() + "|" + connection.corpId() + "|" + connection.appId() + "|" + digest(secret);
    }

    private static String digest(String secret) {
        try {
            byte[] hash = MessageDigest.getInstance("SHA-256").digest(secret.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(hash);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 unavailable", e);
        }
    }
}
