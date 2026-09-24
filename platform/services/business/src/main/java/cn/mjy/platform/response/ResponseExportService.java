package cn.mjy.platform.response;

import cn.mjy.platform.access.Permission;
import cn.mjy.platform.access.ResponseFieldPolicies;
import cn.mjy.platform.audit.AuditLogRepository;
import cn.mjy.platform.engine.ResponseProjectionQuery;
import cn.mjy.platform.engine.ResponseState;
import cn.mjy.platform.response.ExportPlan.PlannedSource;
import cn.mjy.platform.response.ExportRequest.ExportFilter;
import cn.mjy.platform.response.ResponseExportRepository.NewJob;
import cn.mjy.platform.response.ResponseSources.Source;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.util.Arrays;
import java.util.List;
import java.util.Objects;
import java.util.Optional;
import java.util.UUID;
import java.util.regex.Pattern;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import tools.jackson.databind.json.JsonMapper;

/**
 * 答卷导出作业的对外操作（WP-06 切片 06.2，ADR 0015）：创建、查状态、取消、下载。实际导出由
 * {@link ResponseExportWorker} 在后台分批完成。
 *
 * <p>权限全部经 access 模块：创建要 {@code export-raw-responses}（统计查看者 403），敏感列按创建时的
 * {@link ResponseFieldPolicies#forExport} 遮蔽并冻结；<b>下载时再判一次</b>，撤权即 403。
 * 作业只对创建者可见：他人、他租户一律 404。
 */
@Service
public class ResponseExportService {

    public static final String DEFAULT_TEMPLATE = "default";
    static final String AUDIT_CREATE = "response.export.create";
    static final String AUDIT_DOWNLOAD = "response.export.download";
    static final String AUDIT_CANCEL = "response.export.cancel";

    private static final Pattern IDEMPOTENCY_KEY = Pattern.compile("[A-Za-z0-9._:-]{1,128}");
    private static final Logger log = LoggerFactory.getLogger(ResponseExportService.class);

    private final TenantScope tenantScope;
    private final ResponseAccess access;
    private final ResponseFieldPolicies fieldPolicies;
    private final ResponseSources sources;
    private final ResponseProjectionQuery projections;
    private final ResponseExportRepository jobs;
    private final ExportFileStore files;
    private final AuditLogRepository audit;
    private final ResponseExportProperties properties;
    private final JsonMapper json;

    ResponseExportService(TenantScope tenantScope, ResponseAccess access, ResponseFieldPolicies fieldPolicies,
            ResponseSources sources, ResponseProjectionQuery projections, ResponseExportRepository jobs,
            ExportFileStore files, AuditLogRepository audit, ResponseExportProperties properties, JsonMapper json) {
        this.tenantScope = tenantScope;
        this.access = access;
        this.fieldPolicies = fieldPolicies;
        this.sources = sources;
        this.projections = projections;
        this.jobs = jobs;
        this.files = files;
        this.audit = audit;
        this.properties = properties;
        this.json = json;
    }

    /** 创建作业（202）。带幂等键时，同一创建者同一键返回原作业；参数不同则 409。 */
    public ExportJobView create(TenantContext ctx, UUID surveyId, ExportRequest request, String idempotencyKey) {
        Objects.requireNonNull(ctx, "ctx");
        access.require(ctx, Permission.EXPORT_RAW_RESPONSES, surveyId);
        ExportSpec spec = ExportSpec.of(surveyId, request);
        String key = idempotencyKey(idempotencyKey);
        Optional<ExportJobView> replay = replay(ctx, key, spec);
        if (replay.isPresent()) {
            return replay.get();
        }
        List<Source> surveySources = sources.sources(ctx, surveyId);
        ExportPlan plan = tenantScope.call(ctx.tenantId(), () -> plan(surveySources));
        boolean reveal = fieldPolicies.forExport(ctx, surveyId, plan.sensitiveFieldnames()).revealsSensitiveFields();
        NewJob job = new NewJob(ctx.tenantId(), UUID.randomUUID(), surveyId, ctx.actorId(), spec.format(),
                DEFAULT_TEMPLATE, json.writeValueAsString(spec.filter()), json.writeValueAsString(plan), reveal, key,
                properties.batchSize(), properties.retention());
        try {
            ExportJob created = tenantScope.call(ctx.tenantId(), () -> {
                ExportJob inserted = jobs.insert(job);
                audit.record(ctx.tenantId(), ctx.actorId(), AUDIT_CREATE, resource(inserted), ctx.traceId());
                return inserted;
            });
            return view(created);
        } catch (DuplicateKeyException e) {
            return replay(ctx, key, spec).orElseThrow(() -> e);
        }
    }

