package cn.mjy.platform.response;

import cn.mjy.platform.access.Permission;
import cn.mjy.platform.access.ResponseFieldPolicies;
import cn.mjy.platform.access.ResponseFieldPolicy;
import cn.mjy.platform.audit.AuditLogRepository;
import cn.mjy.platform.engine.ResponseProjection;
import cn.mjy.platform.engine.ResponseProjectionQuery;
import cn.mjy.platform.engine.ResponseState;
import cn.mjy.platform.response.ResponsePager.Entry;
import cn.mjy.platform.response.ResponsePager.Slice;
import cn.mjy.platform.response.ResponseSources.Source;
import cn.mjy.platform.response.ResponseSummary.Counts;
import cn.mjy.platform.response.ResponseSummary.VersionCounts;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;
import org.springframework.stereotype.Service;

/**
 * 答卷查询（WP-06 切片 06.1，ADR 0013）：明细分页、统计汇总、字段字典。
 *
 * <p>权限全部经 access 模块判定：明细要 {@code view-raw-responses}（统计查看者 403），
 * 敏感列按 {@link ResponseFieldPolicies} 遮蔽；汇总要 {@code view-statistics}；字段字典要 {@code view}。
 * 作答值在数据库事务之外经 {@link ResponseAnswerSource} 读取，不在持有连接时等网关。
 */
@Service
public class ResponseQueryService {

    public static final int DEFAULT_LIMIT = 50;
    public static final int MAX_LIMIT = 200;
    static final String AUDIT_LIST = "response.list";

    private final TenantScope tenantScope;
    private final ResponseAccess access;
    private final ResponseFieldPolicies fieldPolicies;
    private final ResponseSources sources;
    private final ResponsePager pager;
    private final ResponseProjectionQuery projections;
    private final ResponseAnswerSource answers;
    private final AuditLogRepository audit;

    ResponseQueryService(TenantScope tenantScope, ResponseAccess access, ResponseFieldPolicies fieldPolicies,
            ResponseSources sources, ResponsePager pager, ResponseProjectionQuery projections,
            ResponseAnswerSource answers, AuditLogRepository audit) {
        this.tenantScope = tenantScope;
        this.access = access;
        this.fieldPolicies = fieldPolicies;
        this.sources = sources;
        this.pager = pager;
        this.projections = projections;
        this.answers = answers;
        this.audit = audit;
    }

    /**
     * 明细的一页。
     *
     * @param state  {@code in_progress} / {@code engine_completed} / {@code deleted}；{@code null} 表示全部
     * @param cursor 上一页的 {@code nextCursor}；{@code null} 表示第一页
     * @param limit  每页条数，1–{@value #MAX_LIMIT}；{@code null} 取 {@value #DEFAULT_LIMIT}
     */
    public ResponsePage list(TenantContext ctx, UUID surveyId, String state, String cursor, Integer limit) {
        Objects.requireNonNull(ctx, "ctx");
        access.require(ctx, Permission.VIEW_RAW_RESPONSES, surveyId);
        ResponseState stateFilter = parseState(state);
        ResponseCursor after = cursor == null || cursor.isEmpty() ? null : ResponseCursor.decode(cursor);
        int size = pageSize(limit);

        List<Source> surveySources = sources.sources(ctx, surveyId);
        ResponseFieldPolicy policy = fieldPolicies.forResponses(ctx, surveyId,
                ResponseSources.sensitiveFieldnames(surveySources));
        Slice slice = tenantScope.call(ctx.tenantId(), () -> pager.slice(surveySources, stateFilter, after, size));
        List<ResponseRow> rows = withAnswers(slice, policy);
        tenantScope.run(ctx.tenantId(), () -> audit.record(ctx.tenantId(), ctx.actorId(), AUDIT_LIST,
                "survey/" + surveyId + "/responses", ctx.traceId()));
        String next = slice.hasMore() ? slice.entries().getLast().cursor().encode() : null;
        return new ResponsePage(rows, next, policy.revealsSensitiveFields());
    }

