package cn.mjy.platform.contacts;

import cn.mjy.platform.contacts.ContactDirectoryService.Upsert;
import cn.mjy.platform.contacts.ContactImportRepository.ImportJob;
import cn.mjy.platform.contacts.ContactRepository.ContactRow;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantDirectory;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * 逐行推进导入作业（WP-18 切片 18.1）。每一行是一个独立事务：写联系人、记结果、推进断点三件事同进同退，
 * 因此进程在任何时刻挂掉，恢复后都从同一行重来，既不漏行也不重复建人。
 *
 * <p>日志只写作业 id、行号与结果码，<b>绝不</b>写行内容（ADR 0017 决定 6）。
 */
@Component
public class ContactImportWorker {

    private static final Logger log = LoggerFactory.getLogger(ContactImportWorker.class);
    private static final int CLAIM_SCAN_LIMIT = 20;
    private static final String TRACE_PREFIX = "contact-import-";

    /** 一步的结局：还有下一行、这一行判完了整批也完了、租约已经不是自己的。 */
    private enum Step { CONTINUE, DONE, LOST }

    private final TenantDirectory tenants;
    private final TenantScope tenantScope;
    private final ContactImportRepository jobs;
    private final ContactListService lists;
    private final ContactAccess access;
    private final ContactDirectoryService directory;
    private final OrgUnitService units;
    private final ImportRowParser parser;
    private final ContactImportProperties properties;
    private final ContactsAudit audit;

    ContactImportWorker(TenantDirectory tenants, TenantScope tenantScope, ContactImportRepository jobs,
            ContactListService lists, ContactAccess access, ContactDirectoryService directory,
            OrgUnitService units, ImportRowParser parser, ContactImportProperties properties,
            ContactsAudit audit) {
        this.tenants = tenants;
        this.tenantScope = tenantScope;
        this.jobs = jobs;
        this.lists = lists;
        this.access = access;
        this.directory = directory;
        this.units = units;
        this.parser = parser;
        this.properties = properties;
        this.audit = audit;
    }

    /** 扫一轮所有在营租户的待办作业。一个租户出错不影响其余租户。 */
    public int runOnce() {
        int processed = 0;
        for (TenantId tenant : tenants.openTenants()) {
            try {
                List<UUID> claimable = tenantScope.call(tenant, () -> jobs.claimable(CLAIM_SCAN_LIMIT));
                for (UUID jobId : claimable) {
                    if (process(tenant, jobId, properties.maxRowsPerRun())) {
                        processed++;
                    }
                }
            } catch (RuntimeException e) {
                log.error("contact import round failed for tenant {}", tenant.value(), e);
            }
        }
        return processed;
    }

    /** 认领并推进一个作业，最多处理 maxRows 行。返回是否认领成功。 */
    public boolean process(TenantId tenant, UUID jobId, int maxRows) {
        UUID token = UUID.randomUUID();
        Optional<ImportJob> claimed = tenantScope.call(tenant, () -> jobs.claim(jobId, token, properties.lease()));
        if (claimed.isEmpty()) {
            return false;
        }
        TenantContext ctx = new TenantContext(tenant, claimed.get().requestedBy(), List.of(),
                TRACE_PREFIX + jobId);
        try {
            Run run = prepare(ctx, claimed.get(), token);
            for (int steps = 0; steps < maxRows; steps++) {
                Step step = tenantScope.call(tenant, () -> step(run));
                if (step == Step.LOST) {
                    return true;
                }
                if (step == Step.DONE) {
                    tenantScope.run(tenant, () -> finish(ctx, run));
                    return true;
                }
            }
            tenantScope.run(tenant, () -> jobs.release(jobId, token));
        } catch (ContactAccessDeniedException e) {
            log.warn("contact import {} stopped: requester lost access", jobId);
            tenantScope.run(tenant, () -> jobs.fail(jobId, token, "access_denied"));
        } catch (RuntimeException e) {
            log.error("contact import {} failed", jobId, e);
            tenantScope.run(tenant, () -> jobs.retryLater(jobId, token, "internal_error", properties.backoff(),
                    properties.maxAttempts()));
        }
        return true;
    }

