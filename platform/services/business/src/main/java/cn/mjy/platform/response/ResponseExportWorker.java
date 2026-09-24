package cn.mjy.platform.response;

import cn.mjy.platform.access.AccessDecisionService;
import cn.mjy.platform.access.Permission;
import cn.mjy.platform.access.ResponseFieldPolicies;
import cn.mjy.platform.access.ResponseFieldPolicy;
import cn.mjy.platform.engine.ResponseProjection;
import cn.mjy.platform.engine.ResponseProjectionQuery;
import cn.mjy.platform.engine.ResponseProjectionQuery.Position;
import cn.mjy.platform.engine.ResponseState;
import cn.mjy.platform.response.ExportPlan.PlannedSource;
import cn.mjy.platform.response.ExportRequest.ExportFilter;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantDirectory;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import tools.jackson.databind.json.JsonMapper;

/**
 * 导出作业的后台执行者（ADR 0015 决定 1）。一个作业分三个阶段，每一步都在一个短事务里前移检查点：
 * <ol>
 *   <li>物化快照：按 (版本, 代次, 答卷号) 键集翻页读投影（只要创建时刻之前写入的行），逐页写入快照表并编号；</li>
 *   <li>分批导出：每批按 seq 取出快照行，复核权限，经网关读作答（事务外），遮蔽、编码成分片写入存储，再前移 next_seq；</li>
 *   <li>收尾：按序拼接分片写出最终文件、记录大小与 SHA-256、删除快照行与分片。</li>
 * </ol>
 * 每一步都校验租约（{@code lease_token}），失去租约（被取消、到期、被接管）即停止。进程死掉时租约到期后由
 * 任意执行者从检查点继续；重做的一批覆盖同名分片，因此重试幂等。
 *
 * <p>跨租户：经 {@link TenantDirectory} 拿名单，逐个租户进入 {@link TenantScope}，不开放任何跨租户读取。
 */
@Component
public class ResponseExportWorker {

    static final String SYSTEM_TRACE_PREFIX = "export-";
    private static final int CLAIM_SCAN_LIMIT = 20;
    private static final int EXPIRY_SCAN_LIMIT = 100;
    private static final String PART_PREFIX = "part-";
    private static final String FILE_PREFIX = "export.";
    private static final String SEQ_FORMAT = "%012d";

    private static final Logger log = LoggerFactory.getLogger(ResponseExportWorker.class);

    /** 一步的结果。 */
    enum Step { CONTINUE, DONE, LOST }

    /** 失败即终止、不再重试的情形（权限变化、行数超出格式上限）。 */
    static final class TerminalFailure extends RuntimeException {

        private final String code;

        TerminalFailure(String code, String message) {
            super(message);
            this.code = code;
        }
    }

    /** 本次认领期间不变的上下文：计划与布局只解析一次。 */
    private record Run(TenantId tenant, UUID jobId, UUID token, ExportJob claimed, ExportPlan plan,
            ExportLayout layout, List<String> states, OffsetDateTime watermark) {
    }

    private final TenantDirectory tenants;
    private final TenantScope tenantScope;
    private final ResponseExportRepository jobs;
    private final ResponseProjectionQuery projections;
    private final ResponseAnswerSource answers;
    private final AccessDecisionService access;
    private final ResponseFieldPolicies fieldPolicies;
    private final ExportFileStore files;
    private final ExportFileAssembler assembler;
    private final ResponseExportProperties properties;
    private final JsonMapper json;

    ResponseExportWorker(TenantDirectory tenants, TenantScope tenantScope, ResponseExportRepository jobs,
            ResponseProjectionQuery projections, ResponseAnswerSource answers, AccessDecisionService access,
            ResponseFieldPolicies fieldPolicies, ExportFileStore files, ExportFileAssembler assembler,
            ResponseExportProperties properties, JsonMapper json) {
        this.tenants = tenants;
        this.tenantScope = tenantScope;
        this.jobs = jobs;
        this.projections = projections;
        this.answers = answers;
        this.access = access;
        this.fieldPolicies = fieldPolicies;
        this.files = files;
        this.assembler = assembler;
        this.properties = properties;
        this.json = json;
    }

    static String jobPrefix(TenantId tenant, UUID jobId) {
        return tenant.value() + "/" + jobId;
    }

