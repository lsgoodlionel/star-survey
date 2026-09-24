package cn.mjy.platform.response;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.access.AccessFixture;
import cn.mjy.platform.access.GrantRequest;
import cn.mjy.platform.response.ResponseFixture.Published;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

/** 导出测试的公共场景：建作业、驱动后台处理、读回文件并解析 CSV 包。 */
@Component
public class ExportFixture {

    public static final String UPLOAD_FIELD = "U900";
    public static final String URL_FIELD = "U901";

    private static final AtomicInteger NEXT_SID = new AtomicInteger(990_000);

    private final ResponseFixture responses;
    private final AccessFixture access;
    private final ResponseExportService exports;
    private final ResponseExportWorker worker;
    private final TenantScope tenantScope;
    private final JdbcClient jdbc;

    public ExportFixture(ResponseFixture responses, AccessFixture access, ResponseExportService exports,
            ResponseExportWorker worker, TenantScope tenantScope, JdbcClient jdbc) {
        this.responses = responses;
        this.access = access;
        this.exports = exports;
        this.worker = worker;
        this.tenantScope = tenantScope;
        this.jdbc = jdbc;
    }

    public ResponseFixture responses() {
        return responses;
    }

    /** 一个成员及其授权 id（撤权用）。 */
    public record Member(TenantContext ctx, UUID grantId) {
    }

    public Member member(Published p, String actor, String role) {
        TenantContext ctx = access.activeMember(p.owner(), actor);
        UUID grant = access.grants().grant(p.owner(), new GrantRequest(actor, role, p.surveyId(), null));
        return new Member(ctx, grant);
    }

    /** 给已在职的成员再加一条授权。 */
    public Member grant(Published p, TenantContext member, String role) {
        UUID grant = access.grants().grant(p.owner(), new GrantRequest(member.actorId(), role, p.surveyId(), null));
        return new Member(member, grant);
    }

    public void revoke(Published p, Member member) {
        access.grants().revoke(p.owner(), member.grantId());
    }

    public ExportJobView create(TenantContext ctx, Published p, String format) {
        return exports.create(ctx, p.surveyId(), new ExportRequest(null, format, null, null), null);
    }

    /** 让后台把作业一次跑完，返回最终状态。 */
    public ExportJobView runToEnd(TenantContext ctx, ExportJobView job) {
        worker.process(ctx.tenantId(), job.jobId(), Integer.MAX_VALUE);
        return exports.status(ctx, job.jobId());
    }

