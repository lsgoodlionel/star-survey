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
 * 拉取端点的错误映射与鉴权。
 *
 * <p>钉住的是一条容易在接线时漏掉的事：{@link ResponderUploadsUnavailableException}
 * 必须落到资产模块的错误映射里变成 <b>503</b>。落到没人接的地方就是 500——
 * 「失败即关闭」照样成立（确实没拉进来），但状态码骗人，
 * 而上一轮独立安全审查抓到的正是这一类（ADR 0019 审查表里的 MEDIUM 那条）。
 */
@AssetIntegrationTest
@AutoConfigureMockMvc
class ResponderUploadApiTest {

    private static final String BODY = """
            {"engineInstanceId":"engine-a","engineSid":4242,"generation":"gen-1","responseIds":[7]}
            """;

    @Autowired
    private MockMvc mvc;

    @Autowired
    private AssetFixture fixture;

    @Autowired
    private TestTokens tokens;

    private TenantContext owner;

    @BeforeEach
    void seed() {
        owner = fixture.newTenant();
    }

    /** 测试配置里没有网关地址，所以拉取必然以 unavailable 收场——正好用来看状态码。 */
    @Test
    void pullingWithoutAgatewayIsAserviceUnavailableNotAnInternalError() throws Exception {
        mvc.perform(post("/v1/assets/uploads/pull")
                        .header("Authorization", bearer(owner))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(BODY))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.error").value("responder_uploads_unavailable"));
    }

    /** 拉取是写操作，必须带平台令牌。 */
    @Test
    void pullingWithoutAtokenIsUnauthorized() throws Exception {
        mvc.perform(post("/v1/assets/uploads/pull")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(BODY))
                .andExpect(status().isUnauthorized());
    }

    private String bearer(TenantContext ctx) {
        return "Bearer " + tokens.issue(ctx.actorId(), ctx.tenantId().value(), List.of());
    }
}
