package cn.mjy.platform.access;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.support.TestTokens;
import com.jayway.jsonpath.JsonPath;
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

/** HTTP 层：租户只取自令牌；别的租户的 id 一律 404；列表只含调用者有权看到的本租户节点。 */
@SpringBootTest
@AutoConfigureMockMvc
class ResourceTreeApiTest {

    @Autowired
    private MockMvc mvc;

    @Autowired
    private AccessFixture fixture;

    @Autowired
    private TestTokens tokens;

    @Autowired
    private JsonMapper json;

    private TenantContext ownerA;
    private TenantContext ownerB;

    @BeforeEach
    void twoTenants() {
        ownerA = fixture.newTenant();
        ownerB = fixture.newTenant();
    }

    @Test
    void anOwnerBuildsATreeOverHttp() throws Exception {
        String project = createProject(ownerA, "项目").andExpect(status().isCreated())
                .andExpect(jsonPath("$.kind").value("project"))
                .andExpect(jsonPath("$.name").value("项目"))
                .andReturn().getResponse().getContentAsString();
        String projectId = JsonPath.read(project, "$.id");
        String folderId = idOf(createFolder(ownerA, projectId, "文件夹").andExpect(status().isCreated())
                .andExpect(jsonPath("$.parentId").value(projectId)));
        String targetId = idOf(createFolder(ownerA, projectId, "目标"));

        call(ownerA, patch("/v1/resources/" + folderId), Map.of("name", "改名后")).andExpect(status().isOk())
                .andExpect(jsonPath("$.name").value("改名后"));
        call(ownerA, post("/v1/resources/" + folderId + "/move"), Map.of("parentId", targetId))
                .andExpect(status().isOk()).andExpect(jsonPath("$.parentId").value(targetId));
        mvc.perform(get("/v1/resources/" + folderId).header("Authorization", bearer(ownerA)))
                .andExpect(status().isOk()).andExpect(jsonPath("$.parentId").value(targetId));
        mvc.perform(get("/v1/resources").param("parentId", targetId).header("Authorization", bearer(ownerA)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items.length()").value(1))
                .andExpect(jsonPath("$.items[0].id").value(folderId));
    }

    @Test
    void aNonAdminCannotCreateAProjectAndAViewerCannotCreateAFolderOrMove() throws Exception {
        String projectId = idOf(createProject(ownerA, "项目"));
        String a = idOf(createFolder(ownerA, projectId, "A"));
        String b = idOf(createFolder(ownerA, projectId, "B"));
        TenantContext viewer = fixture.member(ownerA, "viewer", "statistics_viewer", UUID.fromString(projectId));

        createProject(viewer, "不行").andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("no_matching_grant"));
        createFolder(viewer, projectId, "不行").andExpect(status().isForbidden());
        call(viewer, post("/v1/resources/" + a + "/move"), Map.of("parentId", b)).andExpect(status().isForbidden());
    }

    @Test
    void movingIntoOwnDescendantIs409() throws Exception {
        String projectId = idOf(createProject(ownerA, "项目"));
        String parent = idOf(createFolder(ownerA, projectId, "父"));
        String child = idOf(createFolder(ownerA, parent, "子"));

        call(ownerA, post("/v1/resources/" + parent + "/move"), Map.of("parentId", child))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error").value("move_into_descendant"));
    }

    @Test
    void otherTenantsNodesAreInvisibleAndUntouchable() throws Exception {
        String projectA = idOf(createProject(ownerA, "A 的项目"));
        String folderA = idOf(createFolder(ownerA, projectA, "A 的文件夹"));
        String projectB = idOf(createProject(ownerB, "B 的项目"));

        mvc.perform(get("/v1/resources").header("Authorization", bearer(ownerB)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items.length()").value(1))
                .andExpect(jsonPath("$.items[0].id").value(projectB));
        mvc.perform(get("/v1/resources").param("parentId", projectA).header("Authorization", bearer(ownerB)))
                .andExpect(status().isNotFound());
        mvc.perform(get("/v1/resources/" + folderA).header("Authorization", bearer(ownerB)))
                .andExpect(status().isNotFound());
        call(ownerB, patch("/v1/resources/" + folderA), Map.of("name", "劫持")).andExpect(status().isNotFound());
        call(ownerB, post("/v1/resources/" + folderA + "/move"), Map.of("parentId", projectB))
                .andExpect(status().isNotFound());
        call(ownerA, post("/v1/resources/" + folderA + "/move"), Map.of("parentId", projectB))
                .andExpect(status().isNotFound());
        createFolder(ownerB, projectA, "挂到 A 下").andExpect(status().isNotFound());

        mvc.perform(get("/v1/resources/" + folderA).header("Authorization", bearer(ownerA)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.name").value("A 的文件夹"))
                .andExpect(jsonPath("$.parentId").value(projectA));
    }

    @Test
    void theListPagesWithAnOpaqueCursorAndRejectsBadParameters() throws Exception {
        for (int i = 0; i < 3; i++) {
            createProject(ownerA, "P" + i).andExpect(status().isCreated());
        }

        String first = mvc.perform(get("/v1/resources").param("limit", "2").header("Authorization", bearer(ownerA)))
                .andExpect(status().isOk()).andExpect(jsonPath("$.items.length()").value(2))
                .andReturn().getResponse().getContentAsString();
        String cursor = JsonPath.read(first, "$.nextCursor");
        mvc.perform(get("/v1/resources").param("limit", "2").param("cursor", cursor)
                        .header("Authorization", bearer(ownerA)))
                .andExpect(status().isOk()).andExpect(jsonPath("$.items.length()").value(1))
                .andExpect(jsonPath("$.nextCursor").doesNotExist());

        mvc.perform(get("/v1/resources").param("cursor", "not-a-cursor").header("Authorization", bearer(ownerA)))
                .andExpect(status().isBadRequest());
        mvc.perform(get("/v1/resources").param("limit", "0").header("Authorization", bearer(ownerA)))
                .andExpect(status().isBadRequest());
        createProject(ownerA, " ").andExpect(status().isBadRequest());
    }

    @Test
    void aTokenWithoutTenantIsRejected() throws Exception {
        mvc.perform(get("/v1/resources").header("Authorization", "Bearer " + tokens.issueWithoutTenant("x")))
                .andExpect(status().isForbidden());
        mvc.perform(get("/v1/resources")).andExpect(status().isUnauthorized());
    }

    // ------------------------------------------------------------ helpers

    private String bearer(TenantContext ctx) {
        return "Bearer " + tokens.issue(ctx.actorId(), ctx.tenantId().value(), List.of());
    }

    private ResultActions createProject(TenantContext ctx, String name) throws Exception {
        return call(ctx, post("/v1/projects"), Map.of("name", name));
    }

    private ResultActions createFolder(TenantContext ctx, String parentId, String name) throws Exception {
        return call(ctx, post("/v1/folders"), Map.of("parentId", parentId, "name", name));
    }

    private ResultActions call(TenantContext ctx, MockHttpServletRequestBuilder request, Map<String, String> body)
            throws Exception {
        return mvc.perform(request.header("Authorization", bearer(ctx))
                .contentType(MediaType.APPLICATION_JSON).content(json.writeValueAsString(body)));
    }

    private static String idOf(ResultActions result) throws Exception {
        String id = JsonPath.read(result.andReturn().getResponse().getContentAsString(), "$.id");
        assertThat(id).isNotBlank();
        return id;
    }
}
