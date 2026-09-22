package cn.mjy.platform.identity.org;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.tenant.TenantService;
import cn.mjy.platform.tenant.TenantStatus;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;

/** 停用（suspended）或关闭的租户不能免登：发起与回调都 403 tenant_unavailable；恢复为 active 后照常。 */
class OrgTenantStatusTest extends OrgLoginTestSupport {

    @Autowired
    private TenantService tenantService;

    private void changeStatus(TenantId tenant, TenantStatus status) {
        tenantService.changeStatus(tenant, status, "operator-1", "trace-status");
    }

    @Test
    void aSuspendedTenantCannotStartOrCompleteASignIn() throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, "feishu", "jit_staff");
        String userId = unique("u");
        Started started = start(tenant, org.connectionId());

        changeStatus(tenant, TenantStatus.SUSPENDED);

        mvc.perform(get(startPath(tenant, org.connectionId())))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value(OrgLoginException.TENANT_UNAVAILABLE));
        callback(tenant, org.connectionId(), FAKE.issueCode("feishu", org.corp(), org.appId(), userId), started)
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value(OrgLoginException.TENANT_UNAVAILABLE));

        changeStatus(tenant, TenantStatus.ACTIVE);
        // state 在停用期间没有被消耗，恢复后同一次登录可以完成。
        callback(tenant, org.connectionId(), FAKE.issueCode("feishu", org.corp(), org.appId(), userId), started)
                .andExpect(status().isOk());
    }

    @Test
    void aClosedTenantCannotSignIn() throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, "wecom", "jit_staff");

        changeStatus(tenant, TenantStatus.CLOSED);

        mvc.perform(get(startPath(tenant, org.connectionId()))).andExpect(status().isForbidden());
    }
}
