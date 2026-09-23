package cn.mjy.platform.contacts;

import cn.mjy.platform.contacts.ContactImportRepository.ImportJob;
import cn.mjy.platform.contacts.ContactImportRepository.RowPayload;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.UUID;
import java.util.regex.Pattern;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;

/**
 * 导入作业的提交与查询（WP-18 切片 18.1）。提交只做结构解析与落库，
 * 逐行校验、去重与写人都在后台 {@link ContactImportWorker} 里分步进行，可断点续跑。
 */
@Service
public class ContactImportService {

    private static final Pattern IDEMPOTENCY_KEY = Pattern.compile("[A-Za-z0-9._:-]{1,128}");
    private static final int MAX_OUTCOME_PAGE = 500;

    private final TenantScope tenantScope;
    private final ContactAccess access;
    private final ContactListService lists;
    private final ContactImportRepository jobs;
    private final ImportRowParser parser;
    private final ContactImportProperties properties;
    private final ContactsAudit audit;

    ContactImportService(TenantScope tenantScope, ContactAccess access, ContactListService lists,
            ContactImportRepository jobs, ImportRowParser parser, ContactImportProperties properties,
            ContactsAudit audit) {
        this.tenantScope = tenantScope;
        this.access = access;
        this.lists = lists;
        this.jobs = jobs;
        this.parser = parser;
        this.properties = properties;
        this.audit = audit;
    }

    public ImportJobView submit(TenantContext ctx, UUID listId, ImportRequest request, String idempotencyKey) {
        Objects.requireNonNull(ctx, "ctx");
        Objects.requireNonNull(request, "request");
        String key = validKey(idempotencyKey);
        requireSize(request.content());
        List<String> rows = parser.parse(request.format(), request.content());
        if (rows.isEmpty()) {
            throw new InvalidContactRequestException("import has no data rows");
        }
        if (rows.size() > properties.maxRows()) {
            throw new InvalidContactRequestException("import has more than " + properties.maxRows() + " rows");
        }
        return tenantScope.call(ctx.tenantId(), () -> {
            access.scopeOf(ctx);
            lists.require(listId);
            if (key != null) {
                var replay = jobs.findByIdempotencyKey(ctx.actorId(), key);
                if (replay.isPresent()) {
                    return replay.get().toView();
                }
            }
            ImportJob job;
            try {
                job = jobs.insert(UUID.randomUUID(), listId, request.format(), ctx.actorId(), key, rows.size());
            } catch (DuplicateKeyException e) {
                return jobs.findByIdempotencyKey(ctx.actorId(), key).orElseThrow(() -> e).toView();
            }
            jobs.insertRows(job.id(), payloads(rows));
            audit.record(ctx, ContactsAudit.IMPORT_SUBMIT, "contact-import", job.id(),
                    "list/" + listId + " rows=" + rows.size());
            return job.toView();
        });
    }

    public ImportJobView status(TenantContext ctx, UUID jobId) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.scopeOf(ctx);
            return require(jobId).toView();
        });
    }

    /** 逐行结果；不含行内容，只有行号、结局与原因码。 */
    public List<ImportRowOutcome> outcomes(TenantContext ctx, UUID jobId, int fromRow, int limit) {
        int page = Math.min(Math.max(limit, 1), MAX_OUTCOME_PAGE);
        return tenantScope.call(ctx.tenantId(), () -> {
            access.scopeOf(ctx);
            require(jobId);
            return jobs.outcomes(jobId, Math.max(fromRow, 1), page);
        });
    }

    private ImportJob require(UUID jobId) {
        return jobs.find(jobId)
                .orElseThrow(() -> new ContactNotFoundException("contact import not found: " + jobId));
    }

    private void requireSize(String content) {
        if (content != null && content.getBytes(StandardCharsets.UTF_8).length > properties.maxContentBytes()) {
            throw new InvalidContactRequestException(
                    "content exceeds " + properties.maxContentBytes() + " bytes");
        }
    }

    private static List<RowPayload> payloads(List<String> rows) {
        List<RowPayload> payloads = new ArrayList<>(rows.size());
        for (int i = 0; i < rows.size(); i++) {
            payloads.add(new RowPayload(i + 1, rows.get(i)));
        }
        return payloads;
    }

    private static String validKey(String idempotencyKey) {
        if (idempotencyKey == null || idempotencyKey.isBlank()) {
            return null;
        }
        if (!IDEMPOTENCY_KEY.matcher(idempotencyKey).matches()) {
            throw new InvalidContactRequestException("Idempotency-Key must match [A-Za-z0-9._:-]{1,128}");
        }
        return idempotencyKey;
    }
}
