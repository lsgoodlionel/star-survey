package cn.mjy.platform.platformadmin;

import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import cn.mjy.platform.tenant.PlatformOperatorGuard;
import org.junit.jupiter.api.Test;

class PlatformGuardConfigurationTest {
    @Test void personnelModeCannotStartWithoutIndependentMachineCredentials() {
        var personnel = mock(PlatformPersonnelAccess.class);
        assertThatThrownBy(() -> new PlatformOperatorGuard(personnel, true, "", "", "personnel", "engine"))
                .isInstanceOf(IllegalStateException.class);
        String key = "isolated-key-value-at-least-32-characters";
        assertThatThrownBy(() -> new PlatformOperatorGuard(personnel, true, key, "10000000-0000-4000-8000-000000000001", key, "engine"))
                .isInstanceOf(IllegalStateException.class);
        assertThatThrownBy(() -> new PlatformOperatorGuard(personnel, true, key, "invalid", "personnel", "engine"))
                .isInstanceOf(IllegalStateException.class);
        assertThatThrownBy(() -> new PlatformOperatorGuard(personnel, true, key, "10000000-0000-4000-8000-000000000001", "personnel", ""))
                .isInstanceOf(IllegalStateException.class);
    }
}
