package cn.mjy.platform.identity.org;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.shared.TenantId;
import com.jayway.jsonpath.JsonPath;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.boot.test.system.CapturedOutput;
import org.springframework.boot.test.system.OutputCaptureExtension;
import org.springframework.http.MediaType;
import org.springframework.mock.env.MockEnvironment;

/** Real service, PostgreSQL and file resolver; provider HTTP endpoints are local fakes. */
@ExtendWith(OutputCaptureExtension.class)
class OrgFileSecretTenantIsolationTest extends OrgLoginTestSupport {
    @Test
    void twoTenantsCannotBorrowFilesConnectionsPermissionsOrAudit(@TempDir Path directory, CapturedOutput output)
            throws Exception {
        TenantId a = newTenant();
        TenantId b = newTenant();
        String ref = "ISOLATED_REF";
        String secretA = "synthetic-a-" + UUID.randomUUID();
        String secretB = "synthetic-b-" + UUID.randomUUID();
        String corpA = unique("corpA"), appA = unique("appA");
        String corpB = unique("corpB"), appB = unique("appB");
        FAKE.registerApp("feishu", corpA, appA, secretA);
        FAKE.registerApp("feishu", corpB, appB, secretB);
        var resolver = new EnvironmentSecretResolver(new MockEnvironment()
                .withProperty("PLATFORM_ORG_SECRET_DIR", directory.toString()));
        secrets.useResolver(a, resolver);
        secrets.useResolver(b, resolver);
        try {
            Files.writeString(directory.resolve(EnvironmentSecretResolver.variableName(a, ref)), secretA + "\n");
            String aJson = createConnection(a, "feishu", corpA, appA, ref, "jit_staff")
                    .andExpect(status().isCreated()).andReturn().getResponse().getContentAsString();
            String bJson = createConnection(b, "feishu", corpB, appB, ref, "jit_staff")
                    .andExpect(status().isCreated()).andReturn().getResponse().getContentAsString();
            UUID aId = UUID.fromString(JsonPath.read(aJson, "$.id"));
            UUID bId = UUID.fromString(JsonPath.read(bJson, "$.id"));
            assertThat(aJson + bJson).doesNotContain(secretA, secretB);
            assertThat(resolver.resolve(a, ref)).contains(secretA);
            assertThat(resolver.resolve(b, ref)).isEmpty();

            mvc.perform(get("/v1/org-connections/" + aId).header("Authorization", ownerBearer(b)))
                    .andExpect(status().isNotFound());
            mvc.perform(put("/v1/org-connections/" + aId).header("Authorization", ownerBearer(b))
                            .contentType(MediaType.APPLICATION_JSON).content("{\"enabled\":false}"))
                    .andExpect(status().isNotFound());
            mvc.perform(get("/v1/org-connections").header("Authorization", bearer(a, "not-a-member")))
                    .andExpect(status().isForbidden());
            Started missing = start(b, bId);
            String missingResponse = callback(b, bId, FAKE.issueCode("feishu", corpB, appB, unique("user")), missing)
                    .andExpect(status().isServiceUnavailable()).andExpect(jsonPath("$.error").value("not_configured"))
                    .andReturn().getResponse().getContentAsString();
            assertThat(missingResponse).doesNotContain(secretA, secretB);
            assertThat(sessionCount(b)).isZero();

            Path bFile = directory.resolve(EnvironmentSecretResolver.variableName(b, ref));
            Files.writeString(bFile, secretA + "\n");
            Started wrong = start(b, bId);
            String wrongResponse = callback(b, bId, FAKE.issueCode("feishu", corpB, appB, unique("user")), wrong)
                    .andExpect(status().isBadGateway()).andReturn().getResponse().getContentAsString();
            assertThat(wrongResponse).doesNotContain(secretA, secretB);
            assertThat(sessionCount(b)).isZero();
            Files.writeString(bFile, secretB + "\n");
            assertThat(resolver.resolve(a, ref)).contains(secretA);
            assertThat(resolver.resolve(b, ref)).contains(secretB);
            Org orgA = new Org(a, "feishu", corpA, appA, secretA, ref, aId);
            Org orgB = new Org(b, "feishu", corpB, appB, secretB, ref, bId);
            assertThat(login(orgA, unique("user"))).isNotBlank();
            assertThat(login(orgB, unique("user"))).isNotBlank();
            assertThat(sessionCount(a)).isEqualTo(1);
            assertThat(sessionCount(b)).isEqualTo(1);
            List<String> auditA = audit(a);
            List<String> auditB = audit(b);
            assertThat(auditA).anyMatch(row -> row.contains("org_connection/" + aId));
            assertThat(auditB).anyMatch(row -> row.contains("org_connection/" + bId));
            assertThat(String.join("\n", auditA)).doesNotContain(bId.toString(), secretA, secretB);
            assertThat(String.join("\n", auditB)).doesNotContain(aId.toString(), secretA, secretB);
            assertThat(output.getAll()).doesNotContain(secretA, secretB);
        } finally {
            secrets.clearResolver(a);
            secrets.clearResolver(b);
        }
    }

    private long sessionCount(TenantId tenant) {
        return tenantScope.call(tenant, () -> jdbc.sql("SELECT count(*) FROM org_login_session")
                .query(Long.class).single());
    }

    private List<String> audit(TenantId tenant) {
        return tenantScope.call(tenant, () -> jdbc.sql("SELECT action || ' ' || resource FROM audit_log")
                .query(String.class).list());
    }
}