    /** 按版本、按状态的答卷数；统计查看者可用，不含任何逐行数据。 */
    public ResponseSummary summary(TenantContext ctx, UUID surveyId) {
        Objects.requireNonNull(ctx, "ctx");
        access.require(ctx, Permission.VIEW_STATISTICS, surveyId);
        List<Source> surveySources = sources.sources(ctx, surveyId);
        List<VersionCounts> versions = tenantScope.call(ctx.tenantId(), () -> surveySources.stream()
                .map(s -> new VersionCounts(s.version(), counts(projections.countByState(s.engineInstanceId(),
                        s.engineSid()))))
                .toList());
        Counts total = versions.stream().map(VersionCounts::counts).reduce(Counts.ZERO, Counts::plus);
        return new ResponseSummary(surveyId, versions, total);
    }

    /** 字段字典：每个已发布版本一段。 */
    public FieldDictionary fields(TenantContext ctx, UUID surveyId) {
        Objects.requireNonNull(ctx, "ctx");
        access.require(ctx, Permission.VIEW, surveyId);
        return new FieldDictionary(surveyId, sources.dictionary(ctx, surveyId));
    }

    private List<ResponseRow> withAnswers(Slice slice, ResponseFieldPolicy policy) {
        Map<Source, List<Long>> wanted = new LinkedHashMap<>();
        for (Entry entry : slice.entries()) {
            if (statusWithoutLookup(entry, slice) == AnswersStatus.AVAILABLE) {
                wanted.computeIfAbsent(entry.source(), s -> new ArrayList<>())
                        .add(entry.projection().key().responseId());
            }
        }
        Map<Source, AnswerBatch> fetched = new LinkedHashMap<>();
        wanted.forEach((source, ids) -> fetched.put(source,
                answers.read(AnswerQuery.of(source.engineInstanceId(), source.engineSid(), ids,
                        source.fieldnames()))));
        return slice.entries().stream().map(entry -> row(entry, slice, fetched, policy)).toList();
    }

    /** 不问引擎就能确定的状态：删除 → DELETED，旧代次 → ARCHIVED；否则先假定可取（AVAILABLE）。 */
    private static AnswersStatus statusWithoutLookup(Entry entry, Slice slice) {
        ResponseProjection projection = entry.projection();
        if (projection.state() == ResponseState.DELETED) {
            return AnswersStatus.DELETED;
        }
        String current = slice.latestGenerations().get(entry.source());
        return projection.key().generation().equals(current) ? AnswersStatus.AVAILABLE : AnswersStatus.ARCHIVED;
    }

    private static ResponseRow row(Entry entry, Slice slice, Map<Source, AnswerBatch> fetched,
            ResponseFieldPolicy policy) {
        AnswersStatus status = statusWithoutLookup(entry, slice);
        Map<String, String> values = null;
        if (status == AnswersStatus.AVAILABLE) {
            AnswerBatch batch = fetched.getOrDefault(entry.source(), AnswerBatch.empty());
            Map<String, String> raw = batch.answers().get(entry.projection().key().responseId());
            status = raw == null ? AnswersStatus.MISSING : AnswersStatus.AVAILABLE;
            values = raw == null ? null : policy.apply(raw);
        }
        ResponseProjection p = entry.projection();
        Source s = entry.source();
        return new ResponseRow(s.version(), s.engineInstanceId(), s.engineSid(), p.key().generation(),
                p.key().responseId(), p.state().dbValue(), p.firstEventAt(), p.completedAt(), p.deletedAt(),
                status, values);
    }

    private static Counts counts(Map<ResponseState, Long> byState) {
        return new Counts(byState.getOrDefault(ResponseState.IN_PROGRESS, 0L),
                byState.getOrDefault(ResponseState.ENGINE_COMPLETED, 0L),
                byState.getOrDefault(ResponseState.DELETED, 0L));
    }

    private static ResponseState parseState(String state) {
        if (state == null || state.isEmpty()) {
            return null;
        }
        return Arrays.stream(ResponseState.values())
                .filter(s -> s.dbValue().equals(state))
                .findFirst()
                .orElseThrow(() -> new InvalidResponseQueryException(
                        "state must be one of in_progress, engine_completed, deleted"));
    }

    private static int pageSize(Integer limit) {
        if (limit == null) {
            return DEFAULT_LIMIT;
        }
        if (limit < 1 || limit > MAX_LIMIT) {
            throw new InvalidResponseQueryException("limit must be between 1 and " + MAX_LIMIT);
        }
        return limit;
    }
}