    public byte[] download(TenantContext ctx, ExportJobView job) {
        ExportDownload download = exports.download(ctx, job.jobId());
        try (InputStream in = download.content()) {
            return in.readAllBytes();
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    /** 把作业的租约置为已过期，模拟执行者进程死掉后租约超时。 */
    public void expireLease(TenantId tenant, UUID jobId) {
        tenantScope.run(tenant, () -> jdbc.sql(
                        "UPDATE response_export_job SET lease_until = now() - interval '1 second' WHERE id = :id")
                .param("id", jobId).update());
    }

    public void expireJob(TenantId tenant, UUID jobId) {
        tenantScope.run(tenant, () -> jdbc.sql("""
                        UPDATE response_export_job
                           SET created_at = now() - interval '10 days', expires_at = now() - interval '1 second'
                         WHERE id = :id
                        """)
                .param("id", jobId).update());
    }

    /** CSV 包：文件名 → 行（每行已按 CSV 规则拆成单元格）。 */
    public static Map<String, List<List<String>>> unzipCsv(byte[] zip) {
        Map<String, List<List<String>>> files = new LinkedHashMap<>();
        try (ZipInputStream in = new ZipInputStream(new java.io.ByteArrayInputStream(zip))) {
            ZipEntry entry;
            while ((entry = in.getNextEntry()) != null) {
                byte[] bytes = in.readAllBytes();
                assertThat(bytes).as("UTF-8 BOM on " + entry.getName()).startsWith(0xEF, 0xBB, 0xBF);
                files.put(entry.getName(), parseCsv(new String(bytes, 3, bytes.length - 3, StandardCharsets.UTF_8)));
            }
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        return files;
    }

    /** RFC 4180 解析（测试用，足够核对导出）。 */
    public static List<List<String>> parseCsv(String text) {
        List<List<String>> rows = new ArrayList<>();
        List<String> row = new ArrayList<>();
        StringBuilder cell = new StringBuilder();
        boolean quoted = false;
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            if (quoted) {
                if (c == '"' && i + 1 < text.length() && text.charAt(i + 1) == '"') {
                    cell.append('"');
                    i++;
                } else if (c == '"') {
                    quoted = false;
                } else {
                    cell.append(c);
                }
            } else if (c == '"') {
                quoted = true;
            } else if (c == ',') {
                row.add(cell.toString());
                cell.setLength(0);
            } else if (c == '\r') {
                continue;
            } else if (c == '\n') {
                row.add(cell.toString());
                cell.setLength(0);
                rows.add(row);
                row = new ArrayList<>();
            } else {
                cell.append(c);
            }
        }
        if (cell.length() > 0 || !row.isEmpty()) {
            row.add(cell.toString());
            rows.add(row);
        }
        return rows;
    }

    /** DOCX 里 {@code word/document.xml} 的全部文本片段，按出现顺序。 */
    public static List<String> docxTexts(byte[] docx) {
        List<String> texts = new ArrayList<>();
        try (ZipInputStream in = new ZipInputStream(new java.io.ByteArrayInputStream(docx))) {
            ZipEntry entry;
            while ((entry = in.getNextEntry()) != null) {
                if (!"word/document.xml".equals(entry.getName())) {
                    continue;
                }
                javax.xml.parsers.DocumentBuilderFactory factory =
                        javax.xml.parsers.DocumentBuilderFactory.newInstance();
                factory.setNamespaceAware(true);
                factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
                org.w3c.dom.NodeList all = factory.newDocumentBuilder()
                        .parse(new java.io.ByteArrayInputStream(in.readAllBytes()))
                        .getElementsByTagNameNS("*", "t");
                for (int i = 0; i < all.getLength(); i++) {
                    texts.add(all.item(i).getTextContent());
                }
            }
        } catch (IOException | javax.xml.parsers.ParserConfigurationException | org.xml.sax.SAXException e) {
            throw new IllegalStateException("could not read the docx", e);
        }
        return texts;
    }

    public static String sha256(byte[] bytes) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    public static byte[] readAll(InputStream in) {
        try (in; ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            in.transferTo(out);
            return out.toByteArray();
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    /**
     * 直接写入一个带文件上传题（题型 {@code |}）与自由文本题的已发布版本，返回其 sid。
     * 与 {@link ResponseFixture#addVersion} 相同，写在租户作用域内。
     */
    public long addUploadVersion(Published p) {
        long sid = NEXT_SID.incrementAndGet();
        UUID requestId = UUID.randomUUID();
        tenantScope.run(p.tenant(), () -> {
            jdbc.sql("""
                    INSERT INTO survey_publish_attempt (tenant_id, request_id, survey_id, draft_version,
                        engine_instance_id, definition, outcome, requested_by, completed_at)
                    SELECT tenant_id, :request, survey_id, 1, engine_instance_id, definition, 'published', 'owner', now()
                      FROM survey_published_version WHERE survey_id = :survey AND version_no = 1
                    """)
                    .param("request", requestId).param("survey", p.surveyId()).update();
            int version = jdbc.sql("SELECT MAX(version_no) + 1 FROM survey_published_version WHERE survey_id = :s")
                    .param("s", p.surveyId()).query(Integer.class).single();
            jdbc.sql("""
                    INSERT INTO survey_published_version (tenant_id, survey_id, version_no, request_id, draft_version,
                        engine_instance_id, engine_sid, definition, compiler_version, fingerprint_version, fingerprint,
                        language, engine_published_at, binding, published_by)
                    SELECT tenant_id, survey_id, :version, :request, 1, engine_instance_id, :sid, definition,
                        'pubgw-lss-1', 'fm1', 'fm1:0000000000000000', 'en', '2026-09-22T09:00:00Z', '{}'::jsonb, 'owner'
                      FROM survey_published_version WHERE survey_id = :survey AND version_no = 1
                    """)
                    .param("version", version).param("request", requestId).param("sid", sid)
                    .param("survey", p.surveyId()).update();
            binding(p, version, 0, "33333333-0009-4111-8111-000000000009", "QFILE", "|", UPLOAD_FIELD);
            binding(p, version, 1, "33333333-0008-4111-8111-000000000008", "QURL", "S", URL_FIELD);
        });
        return sid;
    }

    private void binding(Published p, int version, int ordinal, String uuid, String code, String type,
            String field) {
        jdbc.sql("""
                INSERT INTO survey_question_binding (tenant_id, survey_id, version_no, ordinal, question_uuid,
                    question_code, question_type, fieldname, aid, scale)
                VALUES (:tenant, :survey, :version, :ordinal, :uuid, :code, :type, :field, '', 0)
                """)
                .param("tenant", p.tenant().value()).param("survey", p.surveyId()).param("version", version)
                .param("ordinal", ordinal).param("uuid", UUID.fromString(uuid)).param("code", code)
                .param("type", type).param("field", field).update();
    }

    /** 用一条 SQL 为某个 (实例, sid) 生成大量已完成答卷的投影（规模测试用）。 */
    public void bulkProjection(Published p, long fromId, long toId) {
        tenantScope.run(p.tenant(), () -> jdbc.sql("""
                        INSERT INTO response_projection (tenant_id, engine_instance_id, survey_id, generation,
                            response_id, state, first_event_at, completed_at, last_event_id)
                        SELECT :tenant, :instance, :sid, :generation, g, 'engine_completed',
                               now(), now(), gen_random_uuid()
                          FROM generate_series(CAST(:from AS bigint), CAST(:to AS bigint)) AS g
                        """)
                .param("tenant", p.tenant().value()).param("instance", p.instance()).param("sid", p.sid())
                .param("generation", ResponseFixture.GENERATION).param("from", fromId).param("to", toId)
                .update());
    }
}