    static String partKey(TenantId tenant, UUID jobId, long fromSeq) {
        return jobPrefix(tenant, jobId) + "/" + PART_PREFIX + String.format(Locale.ROOT, SEQ_FORMAT, fromSeq);
    }

    static String fileKey(TenantId tenant, UUID jobId, String extension) {
        return jobPrefix(tenant, jobId) + "/" + FILE_PREFIX + extension;
    }

    /** 跑一轮：逐个租户认领到期的作业，每个作业最多推进 maxStepsPerRun 步。单个作业或租户出错不影响其余。 */
    public int runOnce() {
        int processed = 0;
        for (TenantId tenant : tenants.openTenants()) {
            try {
                for (UUID job : tenantScope.call(tenant, () -> jobs.claimable(CLAIM_SCAN_LIMIT))) {
                    processed += process(tenant, job, properties.maxStepsPerRun()) ? 1 : 0;
                }
            } catch (RuntimeException e) {
                log.error("response export round failed for tenant {}", tenant, e);
            }
        }
        return processed;
    }

    /**
     * 认领并推进一个作业至多 maxSteps 步。认领不到（不存在、别的租户、已结束、租约被占、退避未到）返回 false。
     * 步数用完正常让出租约；业务失败按退避重排或终止。{@link Error} 不捕获——等同进程死掉，租约到期后续做。
     */
    public boolean process(TenantId tenant, UUID jobId, int maxSteps) {
        UUID token = UUID.randomUUID();
        Optional<ExportJob> claimed = tenantScope.call(tenant, () -> jobs.claim(jobId, token, properties.lease()));
        if (claimed.isEmpty()) {
            return false;
        }
        Run run = run(tenant, token, claimed.get());
        try {
            for (int steps = 0; steps < maxSteps; steps++) {
                if (step(run) != Step.CONTINUE) {
                    return true;
                }
            }
            tenantScope.run(tenant, () -> jobs.release(jobId, token));
        } catch (LeaseLost e) {
            log.info("response export {} of tenant {}: lease lost, stopping", jobId, tenant);
        } catch (TerminalFailure e) {
            log.warn("response export {} of tenant {} failed permanently: {}", jobId, tenant, e.getMessage());
            tenantScope.run(tenant, () -> jobs.fail(jobId, token, e.code));
        } catch (ResponseAnswersUnavailableException e) {
            retryLater(run, "answers_unavailable", e);
        } catch (UncheckedIOException e) {
            retryLater(run, "storage_error", e);
        } catch (RuntimeException e) {
            retryLater(run, "internal_error", e);
        }
        return true;
    }

    /** 到期作业：删除文件与快照行，状态置 expired。 */
    public int expireDue() {
        int expired = 0;
        for (TenantId tenant : tenants.openTenants()) {
            try {
                for (UUID job : tenantScope.call(tenant, () -> jobs.expired(EXPIRY_SCAN_LIMIT))) {
                    boolean marked = tenantScope.call(tenant, () -> {
                        boolean ok = jobs.markExpired(job);
                        if (ok) {
                            jobs.deleteItems(job);
                        }
                        return ok;
                    });
                    if (marked) {
                        deletePrefix(tenant, job);
                        expired++;
                    }
                }
            } catch (RuntimeException e) {
                log.error("response export expiry failed for tenant {}", tenant, e);
            }
        }
        return expired;
    }

    private Run run(TenantId tenant, UUID token, ExportJob job) {
        ExportPlan plan = json.readValue(job.planJson(), ExportPlan.class);
        List<String> states = json.readValue(job.filterJson(), ExportFilter.class).states();
        return new Run(tenant, job.id(), token, job, plan, new ExportLayout(plan), states,
                job.createdAt().atOffset(ZoneOffset.UTC));
    }

    private Step step(Run run) {
        Optional<ExportJob> current = tenantScope.call(run.tenant(), () -> jobs.find(run.jobId()));
        if (current.isEmpty() || !run.token().equals(current.get().leaseToken())
                || current.get().status() != ExportStatus.RUNNING) {
            return Step.LOST;
        }
        ExportJob job = current.get();
        if (!job.snapshotDone()) {
            return snapshotStep(run, job);
        }
        if (job.nextSeq() <= job.totalRows()) {
            return exportStep(run, job);
        }
        return finish(run, job);
    }

    // ---- 阶段一：物化快照 ----

