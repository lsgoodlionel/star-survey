package cn.mjy.platform.identity.org;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.function.LongSupplier;

/**
 * 进程内令牌桶：每个键一个桶，按经过的时间连续补充，容量＝每分钟许可数
 * （也就是允许一整分钟的量作为突发）。只用 JDK，不引入依赖。
 *
 * <p>桶数有上限，满了按「最久没被访问」淘汰。桶越久没被碰就越接近装满，因此被淘汰的几乎总是满桶：
 * 淘汰不会放宽正在生效的限流，内存却是有界的。
 *
 * <p>全部方法同步：单次操作只是几次算术，竞争成本远低于它挡掉的解密与数据库查询。
 */
final class TokenBucketLimiter {

    private static final double NANOS_PER_MINUTE = 60_000_000_000.0;

    /** 某个键此刻的余量与结算时刻。不可变：每次操作放回一个新的。 */
    private record Bucket(double tokens, long atNanos) {
    }

    private final double capacity;
    private final double tokensPerNano;
    private final LongSupplier nanoTime;
    private final Map<String, Bucket> buckets;

    TokenBucketLimiter(int permitsPerMinute, int maxBuckets, LongSupplier nanoTime) {
        if (permitsPerMinute <= 0 || maxBuckets <= 0) {
            throw new IllegalArgumentException("permitsPerMinute and maxBuckets must be positive");
        }
        this.capacity = permitsPerMinute;
        this.tokensPerNano = permitsPerMinute / NANOS_PER_MINUTE;
        this.nanoTime = nanoTime;
        this.buckets = new LinkedHashMap<>(16, 0.75f, true) {
            @Override
            protected boolean removeEldestEntry(Map.Entry<String, Bucket> eldest) {
                return size() > maxBuckets;
            }
        };
    }

    /** 扣一个令牌；桶空了返回 false（不扣）。 */
    synchronized boolean tryAcquire(String key) {
        long now = nanoTime.getAsLong();
        Bucket bucket = refilled(buckets.get(key), now);
        boolean granted = bucket.tokens() >= 1.0;
        buckets.put(key, granted ? new Bucket(bucket.tokens() - 1.0, now) : bucket);
        return granted;
    }

    /** 把一个已扣的令牌还回去：请求事后被判定为正当，不该占用未验签的额度。 */
    synchronized void refund(String key) {
        long now = nanoTime.getAsLong();
        Bucket bucket = refilled(buckets.get(key), now);
        buckets.put(key, new Bucket(Math.min(capacity, bucket.tokens() + 1.0), now));
    }

    /** 当前留存的桶数（内存上限的测试口径）。 */
    synchronized int bucketCount() {
        return buckets.size();
    }

    private Bucket refilled(Bucket bucket, long now) {
        if (bucket == null) {
            return new Bucket(capacity, now);
        }
        double gained = Math.max(0L, now - bucket.atNanos()) * tokensPerNano;
        return new Bucket(Math.min(capacity, bucket.tokens() + gained), now);
    }
}
