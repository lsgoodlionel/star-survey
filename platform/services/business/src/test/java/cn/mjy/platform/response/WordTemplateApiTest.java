package cn.mjy.platform.response;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.response.ExportFixture.Member;
import cn.mjy.platform.response.ResponseFixture.Published;
import cn.mjy.platform.support.TestTokens;
import com.jayway.jsonpath.JsonPath;
import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest
@AutoConfigureMockMvc
class WordTemplateApiTest {
    @Autowired MockMvc mvc;
    @Autowired ExportFixture fixture;
    @Autowired TestTokens tokens;
    @Autowired WordTemplateService templates;

    @Test
    void uploadCreatesImmutableVersionsAndRejectsInvalidContent() throws Exception {
        Published p = fixture.responses().publishedSurvey();
        String auth = "Bearer " + tokens.issue(p.owner().actorId(), p.tenant().value(), List.of());
        String url = "/v1/surveys/" + p.surveyId() + "/word-templates";
        var first = mvc.perform(multipart(url).file(file("{{response.id}}"))
                        .param("name", "报告模板").header("Authorization", auth))
                .andExpect(status().isCreated()).andExpect(jsonPath("$.version").value(1))
                .andExpect(jsonPath("$.sha256").isNotEmpty()).andReturn();
        String id = JsonPath.read(first.getResponse().getContentAsString(), "$.id");
        mvc.perform(multipart(url + "/" + id + "/versions").file(file("{{response.answers}}"))
                        .header("Authorization", auth))
                .andExpect(status().isCreated()).andExpect(jsonPath("$.version").value(2));
        mvc.perform(get(url).header("Authorization", auth))
                .andExpect(status().isOk()).andExpect(jsonPath("$[0].version").value(2));
        mvc.perform(get(url).queryParam("limit", "1").queryParam("offset", "1")
                        .header("Authorization", auth))
                .andExpect(status().isOk()).andExpect(jsonPath("$").isArray())
                .andExpect(jsonPath("$.length()").value(0));
        mvc.perform(get(url).queryParam("limit", "101").header("Authorization", auth))
                .andExpect(status().isBadRequest());
        mvc.perform(get(url + "/" + id).header("Authorization", auth))
                .andExpect(status().isOk()).andExpect(jsonPath("$.id").value(id))
                .andExpect(jsonPath("$.name").value("报告模板"))
                .andExpect(jsonPath("$.version").value(2));
        mvc.perform(get(url + "/" + id + "/versions/1").header("Authorization", auth))
                .andExpect(status().isOk()).andExpect(jsonPath("$.id").value(id))
                .andExpect(jsonPath("$.version").value(1));
        mvc.perform(multipart(url).file(file("{{tenant.secret}}"))
                        .param("name", "无效").header("Authorization", auth))
                .andExpect(status().isBadRequest());

        Member stats = fixture.member(p, "template-stats", "statistics_viewer");
        String statsAuth = "Bearer " + tokens.issue(stats.ctx().actorId(), p.tenant().value(), List.of());
        mvc.perform(get(url).header("Authorization", statsAuth)).andExpect(status().isForbidden());
        mvc.perform(multipart(url).file(file("{{response.id}}"))
                        .param("name", "越权模板").header("Authorization", statsAuth))
                .andExpect(status().isForbidden());
        mvc.perform(multipart(url).file(file("{{tenant.secret}}"))
                        .param("name", "越权无效模板").header("Authorization", statsAuth))
                .andExpect(status().isForbidden());

        Published other = fixture.responses().publishedSurvey();
        String otherAuth = "Bearer " + tokens.issue(other.owner().actorId(), other.tenant().value(), List.of());
        mvc.perform(multipart(url + "/" + id + "/versions").file(file("{{response.id}}"))
                        .header("Authorization", otherAuth)).andExpect(status().isNotFound());
    }

    @Test
    void enforcesTemplateCountQuotaAndMetadataPagination() throws Exception {
        Published p = fixture.responses().publishedSurvey();
        byte[] content = file("{{response.id}}").getBytes();
        for (int i = 0; i < WordTemplateService.MAX_TEMPLATES_PER_SURVEY; i++) {
            templates.create(p.owner(), p.surveyId(), "模板-" + i, content);
        }

        assertThat(templates.list(p.owner(), p.surveyId(), 7, 3)).hasSize(7)
                .allSatisfy(view -> assertThat(view.byteSize()).isEqualTo(content.length));
        assertThatThrownBy(() -> templates.create(p.owner(), p.surveyId(), "超限模板", content))
                .isInstanceOf(InvalidResponseQueryException.class)
                .hasMessageContaining("最多可保存 100 个");
    }

    @Test
    void concurrentUploadsCannotExceedTheVersionQuota() throws Exception {
        Published p = fixture.responses().publishedSurvey();
        byte[] content = file("{{response.id}}").getBytes();
        WordTemplateView template = templates.create(p.owner(), p.surveyId(), "并发版本模板", content);
        for (int version = 2; version < WordTemplateService.MAX_VERSIONS_PER_TEMPLATE; version++) {
            templates.addVersion(p.owner(), p.surveyId(), template.id(), content);
        }

        CompletableFuture<Object> first = CompletableFuture.supplyAsync(
                () -> addVersionOutcome(p, template.id(), content));
        CompletableFuture<Object> second = CompletableFuture.supplyAsync(
                () -> addVersionOutcome(p, template.id(), content));
        List<Object> outcomes = List.of(first.join(), second.join());

        assertThat(outcomes).filteredOn(WordTemplateView.class::isInstance).hasSize(1);
        assertThat(outcomes).filteredOn(InvalidResponseQueryException.class::isInstance).singleElement()
                .satisfies(error -> assertThat((RuntimeException) error).hasMessageContaining("最多可保存 100 个版本"));
        assertThat(templates.get(p.owner(), p.surveyId(), template.id(), null).version())
                .isEqualTo(WordTemplateService.MAX_VERSIONS_PER_TEMPLATE);
    }

    private Object addVersionOutcome(Published p, UUID id, byte[] content) {
        try {
            return templates.addVersion(p.owner(), p.surveyId(), id, content);
        } catch (RuntimeException error) {
            return error;
        }
    }

    private static MockMultipartFile file(String variable) throws Exception {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        try (ZipOutputStream zip = new ZipOutputStream(out)) {
            entry(zip, "[Content_Types].xml", "<Types/>");
            entry(zip, "_rels/.rels", "<Relationships/>");
            entry(zip, "word/document.xml", "<document>" + variable + "</document>");
        }
        return new MockMultipartFile("file", "template.docx", "application/octet-stream", out.toByteArray());
    }
    private static void entry(ZipOutputStream zip, String name, String value) throws Exception {
        zip.putNextEntry(new ZipEntry(name));
        zip.write(value.getBytes(StandardCharsets.UTF_8));
        zip.closeEntry();
    }
}