    private Step snapshotStep(Run run, ExportJob job) {
        List<PlannedSource> sources = run.plan().sources();
        if (sources.isEmpty()) {
            return finishSnapshot(run, 0);
        }
        int version = job.snapshotVersion() == null ? sources.getFirst().version() : job.snapshotVersion();
        PlannedSource source = run.plan().source(version);
        Position after = job.snapshotGeneration() == null ? null
                : new Position(job.snapshotGeneration(), job.snapshotResponseId());
        return tenantScope.call(run.tenant(), () -> {
            List<ResponseProjection> page = projections.page(source.engineInstanceId(), source.engineSid(),
                    singleState(run.states()), after, properties.snapshotPageSize(), run.watermark());
            long lastSeq = jobs.maxSeq(run.jobId());
            List<ExportItem> items = items(source, page, run.states(), lastSeq);
            if (!items.isEmpty()) {
                jobs.insertItems(run.tenant(), run.jobId(), items);
            }
            boolean mine = page.size() == properties.snapshotPageSize()
                    ? jobs.snapshotProgress(run.jobId(), run.token(), version, page.getLast().key().generation(),
                            page.getLast().key().responseId(), properties.lease())
                    : nextSource(run, sources, version, lastSeq + items.size());
            if (!mine) {
                throw new LeaseLost();
            }
            return Step.CONTINUE;
        });
    }

    private boolean nextSource(Run run, List<PlannedSource> sources, int version, long total) {
        int index = sources.indexOf(run.plan().source(version));
        if (index + 1 < sources.size()) {
            return jobs.snapshotProgress(run.jobId(), run.token(), sources.get(index + 1).version(), null, null,
                    properties.lease());
        }
        long limit = run.claimed().format().writer().maxDataRows();
        if (total > limit) {
            throw new TerminalFailure("too_many_rows",
                    total + " rows exceed the " + run.claimed().format().code() + " limit of " + limit);
        }
        return jobs.snapshotDone(run.jobId(), run.token(), total, properties.lease());
    }

    private Step finishSnapshot(Run run, long total) {
        boolean mine = tenantScope.call(run.tenant(),
                () -> jobs.snapshotDone(run.jobId(), run.token(), total, properties.lease()));
        return mine ? Step.CONTINUE : Step.LOST;
    }

    private static ResponseState singleState(List<String> states) {
        return states.size() == 1 ? ResponseState.fromDb(states.getFirst()) : null;
    }

    private static List<ExportItem> items(PlannedSource source, List<ResponseProjection> page, List<String> states,
            long lastSeq) {
        List<ExportItem> items = new ArrayList<>();
        long seq = lastSeq;
        for (ResponseProjection row : page) {
            if (!states.contains(row.state().dbValue())) {
                continue;
            }
            items.add(new ExportItem(++seq, source.version(), source.engineInstanceId(), source.engineSid(),
                    row.key().generation(), row.key().responseId(), row.state().dbValue(), row.firstEventAt(),
                    row.completedAt()));
        }
        return items;
    }

    // ---- 阶段二：分批导出 ----

    private Step exportStep(Run run, ExportJob job) {
        List<ExportItem> items = tenantScope.call(run.tenant(),
                () -> jobs.items(run.jobId(), job.nextSeq(), job.batchSize()));
        if (items.isEmpty() || items.getFirst().seq() != job.nextSeq()) {
            return Step.LOST;
        }
        ResponseFieldPolicy policy = currentPolicy(run, job);
        Map<Integer, AnswerBatch> fetched = fetch(run.plan(), items);
        ExportRowEncoder.Encoded encoded = new ExportRowEncoder(run.layout(), policy).encode(items, fetched);
        writePart(partKey(run.tenant(), run.jobId(), job.nextSeq()), encoded);
        long next = items.getLast().seq() + 1;
        boolean mine = tenantScope.call(run.tenant(),
                () -> jobs.advance(run.jobId(), run.token(), next, properties.lease()));
        return mine ? Step.CONTINUE : Step.LOST;
    }

