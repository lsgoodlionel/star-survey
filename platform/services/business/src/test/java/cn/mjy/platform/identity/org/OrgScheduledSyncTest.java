package cn.mjy.platform.identity.org;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.shared.TenantId;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.ApplicationContext;

/**
 * 分页与定时全量同步：按 (建立时间, 主体) 键集分页（测试里每页 {@value #SYNC_BATCH_SIZE} 条），跨页不漏不重；
 * 定时一轮逐个未关闭租户的启用连接，只在明确"离职 / 禁用 / 不存在"时撤权；测试环境不装配调度器。
 */
class OrgScheduledSyncTest extends OrgLoginTestSupport {

    @Autowired
    private OrgDirectorySyncService sync;

    @Autowired
    private ApplicationContext context;

    private List<String> signInAll(Org org, int count) throws Exception {
        List<String> tokens = new ArrayList<>();
        for (int i = 0; i < count; i++) {
            String userId = "p" + i + unique("u");
            FAKE.addUser(org.provider(), org.corp(), userId);
            tokens.add(login(org, userId));
        }
        return tokens;
    }

    private String userIdOf(Org org, String token) {
        String principal = subject(token);
        return tenantScope.call(org.tenant(), () -> jdbc
                .sql("SELECT external_id FROM identity_binding WHERE principal_id = CAST(:p AS uuid)")
                .param("p", principal).query(String.class).single());
    }

    private boolean signedIn(String token) throws Exception {
        return mvc.perform(get("/v1/me").header("Authorization", "Bearer " + token))
                .andReturn().getResponse().getStatus() == 200;
    }

    @Test
    void anAdminSyncWalksEveryPage() throws Exception {
        Org org = connect(newTenant(), "dingtalk", "jit_staff");
        List<String> tokens = signInAll(org, 5);
        FAKE.setStatus("dingtalk", org.corp(), userIdOf(org, tokens.get(1)), FakeOrgPlatforms.Status.LEFT);
        FAKE.setStatus("dingtalk", org.corp(), userIdOf(org, tokens.get(4)), FakeOrgPlatforms.Status.DELETED);

        mvc.perform(post("/v1/org-connections/" + org.connectionId() + "/sync")
                        .header("Authorization", ownerBearer(org.tenant())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.checked").value(5))
                .andExpect(jsonPath("$.revoked").value(2))
                .andExpect(jsonPath("$.unknown").value(0));

        assertThat(signedIn(tokens.get(0))).isTrue();
        assertThat(signedIn(tokens.get(1))).isFalse();
        assertThat(signedIn(tokens.get(3))).isTrue();
        assertThat(signedIn(tokens.get(4))).isFalse();
    }

    @Test
    void aScheduledRoundRevokesDepartedMembersOfEnabledConnectionsOnly() throws Exception {
        TenantId tenant = newTenant();
        Org enabled = connect(tenant, "feishu", "jit_staff");
        Org disabled = connect(tenant, "wecom", "jit_staff");
        List<String> kept = signInAll(enabled, 3);
        String leaverOfDisabled = signInAll(disabled, 1).getFirst();
        FAKE.setStatus("feishu", enabled.corp(), userIdOf(enabled, kept.get(2)), FakeOrgPlatforms.Status.LEFT);
        FAKE.setStatus("wecom", disabled.corp(), userIdOf(disabled, leaverOfDisabled), FakeOrgPlatforms.Status.LEFT);
        disable(disabled);

        OrgDirectorySyncService.SyncResult result = sync.runScheduledRound();

        assertThat(result.checked()).isGreaterThanOrEqualTo(3);
        assertThat(result.revoked()).isGreaterThanOrEqualTo(1);
        assertThat(signedIn(kept.get(0))).isTrue();
        assertThat(signedIn(kept.get(2))).isFalse();
        assertThat(tenantScope.call(tenant, () -> jdbc.sql("""
                        SELECT COUNT(*) FROM identity_binding_revocation r JOIN identity_binding b
                          ON b.principal_id = r.principal_id WHERE b.provider = 'wecom'
                        """).query(Long.class).single())).isZero();
    }

    @Test
    void theSchedulerIsNotWiredWhenDisabled() {
        assertThat(context.getBeanNamesForType(OrgDirectorySyncScheduler.class)).isEmpty();
    }
}
