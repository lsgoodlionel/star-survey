package cn.mjy.platform.contacts;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.support.TestTokens;
import cn.mjy.platform.survey.SurveyFixture;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import tools.jackson.databind.json.JsonMapper;

/**
 * 受众 HTTP 接口：读写都要同时过两道判定——通讯录的数据范围，以及<b>这份问卷本身可不可见</b>。
 * 少了后者，任何持通讯录角色的人都能按 surveyId 枚举出"这份问卷邀请了哪个名单/部门/标签、多少人"。
 */
@SpringBootTest
@AutoConfigureMockMvc
class SurveyAudienceApiTest {

    @Autowired
    private MockMvc mvc;

    @Autowired
    private SurveyFixture surveys;

    @Autowired
    private ContactsFixture fixture;

    @Autowired
    private ContactDirectoryService contacts;

    @Autowired
    private TestTokens tokens;

    @Autowired
    private JsonMapper json;

    private Workspace ws;
    private UUID survey;
    private UUID list;

    @BeforeEach
    void aDraftSurveyAndAContactList() {
        ws = surveys.workspace();
        survey = surveys.newSurvey(ws).id();
        list = fixture.list(ws.owner(), "受众 " + UUID.randomUUID(), ContactKind.RESPONDENT, DedupeKey.EMAIL).id();
        contacts.create(ws.owner(), list, ContactKind.RESPONDENT,
                ContactDraft.named("甲").withEmail("ann@example.com"));
    }

    private String audienceUrl() {
        return "/v1/surveys/" + survey + "/audience";
    }

    @Test
    void theAudienceCanBeSetReadAndCleared() throws Exception {
        call(ws.owner(), put(audienceUrl()), Map.of("listId", list.toString()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.audience.listId").value(list.toString()))
                .andExpect(jsonPath("$.recipients").value(1));

        mvc.perform(get(audienceUrl()).header("Authorization", bearer(ws.owner())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.recipients").value(1));

        mvc.perform(delete(audienceUrl()).header("Authorization", bearer(ws.owner())))
                .andExpect(status().isNoContent());
        mvc.perform(get(audienceUrl()).header("Authorization", bearer(ws.owner())))
                .andExpect(status().isNotFound());
    }

    @Test
    void anAudienceWithoutAnyCriterionIsRejected() throws Exception {
        call(ws.owner(), put(audienceUrl()), Map.of())
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("invalid_request"));
    }

    @Test
    void anotherTenantCanNeitherReadNorSetTheAudience() throws Exception {
        call(ws.owner(), put(audienceUrl()), Map.of("listId", list.toString())).andExpect(status().isOk());
        TenantContext stranger = fixture.newTenant();

        mvc.perform(get(audienceUrl()).header("Authorization", bearer(stranger)))
                .andExpect(status().isNotFound());
        call(stranger, put(audienceUrl()), Map.of("listId", list.toString()))
                .andExpect(status().isNotFound());
        mvc.perform(delete(audienceUrl()).header("Authorization", bearer(stranger)))
                .andExpect(status().isNotFound());
    }

    /** 同租户、看得见问卷，但没有通讯录权限：读不到受众，也就读不到"邀请了谁"。 */
    @Test
    void aMemberWithoutContactsRightsIsRefused() throws Exception {
        call(ws.owner(), put(audienceUrl()), Map.of("listId", list.toString())).andExpect(status().isOk());
        TenantContext plain = fixture.plainMember(ws.owner(), "audience-outsider");

        mvc.perform(get(audienceUrl()).header("Authorization", bearer(plain)))
                .andExpect(status().isForbidden());
        call(plain, put(audienceUrl()), Map.of("listId", list.toString()))
                .andExpect(status().isForbidden());
    }

    /**
     * 受众只能挂在问卷上。资源树里的项目 / 文件夹即使调用者看得见，也不是问卷——
     * 读写都必须先确认"这是一份我看得见的问卷"，而不是直接查受众表。
     */
    @Test
    void aResourceThatIsNotASurveyHasNoAudience() throws Exception {
        String projectUrl = "/v1/surveys/" + ws.project() + "/audience";

        mvc.perform(get(projectUrl).header("Authorization", bearer(ws.owner())))
                .andExpect(status().isNotFound());
        mvc.perform(put(projectUrl).header("Authorization", bearer(ws.owner()))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(json.writeValueAsString(Map.of("listId", list.toString()))))
                .andExpect(status().isNotFound());
    }

    /**
     * 不存在的、以及别的租户的 surveyId 都拿不到受众。
     *
     * <p>注意这一条在 HTTP 层<b>两种实现都返回 404</b>（控制器把"没有受众"也映射成 404，跨租户另有行级安全），
     * 所以它证明的是接口对外的形状，不是守卫本身——守卫的红在 {@code SurveyAudienceTest} 那条里。
     */
    @Test
    void noAudienceComesBackForASurveyTheCallerCannotSee() throws Exception {
        UUID theirSurvey = surveys.newSurvey(surveys.workspace()).id();

        mvc.perform(get("/v1/surveys/" + UUID.randomUUID() + "/audience")
                        .header("Authorization", bearer(ws.owner())))
                .andExpect(status().isNotFound());
        mvc.perform(get("/v1/surveys/" + theirSurvey + "/audience")
                        .header("Authorization", bearer(ws.owner())))
                .andExpect(status().isNotFound());
    }

    private String bearer(TenantContext ctx) {
        return "Bearer " + tokens.issue(ctx.actorId(), ctx.tenantId().value(), List.of());
    }

    private ResultActions call(TenantContext ctx, MockHttpServletRequestBuilder request, Map<String, String> body)
            throws Exception {
        return mvc.perform(request.header("Authorization", bearer(ctx))
                .contentType(MediaType.APPLICATION_JSON).content(json.writeValueAsString(body)));
    }
}