    /** 每批复核：创建者仍能导出，且"能否看敏感明文"与创建时一致，否则整份作业作废，避免前后口径不一。 */
    private ResponseFieldPolicy currentPolicy(Run run, ExportJob job) {
        TenantContext requester = new TenantContext(run.tenant(), job.requestedBy(), List.of(),
                SYSTEM_TRACE_PREFIX + run.jobId());
        if (!access.can(requester, Permission.EXPORT_RAW_RESPONSES, job.surveyId()).allowed()) {
            throw new TerminalFailure("permission_changed", "requester can no longer export raw responses");
        }
        ResponseFieldPolicy policy = fieldPolicies.forExport(requester, job.surveyId(),
                run.plan().sensitiveFieldnames());
        if (policy.revealsSensitiveFields() != job.revealSensitive()) {
            throw new TerminalFailure("permission_changed", "sensitive field visibility changed since creation");
        }
        return policy;
    }

    /**
     * 按版本分组读作答：只读未删除、属于创建时最近代次的行；一批不超过网关单次上限。
     * 副表题在计划里点名，代次取计划冻结的那一个——两者一起给才读得到副表（契约 response-read-v1）。
     */
    private Map<Integer, AnswerBatch> fetch(ExportPlan plan, List<ExportItem> items) {
        Map<Integer, List<Long>> wanted = new LinkedHashMap<>();
        for (ExportItem item : items) {
            if (ExportRowEncoder.needsAnswers(item, plan.source(item.version()))) {
                wanted.computeIfAbsent(item.version(), v -> new ArrayList<>()).add(item.responseId());
            }
        }
        Map<Integer, AnswerBatch> fetched = new LinkedHashMap<>();
        wanted.forEach((version, ids) -> {
            PlannedSource source = plan.source(version);
            AnswerQuery query = new AnswerQuery(source.engineInstanceId(), source.engineSid(), ids,
                    source.fieldnames(), source.latestGeneration(), source.extensionQuestions());
            fetched.put(version, query.isEmpty() ? AnswerBatch.empty() : answers.read(query));
        });
        return fetched;
    }

    private void writePart(String key, ExportRowEncoder.Encoded encoded) {
        try (ExportFileStore.Upload upload = files.create(key)) {
            ExportPartCodec.write(encoded, upload.stream());
            upload.commit();
        } catch (IOException e) {
            throw new UncheckedIOException("could not write export part", e);
        }
    }

    // ---- 阶段三：收尾 ----

    private Step finish(Run run, ExportJob job) {
        String key = fileKey(run.tenant(), run.jobId(), job.format().extension());
        List<String> parts = partKeys(run.tenant(), run.jobId(), job);
        ExportFileAssembler.Written written = assembler.assemble(key, job.format(), run.layout(),
                job.revealSensitive(), parts);
        boolean mine = tenantScope.call(run.tenant(), () -> {
            boolean ok = jobs.complete(run.jobId(), run.token(), key, written.size(), written.sha256());
            if (ok) {
                jobs.deleteItems(run.jobId());
            }
            return ok;
        });
        if (!mine) {
            return Step.LOST;
        }
        for (String part : parts) {
            try {
                files.delete(part);
            } catch (IOException e) {
                log.warn("could not delete export part {}; expiry will retry", part, e);
            }
        }
        log.info("response export {} of tenant {} completed: {} rows, {} bytes", run.jobId(), run.tenant(),
                job.totalRows(), written.size());
        return Step.DONE;
    }

    private static List<String> partKeys(TenantId tenant, UUID jobId, ExportJob job) {
        List<String> keys = new ArrayList<>();
        for (long seq = 1; seq <= job.totalRows(); seq += job.batchSize()) {
            keys.add(partKey(tenant, jobId, seq));
        }
        return keys;
    }

    private void retryLater(Run run, String code, RuntimeException cause) {
        log.warn("response export {} of tenant {} failed ({}); will retry", run.jobId(), run.tenant(), code, cause);
        Optional<Boolean> failed = tenantScope.call(run.tenant(), () -> jobs.retryLater(run.jobId(), run.token(),
                code, properties.backoff(), properties.maxAttempts()));
        if (failed.orElse(false)) {
            log.error("response export {} of tenant {} gave up after {} attempts", run.jobId(), run.tenant(),
                    properties.maxAttempts());
        }
    }

    private void deletePrefix(TenantId tenant, UUID job) {
        try {
            files.deletePrefix(jobPrefix(tenant, job));
        } catch (IOException e) {
            log.warn("could not delete files of expired export {}", job, e);
        }
    }

    /** 事务里发现租约已不属于自己：回滚本步。 */
    private static final class LeaseLost extends RuntimeException {

        LeaseLost() {
            super("export lease lost");
        }
    }
}
