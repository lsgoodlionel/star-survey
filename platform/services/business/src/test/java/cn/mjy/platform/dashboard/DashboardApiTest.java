package cn.mjy.platform.dashboard;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.support.TestTokens;
import cn.mjy.platform.survey.SurveyFixture;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import cn.mjy.platform.survey.SurveyView;
import java.util.List;
import java.util.stream.Stream;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@SpringBootTest
@AutoConfigureMockMvc
class DashboardApiTest {

    @Autowired
    private MockMvc mvc;

    @Autowired
    private SurveyFixture fixture;

    @Autowired
    private TestTokens tokens;

    @Autowired
    private JsonMapper json;

    private Workspace workspace;
    private SurveyView survey;

    @BeforeEach
    void setUp() {
        workspace = fixture.workspace();
        survey = fixture.newSurvey(workspace);
    }

    @Test
    void defaultsBothLimitsToTwentyAndReturnsTheImmutableContract() throws Exception {
        mvc.perform(get("/v1/dashboard").header("Authorization", bearer(workspace.owner())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.generatedAt").isString())
                .andExpect(jsonPath("$.visibleSections.length()").value(5))
                .andExpect(jsonPath("$.visibleSections[0]").value("approval"))
                .andExpect(jsonPath("$.visibleSections[1]").value("publish"))
                .andExpect(jsonPath("$.visibleSections[2]").value("preview"))
                .andExpect(jsonPath("$.visibleSections[3]").value("export"))
                .andExpect(jsonPath("$.visibleSections[4]").value("survey"))
                .andExpect(jsonPath("$.summary.pendingApprovals").value(0))
                .andExpect(jsonPath("$.summary.publishExceptions").value(0))
                .andExpect(jsonPath("$.summary.activePreviews").value(0))
                .andExpect(jsonPath("$.summary.activeExports").value(0))
                .andExpect(jsonPath("$.tasks.length()").value(1))
                .andExpect(jsonPath("$.tasks[0].kind").value("draft_pending_publish"))
                .andExpect(jsonPath("$.tasks[0].targetPath")
                        .value("/surveys/" + survey.id() + "/publish"))
                .andExpect(jsonPath("$.surveys.length()").value(1))
                .andExpect(jsonPath("$.surveys[0].surveyId").value(survey.id().toString()))
                .andExpect(jsonPath("$.surveys[0].publishState").value("draft"))
                .andExpect(jsonPath("$.surveys[0].completedResponses").value(0))
                .andExpect(jsonPath("$.surveys[0].actions.length()").value(4))
                .andExpect(jsonPath("$.surveys[0].actions[0]").value("edit"))
                .andExpect(jsonPath("$.surveys[0].actions[1]").value("preview"))
                .andExpect(jsonPath("$.surveys[0].actions[2]").value("publish"))
                .andExpect(jsonPath("$.surveys[0].actions[3]").value("responses"))
                .andExpect(jsonPath("$.recentWork").isEmpty());
    }

    @ParameterizedTest
    @MethodSource("invalidLimits")
    void invalidLimitsUseOne422Contract(String query) throws Exception {
        mvc.perform(get("/v1/dashboard?" + query).header("Authorization", bearer(workspace.owner())))
                .andExpect(status().isUnprocessableContent())
                .andExpect(jsonPath("$.error").value("invalid_dashboard_request"))
                .andExpect(jsonPath("$.message").value("dashboard request is invalid"));
    }

    static Stream<Arguments> invalidLimits() {
        return Stream.of(
                Arguments.of("surveyLimit=0"),
                Arguments.of("surveyLimit=51"),
                Arguments.of("taskLimit=0"),
                Arguments.of("taskLimit=51"),
                Arguments.of("surveyLimit=abc"));
    }

    @Test
    void recentWorkUsesTheAuthenticatedTenantAndActorAndReturns204() throws Exception {
        ObjectNode body = json.createObjectNode();
        body.put("surveyId", survey.id().toString());
        body.put("page", "edit");
        body.putNull("version");

        mvc.perform(post("/v1/dashboard/recent-work")
                        .header("Authorization", bearer(workspace.owner()))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(json.writeValueAsString(body)))
                .andExpect(status().isNoContent());

        mvc.perform(get("/v1/dashboard").header("Authorization", bearer(workspace.owner())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.recentWork.length()").value(1))
                .andExpect(jsonPath("$.recentWork[0].surveyId").value(survey.id().toString()))
                .andExpect(jsonPath("$.recentWork[0].page").value("edit"))
                .andExpect(jsonPath("$.recentWork[0].targetPath")
                        .value("/surveys/" + survey.id() + "/edit"));
    }

    @Test
    void invalidRecentWorkAndInvisibleTargetsUseUniform422And404() throws Exception {
        ObjectNode invalid = json.createObjectNode();
        invalid.put("surveyId", survey.id().toString());
        invalid.put("page", "version");
        invalid.putNull("version");

        mvc.perform(post("/v1/dashboard/recent-work")
                        .header("Authorization", bearer(workspace.owner()))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(json.writeValueAsString(invalid)))
                .andExpect(status().isUnprocessableContent())
                .andExpect(jsonPath("$.error").value("invalid_dashboard_request"));

        Workspace other = fixture.workspace();
        ObjectNode invisible = json.createObjectNode();
        invisible.put("surveyId", survey.id().toString());
        invisible.put("page", "edit");
        invisible.putNull("version");

        mvc.perform(post("/v1/dashboard/recent-work")
                        .header("Authorization", bearer(other.owner()))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(json.writeValueAsString(invisible)))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").value("not_found"))
                .andExpect(jsonPath("$.message").value("dashboard target not found"));
    }

    @Test
    void nullRecentWorkBodyUsesTheSame422Contract() throws Exception {
        mvc.perform(post("/v1/dashboard/recent-work")
                        .header("Authorization", bearer(workspace.owner()))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("null"))
                .andExpect(status().isUnprocessableContent())
                .andExpect(jsonPath("$.error").value("invalid_dashboard_request"));
    }

    @Test
    void tokenRolesCannotGrantDashboardVisibility() throws Exception {
        String forged = "Bearer " + tokens.issue("not-a-member", workspace.tenant().value(), List.of("tenant_owner"));

        mvc.perform(get("/v1/dashboard").header("Authorization", forged))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("dashboard_forbidden"))
                .andExpect(jsonPath("$.message").value("dashboard access is forbidden"));
    }

    @Test
    void dependencyFailuresReturnOnlyTheStable503Body() {
        DashboardErrorHandler handler = new DashboardErrorHandler();

        var response = handler.unavailable(new DashboardUnavailableException(
                "secret database detail", new IllegalStateException("connection failed")));

        assertThat(response.getStatusCode().value()).isEqualTo(503);
        assertThat(response.getBody()).isEqualTo(new DashboardErrorHandler.DashboardError(
                "dashboard_temporarily_unavailable", "dashboard is temporarily unavailable"));
    }

    @Test
    void transactionBoundaryDataFailuresAlsoUseTheStable503Body() throws Exception {
        DashboardService service = mock(DashboardService.class);
        cn.mjy.platform.shared.security.CurrentTenant currentTenant =
                mock(cn.mjy.platform.shared.security.CurrentTenant.class);
        when(currentTenant.require()).thenReturn(workspace.owner());
        when(service.getDashboard(any(), anyInt(), anyInt()))
                .thenThrow(new DataAccessResourceFailureException("secret connection detail"));
        MockMvc isolated = MockMvcBuilders
                .standaloneSetup(new DashboardController(service, currentTenant))
                .setControllerAdvice(new DashboardErrorHandler())
                .build();

        isolated.perform(get("/v1/dashboard"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.error").value("dashboard_temporarily_unavailable"))
                .andExpect(jsonPath("$.message").value("dashboard is temporarily unavailable"));
    }

    private String bearer(TenantContext context) {
        return "Bearer " + tokens.issue(context.actorId(), context.tenantId().value(), List.of());
    }
}