    public ExportJobView status(TenantContext ctx, UUID jobId) {
        return view(owned(ctx, jobId));
    }

    /** 取消未结束的作业；快照行立即删除，已写的分片随之清理。 */
    public ExportJobView cancel(TenantContext ctx, UUID jobId) {
        ExportJob job = owned(ctx, jobId);
        boolean cancelled = tenantScope.call(ctx.tenantId(), () -> {
            if (!jobs.cancel(jobId, ctx.actorId())) {
                return false;
            }
            jobs.deleteItems(jobId);
            audit.record(ctx.tenantId(), ctx.actorId(), AUDIT_CANCEL, resource(job), ctx.traceId());
            return true;
        });
        if (!cancelled) {
            throw new ExportConflictException("export_not_cancellable",
                    "export " + jobId + " is " + job.status().wire() + " and cannot be cancelled");
        }
        deleteFilesQuietly(ctx.tenantId(), jobId);
        return status(ctx, jobId);
    }

    /**
     * 下载：先再次授权（创建者此刻仍须有导出权限；文件含敏感明文时还须有查看敏感字段权限），再看状态。
     * 调用方负责关闭返回的流。
     */
    public ExportDownload download(TenantContext ctx, UUID jobId) {
        ExportJob job = owned(ctx, jobId);
        access.require(ctx, Permission.EXPORT_RAW_RESPONSES, job.surveyId());
        if (job.revealSensitive()) {
            access.require(ctx, Permission.VIEW_SENSITIVE_FIELDS, job.surveyId());
        }
        if (job.status() == ExportStatus.EXPIRED) {
            throw new ExportGoneException("export " + jobId + " has expired");
        }
        if (job.status() != ExportStatus.COMPLETED) {
            throw new ExportNotReadyException("export " + jobId + " is " + job.status().wire());
        }
        InputStream content;
        try {
            content = files.open(job.fileKey());
        } catch (IOException e) {
            throw new UncheckedIOException("export file of " + jobId + " is unreadable", e);
        }
        tenantScope.run(ctx.tenantId(), () -> audit.record(ctx.tenantId(), ctx.actorId(), AUDIT_DOWNLOAD,
                resource(job), ctx.traceId()));
        String name = "survey-" + job.surveyId() + "-export-" + jobId + "." + job.format().extension();
        return new ExportDownload(name, job.format().contentType(), job.fileSize(), job.fileSha256(), content);
    }

    private ExportJob owned(TenantContext ctx, UUID jobId) {
        Objects.requireNonNull(ctx, "ctx");
        return tenantScope.call(ctx.tenantId(), () -> jobs.findOwned(jobId, ctx.actorId()))
                .orElseThrow(() -> new ResponseNotFoundException("export not found: " + jobId));
    }

    private Optional<ExportJobView> replay(TenantContext ctx, String key, ExportSpec spec) {
        if (key == null) {
            return Optional.empty();
        }
        return tenantScope.call(ctx.tenantId(), () -> jobs.findByIdempotencyKey(ctx.actorId(), key))
                .map(existing -> {
                    ExportFilter filter = json.readValue(existing.filterJson(), ExportFilter.class);
                    if (!existing.surveyId().equals(spec.surveyId()) || existing.format() != spec.format()
                            || !filter.equals(spec.filter())) {
                        throw new ExportConflictException("idempotency_key_reused",
                                "Idempotency-Key was already used for a different export");
                    }
                    return view(existing);
                });
    }

