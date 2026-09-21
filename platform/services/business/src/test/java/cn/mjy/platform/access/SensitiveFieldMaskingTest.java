package cn.mjy.platform.access;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantContext;
import java.util.HashMap;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.security.access.AccessDeniedException;

/** 字段级：没有"查看敏感字段"权限时，手机号、证件号等被遮蔽；原记录不被修改。 */
@SpringBootTest
class SensitiveFieldMaskingTest {

    private static final Set<String> SENSITIVE = Set.of("phone", "id_number");

    @Autowired
    private AccessFixture fixture;

    @Autowired
    private ResponseFieldPolicies policies;

    private TenantContext owner;
    private UUID survey;
    private Map<String, String> response;

    @BeforeEach
    void surveyWithAResponse() {
        owner = fixture.newTenant();
        survey = fixture.survey(owner.tenantId(), fixture.project(owner.tenantId()));
        response = new HashMap<>();
        response.put("name", "张三");
        response.put("phone", "13812345678");
        response.put("id_number", "110101199001011234");
        response.put("q1", "非常满意");
    }

    @Test
    void sensitiveFieldsAreMaskedWithoutThePermission() {
        TenantContext grader = fixture.member(owner, "grader", "grader", survey);

        Map<String, String> seen = policies.forResponses(grader, survey, SENSITIVE).apply(response);

        assertThat(seen.get("phone")).isEqualTo(ResponseFieldPolicy.MASK);
        assertThat(seen.get("id_number")).isEqualTo(ResponseFieldPolicy.MASK);
        assertThat(seen.get("q1")).isEqualTo("非常满意");
        assertThat(seen.get("name")).isEqualTo("张三");
    }

    @Test
    void sensitiveFieldsAreVisibleWithThePermission() {
        TenantContext rawViewer = fixture.member(owner, "raw", "raw_data_viewer", survey);

        Map<String, String> seen = policies.forResponses(rawViewer, survey, SENSITIVE).apply(response);

        assertThat(seen).isEqualTo(response);
    }

    @Test
    void maskingReturnsACopyAndLeavesTheOriginalUntouched() {
        TenantContext grader = fixture.member(owner, "grader", "grader", survey);

        Map<String, String> seen = policies.forResponses(grader, survey, SENSITIVE).apply(response);

        assertThat(response.get("phone")).isEqualTo("13812345678");
        assertThatThrownBy(() -> seen.put("phone", "x")).isInstanceOf(UnsupportedOperationException.class);
    }

    @Test
    void absentOrNullSensitiveValuesStayAbsentOrNull() {
        TenantContext grader = fixture.member(owner, "grader", "grader", survey);
        response.remove("id_number");
        response.put("phone", null);

        Map<String, String> seen = policies.forResponses(grader, survey, SENSITIVE).apply(response);

        assertThat(seen).doesNotContainKey("id_number");
        assertThat(seen).containsEntry("phone", null);
    }

    @Test
    void aStatisticsViewerCannotObtainResponseRecordsAtAll() {
        TenantContext viewer = fixture.member(owner, "stats", "statistics_viewer", survey);

        assertThatThrownBy(() -> policies.forResponses(viewer, survey, SENSITIVE))
                .isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> policies.forExport(viewer, survey, SENSITIVE))
                .isInstanceOf(AccessDeniedException.class);
    }

    @Test
    void exportsAreMaskedTheSameWay() {
        TenantContext manager = fixture.member(owner, "pm", "project_manager", survey);

        Map<String, String> exported = policies.forExport(manager, survey, SENSITIVE).apply(response);

        assertThat(exported.get("phone")).isEqualTo(ResponseFieldPolicy.MASK);
    }
}
