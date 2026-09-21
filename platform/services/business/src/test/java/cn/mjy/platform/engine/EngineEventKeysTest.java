package cn.mjy.platform.engine;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.engine.support.PhpEventSigner;
import org.junit.jupiter.api.Test;

/**
 * 实例密钥派生：{@code hex(HMAC-SHA256(主密钥, "mjy-engine-events/v1/" + 实例标识))}。
 * 同一实例永远得到同一密钥，不同实例的密钥互不相同，且与独立实现（测试侧 PHP 重现）逐字一致。
 */
class EngineEventKeysTest {

    private static final String MASTER = "unit-test-master-secret-at-least-32-bytes";

    private final EngineEventKeys keys = new EngineEventKeys(MASTER);

    @Test
    void derivationIsDeterministic() {
        assertThat(keys.derivedSecret("engine-a")).isEqualTo(new EngineEventKeys(MASTER).derivedSecret("engine-a"));
    }

    @Test
    void everyInstanceGetsADifferentSecret() {
        assertThat(keys.derivedSecret("engine-a")).isNotEqualTo(keys.derivedSecret("engine-b"));
    }

    @Test
    void anotherMasterSecretYieldsDifferentInstanceSecrets() {
        EngineEventKeys other = new EngineEventKeys("another-master-secret-also-32-bytes-long");

        assertThat(other.derivedSecret("engine-a")).isNotEqualTo(keys.derivedSecret("engine-a"));
    }

    @Test
    void theDerivedSecretIsLowercaseHexOfAnHmacSha256OverThePrefixedInstanceId() {
        String derived = keys.derivedSecret("engine-a");

        assertThat(derived).matches("[0-9a-f]{64}");
        assertThat(derived).isEqualTo(PhpEventSigner.deriveInstanceSecret(MASTER, "engine-a"));
        assertThat(derived).isNotEqualTo(MASTER);
    }

    @Test
    void aMissingOrShortMasterSecretIsNotConfiguredAndDerivesNothing() {
        EngineEventKeys missing = new EngineEventKeys("");
        EngineEventKeys weak = new EngineEventKeys("short");

        assertThat(missing.isConfigured()).isFalse();
        assertThat(weak.isConfigured()).isFalse();
        assertThat(keys.isConfigured()).isTrue();
        assertThatThrownBy(() -> missing.derivedSecret("engine-a")).isInstanceOf(IllegalStateException.class);
    }
}
