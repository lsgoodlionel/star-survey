package cn.mjy.platform.asset;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.support.TestTokens;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

/**
 * 拉取端点的鉴权与错误映射。
 *
 * <p>两件容易在接线时漏掉的事：
 * <ol>
 *   <li>{@link ResponderUploadsUnavailableException} 必须落到资产模块的错误映射里变成 <b>503</b>。
 *       落到没人接的地方就是 500——「失败即关闭」照样成立（确实没拉进来），但状态码骗人。</li>
 *   <li>引擎坐标是调用方给的，<b>必须核对归属</b>（独立安全审查的 CRITICAL）。
 *       服务层的用例见 {@link ResponderUploadTenantIsolationTest}，这里守 HTTP 这一层。</li>
 * </ol>
 */
@AssetIntegrationTest
@AutoConfigureMockMvc
class ResponderUploadApiTest {

    @Autowired
    private MockMvc mvc;

    @Autowired
    private AssetFixture fixture;

    @Autowired
    private TestTokens tokens;

    private TenantContext owner;
    private String instance;

    @BeforeEach
    void seed() {
        AssetFixture.EngineWorkspace ws = fixture.newTenantWithEngine();
        owner = ws.owner();
        instance = ws.engineInstanceId();
    }

    /** 测试配置里没有网关地址，所以拉取必然以 unavailable 收场——正好用来看状态码。 */
    @Test
    void pullingWithoutAgatewayIsAserviceUnavailableNotAnInternalError() throws Exception {
        mvc.perform(post("/v1/assets/uploads/pull")
                        .header("Authorization", bearer(owner))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body(instance)))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.error").value("responder_uploads_unavailable"));
    }

    /** 点名不属于本租户的引擎：404，而且在出网之前就挡住。 */
    @Test
    void pullingFromAnEngineThatIsNotYoursIsANotFound() throws Exception {
        String someoneElses = fixture.newTenantWithEngine().engineInstanceId();

        mvc.perform(post("/v1/assets/uploads/pull")
                        .header("Authorization", bearer(owner))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body(someoneElses)))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").value("not_found"));
    }

    /** 拉取是写操作，必须带平台令牌。 */
    @Test
    void pullingWithoutAtokenIsUnauthorized() throws Exception {
        mvc.perform(post("/v1/assets/uploads/pull")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body("any-engine")))
                .andExpect(status().isUnauthorized());
    }

    private static String body(String engineInstanceId) {
        return "{\"engineInstanceId\":\"" + engineInstanceId
                + "\",\"engineSid\":4242,\"generation\":\"gen-1\",\"responseIds\":[7]}";
    }

    private String bearer(TenantContext ctx) {
        return "Bearer " + tokens.issue(ctx.actorId(), ctx.tenantId().value(), List.of());
    }
}
