package cn.mjy.platform.identity.org;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.shared.TenantId;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.springframework.boot.test.system.CapturedOutput;
import org.springframework.boot.test.system.OutputCaptureExtension;

/**
 * 密钥（应用 Secret）与开放平台下发的凭据（授权码、access_token）不出现在任何响应、日志、审计与数据库里。
 * 走完整生命周期（建连接、预授权、登录、失败登录、同步）后逐一检查。
 */
@ExtendWith(OutputCaptureExtension.class)
class OrgSecretHygieneTest extends OrgLoginTestSupport {

    @Test
    void secretsNeverAppearInResponsesLogsAuditOrTables(CapturedOutput output) throws Exception {
        TenantId tenant = newTenant();
        List<String> responses = new ArrayList<>();
        List<String> secrets = new ArrayList<>();
        for (String provider : List.of("wecom", "dingtalk", "feishu")) {
            Org org = connect(tenant, provider, "jit_staff");
            secrets.add(org.secret());
            String userId = unique("u");
            FAKE.addUser(provider, org.corp(), userId);
            responses.add(mvc.perform(get("/v1/org-connections/" + org.connectionId())
                    .header("Authorization", ownerBearer(tenant))).andReturn().getResponse().getContentAsString());
            Started started = start(tenant, org.connectionId());
            responses.add(started.location().toString());
            responses.add(callback(tenant, org.connectionId(),
                    FAKE.issueCode(provider, org.corp(), org.appId(), userId), started)
                    .andReturn().getResponse().getContentAsString());
            Started failing = start(tenant, org.connectionId());
            responses.add(callback(tenant, org.connectionId(), "bogus-code", failing)
                    .andReturn().getResponse().getContentAsString());
            FAKE.failNextWith(500);
            responses.add(mvc.perform(post("/v1/org-connections/" + org.connectionId() + "/sync")
                    .header("Authorization", ownerBearer(tenant))).andExpect(status().isOk())
                    .andReturn().getResponse().getContentAsString());
        }
        responses.add(mvc.perform(get("/v1/org-connections").header("Authorization", ownerBearer(tenant)))
                .andReturn().getResponse().getContentAsString());
        String audit = String.join("\n", tenantScope.call(tenant, () -> jdbc
                .sql("SELECT actor_id || ' ' || action || ' ' || resource FROM audit_log").query(String.class).list()));
        String tables = String.join("\n", tenantScope.call(tenant, () -> jdbc
                .sql("SELECT row_to_json(c)::text FROM org_connection c").query(String.class).list()));
        List<String> providerTokens = FAKE.requestLog().stream()
                .flatMap(line -> java.util.Arrays.stream(line.split("[^A-Za-z0-9]")))
                .filter(word -> word.startsWith("tok") || word.startsWith("code"))
                .filter(word -> word.length() > 20)
                .toList();

        assertThat(audit).contains("identity.org_login");
        for (String secret : secrets) {
            assertThat(String.join("\n", responses)).doesNotContain(secret);
            assertThat(output.getAll()).doesNotContain(secret);
            assertThat(audit).doesNotContain(secret);
            assertThat(tables).doesNotContain(secret);
        }
        assertThat(providerTokens).isNotEmpty();
        for (String credential : providerTokens) {
            assertThat(output.getAll()).doesNotContain(credential);
            assertThat(audit).doesNotContain(credential);
        }
    }
}