    /** 一次推进的固定上下文：作业、租约、名单与调用者的数据范围。 */
    private record Run(TenantContext ctx, UUID jobId, UUID token, UUID listId, ContactKind kind,
            ContactScope scope) {
    }

    private Run prepare(TenantContext ctx, ImportJob job, UUID token) {
        return tenantScope.call(ctx.tenantId(), () -> {
            ContactScope scope = access.scopeOf(ctx);
            ContactListView list = lists.require(job.listId());
            return new Run(ctx, job.id(), token, list.id(), list.kind(), scope);
        });
    }

    private Step step(Run run) {
        Optional<ImportJob> current = jobs.find(run.jobId());
        if (current.isEmpty() || !run.token().equals(current.get().leaseToken())
                || !"running".equals(current.get().status())) {
            return Step.LOST;
        }
        ImportJob job = current.get();
        if (job.isDone()) {
            return Step.DONE;
        }
        int rowNo = job.nextRow();
        Optional<String> payload = jobs.payload(run.jobId(), rowNo);
        if (payload.isEmpty()) {
            return commit(run, rowNo, "invalid", "missing_row", null);
        }
        try {
            return commitUpsert(run, rowNo, parser.toColumns(payload.get()));
        } catch (ContactRowRejectedException e) {
            return commit(run, rowNo, "invalid", e.reason(), null);
        } catch (InvalidContactRequestException e) {
            return commit(run, rowNo, "invalid", "invalid_row", null);
        }
    }

    private Step commitUpsert(Run run, int rowNo, Map<String, String> columns) {
        ContactDraft draft = draft(run, columns);
        Upsert upsert = directory.importRow(run.ctx(), run.scope(), run.listId(), run.kind(), draft);
        ContactRow row = upsert.row();
        if (upsert.created()) {
            return commit(run, rowNo, "created", null, row.id());
        }
        // 已存在的人：如果本次作业刚刚写过它，这一行就是文件内的重复；否则是对库里已有人的更新。
        boolean duplicateInFile = jobs.touchedByJob(run.jobId(), row.id());
        return duplicateInFile
                ? commit(run, rowNo, "duplicate", "duplicate_in_file", null)
                : commit(run, rowNo, "updated", null, row.id());
    }

    private Step commit(Run run, int rowNo, String outcome, String reason, UUID contactId) {
        boolean stillMine = jobs.commitRow(run.jobId(), run.token(), rowNo, outcome, reason, contactId,
                properties.lease());
        return stillMine ? Step.CONTINUE : Step.LOST;
    }

    private void finish(TenantContext ctx, Run run) {
        if (jobs.finish(run.jobId(), run.token())) {
            audit.record(ctx, ContactsAudit.IMPORT_FINISH, "contact-import", run.jobId());
        }
    }

    private ContactDraft draft(Run run, Map<String, String> columns) {
        ContactDraft draft = ContactDraft.named(columns.get("name"))
                .withEmail(columns.get("email"))
                .withPhone(columns.get("phone"))
                .withEmployeeId(columns.get("employee_id"));
        String provider = columns.get("provider");
        String appId = columns.get("app_id");
        String externalId = columns.get("external_id");
        if (provider != null || appId != null || externalId != null) {
            if (provider == null || appId == null || externalId == null) {
                throw new ContactRowRejectedException("invalid_external_id");
            }
            draft = draft.withIdentity(new ExternalContactIdentity(provider, appId, externalId));
        }
        String unitRef = columns.get("org_unit");
        if (unitRef != null) {
            draft = draft.withUnit(units.requireByExternalRef(run.scope(), unitRef).id());
        }
        return draft;
    }
}