    /**
     * 冻结来源、各 sid 此刻的最近代次与副表题名单（ADR 0013 决定 3、ADR 0015 增补二）。须在租户作用域内调用。
     *
     * <p>副表题超过网关单次上限时当场拒绝建作业：分多次读会让同一份文件里的副表来自不同时刻，
     * 与「作业创建即定水位线」相冲；悄悄少读几道题更不行。
     */
    private ExportPlan plan(List<Source> surveySources) {
        return new ExportPlan(surveySources.stream()
                .map(s -> new PlannedSource(s.version(), s.engineInstanceId(), s.engineSid(),
                        projections.latestGeneration(s.engineInstanceId(), s.engineSid()).orElse(null), s.fields(),
                        extensionQuestions(s)))
                .toList());
    }

    private static List<String> extensionQuestions(Source source) {
        if (source.extensionQuestions().size() > AnswerQuery.MAX_EXTENSION_QUESTIONS) {
            throw new InvalidResponseQueryException("version " + source.version() + " has more than "
                    + AnswerQuery.MAX_EXTENSION_QUESTIONS + " side-table questions and cannot be exported");
        }
        return source.extensionQuestions();
    }

    void deleteFilesQuietly(TenantId tenant, UUID jobId) {
        try {
            files.deletePrefix(ResponseExportWorker.jobPrefix(tenant, jobId));
        } catch (IOException | RuntimeException e) {
            log.warn("could not delete files of export {}; expiry will retry", jobId, e);
        }
    }

    ExportJobView view(ExportJob job) {
        return new ExportJobView(job.id(), job.surveyId(), job.format().code(), job.templateVersion(),
                json.readValue(job.filterJson(), ExportFilter.class), job.status().wire(), job.revealSensitive(),
                job.totalRows(), job.processedRows(), job.attempts(), job.lastError(), job.fileSize(),
                job.fileSha256(), job.createdAt(), job.startedAt(), job.finishedAt(), job.expiresAt());
    }

    private static String resource(ExportJob job) {
        return "survey/" + job.surveyId() + "/exports/" + job.id();
    }

    private static String idempotencyKey(String key) {
        if (key == null || key.isEmpty()) {
            return null;
        }
        if (!IDEMPOTENCY_KEY.matcher(key).matches()) {
            throw new InvalidResponseQueryException("Idempotency-Key must be 1-128 characters of [A-Za-z0-9._:-]");
        }
        return key;
    }

    /** 校验过的请求：格式、筛选（状态按固定顺序、去重）。 */
    record ExportSpec(UUID surveyId, ExportFormat format, ExportFilter filter) {

        static ExportSpec of(UUID surveyId, ExportRequest request) {
            if (request == null) {
                throw new InvalidResponseQueryException("request body is required");
            }
            if (request.surveyId() != null && !request.surveyId().equals(surveyId)) {
                throw new InvalidResponseQueryException("surveyId in the body does not match the path");
            }
            ExportFormat format = ExportFormat.fromCode(request.format())
                    .orElseThrow(() -> new InvalidResponseQueryException("format must be one of csv, xlsx"));
            String template = request.templateVersion();
            if (template != null && !template.isEmpty() && !DEFAULT_TEMPLATE.equals(template)) {
                throw new InvalidResponseQueryException("templateVersion must be empty or default");
            }
            return new ExportSpec(surveyId, format, new ExportFilter(states(request.filter())));
        }

        private static List<String> states(ExportFilter filter) {
            List<String> requested = filter == null || filter.states() == null ? List.of() : filter.states();
            for (String state : requested) {
                if (Arrays.stream(ResponseState.values()).noneMatch(s -> s.dbValue().equals(state))) {
                    throw new InvalidResponseQueryException(
                            "filter.states must be drawn from in_progress, engine_completed, deleted");
                }
            }
            return Arrays.stream(ResponseState.values())
                    .map(ResponseState::dbValue)
                    .filter(s -> requested.isEmpty() || requested.contains(s))
                    .toList();
        }
    }
}
