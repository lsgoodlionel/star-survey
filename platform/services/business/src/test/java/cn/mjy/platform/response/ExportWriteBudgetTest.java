package cn.mjy.platform.response;

import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.time.Duration;
import org.junit.jupiter.api.Test;

class ExportWriteBudgetTest {

    @Test
    void rejectsBytesBeyondTheConfiguredArtifactLimit() {
        ExportWriteBudget budget = new ExportWriteBudget(10, Duration.ofMinutes(1));
        budget.beforeWrite(0, 10);

        assertThatThrownBy(() -> budget.beforeWrite(10, 1))
                .isInstanceOf(ExportBudgetExceededException.class)
                .hasMessageContaining("最大字节数");
    }

    @Test
    void rejectsWorkAfterTheConfiguredDeadline() {
        ExportWriteBudget budget = new ExportWriteBudget(10, Duration.ZERO);

        assertThatThrownBy(budget::checkpoint)
                .isInstanceOf(ExportBudgetExceededException.class)
                .hasMessageContaining("允许时长");
    }
}
