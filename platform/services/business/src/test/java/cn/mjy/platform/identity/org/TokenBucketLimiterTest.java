package cn.mjy.platform.identity.org;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.concurrent.atomic.AtomicLong;
import org.junit.jupiter.api.Test;

/** 令牌桶本身：突发额度、按时间补充、键之间互不影响、条目数有上限。 */
class TokenBucketLimiterTest {

    private static final long SECOND = 1_000_000_000L;

    private final AtomicLong clock = new AtomicLong();

    private TokenBucketLimiter limiter(int perMinute, int maxBuckets) {
        return new TokenBucketLimiter(perMinute, maxBuckets, clock::get);
    }

    @Test
    void aFreshKeyStartsFullAndRunsOutAfterItsPermits() {
        TokenBucketLimiter limiter = limiter(3, 10);

        assertThat(limiter.tryAcquire("a")).isTrue();
        assertThat(limiter.tryAcquire("a")).isTrue();
        assertThat(limiter.tryAcquire("a")).isTrue();
        assertThat(limiter.tryAcquire("a")).isFalse();
    }

    @Test
    void keysDoNotShareTokens() {
        TokenBucketLimiter limiter = limiter(1, 10);

        assertThat(limiter.tryAcquire("a")).isTrue();
        assertThat(limiter.tryAcquire("a")).isFalse();
        assertThat(limiter.tryAcquire("b")).isTrue();
    }

    @Test
    void tokensComeBackAsTimePasses() {
        TokenBucketLimiter limiter = limiter(60, 10);
        for (int i = 0; i < 60; i++) {
            assertThat(limiter.tryAcquire("a")).isTrue();
        }
        assertThat(limiter.tryAcquire("a")).isFalse();

        clock.addAndGet(SECOND);

        assertThat(limiter.tryAcquire("a")).isTrue();
        assertThat(limiter.tryAcquire("a")).isFalse();
    }

    @Test
    void refillNeverExceedsTheBurstAllowance() {
        TokenBucketLimiter limiter = limiter(2, 10);
        assertThat(limiter.tryAcquire("a")).isTrue();
        assertThat(limiter.tryAcquire("a")).isTrue();

        clock.addAndGet(SECOND * 600);

        assertThat(limiter.tryAcquire("a")).isTrue();
        assertThat(limiter.tryAcquire("a")).isTrue();
        assertThat(limiter.tryAcquire("a")).isFalse();
    }

    @Test
    void aRefundedTokenCanBeSpentAgainButNeverOverfillsTheBucket() {
        TokenBucketLimiter limiter = limiter(1, 10);
        assertThat(limiter.tryAcquire("a")).isTrue();

        limiter.refund("a");
        limiter.refund("a");

        assertThat(limiter.tryAcquire("a")).isTrue();
        assertThat(limiter.tryAcquire("a")).isFalse();
    }

    @Test
    void memoryIsBoundedByEvictingTheLeastRecentlyUsedKeys() {
        TokenBucketLimiter limiter = limiter(10, 4);

        for (int i = 0; i < 10_000; i++) {
            limiter.tryAcquire("key-" + i);
        }

        assertThat(limiter.bucketCount()).isEqualTo(4);
    }

    @Test
    void anActiveKeyKeepsItsBucketWhileStrangersAreEvicted() {
        TokenBucketLimiter limiter = limiter(2, 3);
        assertThat(limiter.tryAcquire("busy")).isTrue();
        assertThat(limiter.tryAcquire("busy")).isTrue();

        for (int i = 0; i < 100; i++) {
            limiter.tryAcquire("stranger-" + i);
            limiter.tryAcquire("busy"); // 每轮都碰一下：LRU 淘汰的是陌生键，不是它
        }

        assertThat(limiter.tryAcquire("busy")).isFalse();
        assertThat(limiter.bucketCount()).isEqualTo(3);
    }

    @Test
    void nonPositiveSettingsAreRefused() {
        assertThatThrownBy(() -> limiter(0, 10)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> limiter(10, 0)).isInstanceOf(IllegalArgumentException.class);
    }
}
