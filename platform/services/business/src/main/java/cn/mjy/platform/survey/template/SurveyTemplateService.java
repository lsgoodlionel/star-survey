package cn.mjy.platform.survey.template;

import cn.mjy.platform.access.Permission;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.SurveyDefinitions;
import cn.mjy.platform.survey.SurveyService;
import cn.mjy.platform.survey.SurveyView;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.Optional;
import java.util.UUID;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * 企业模板库（R19-05）：从问卷做模板、版本、审核、复制、下架。
 *
 * <p>三条不变量：
 *
 * <ol>
 *   <li><b>模板不携带生产数据。</b>剥离在 {@link TemplateDefinitions} 里，进库前完成，
 *       库里存的就是剥干净的快照——不是"读的时候过滤"。
 *   <li><b>复制出来的是独立草稿。</b>复制写的是问卷自己的 {@code draft_definition}，
 *       之后模板怎么改都与它无关；每份复制还会换一套实体 id，两份复制之间也互不相干。
 *   <li><b>下架只影响之后的复制。</b>已经复制出去的问卷是租户自己的问卷，不受任何影响。
 * </ol>
 *
 * <p>状态迁移全部写成带 from 状态谓词的 UPDATE，并在模板行锁内进行，所以并发送审 / 批准
 * 只有一个能成功，另一个拿到 409。
 */
@Service
public class SurveyTemplateService {

    static final int MAX_NAME_LENGTH = 200;
    static final int MAX_REASON_LENGTH = 1000;

    private final TenantScope tenantScope;
    private final TemplateAccess access;
    private final SurveyTemplateRepository templates;
    private final PlatformTemplateRepository platformTemplates;
    private final TemplateDefinitions templateDefinitions;
    private final SurveyDefinitions definitions;
    private final SurveyService surveys;
    private final TemplateAudit audit;

    SurveyTemplateService(TenantScope tenantScope, TemplateAccess access, SurveyTemplateRepository templates,
            PlatformTemplateRepository platformTemplates, TemplateDefinitions templateDefinitions,
            SurveyDefinitions definitions, SurveyService surveys, TemplateAudit audit) {
        this.tenantScope = tenantScope;
        this.access = access;
        this.templates = templates;
        this.platformTemplates = platformTemplates;
        this.templateDefinitions = templateDefinitions;
        this.definitions = definitions;
        this.surveys = surveys;
        this.audit = audit;
    }

    // ------------------------------------------------------------ 建模板

    /** 从一份已有问卷做出模板的第一版；需要那份问卷上的编辑权。新模板是 draft，还不在模板库里。 */
    public TemplateView createFromSurvey(TenantContext ctx, UUID surveyId, String name, String description) {
        Objects.requireNonNull(ctx, "ctx");
        String cleanName = requireName(name);
        if (surveyId == null) {
            throw new InvalidTemplateRequestException("sourceSurveyId is required");
        }
        UUID id = UUID.randomUUID();
        return tenantScope.call(ctx.tenantId(), () -> {
            access.requireOnSurvey(ctx, Permission.EDIT, surveyId);
            Snapshot snapshot = snapshotOf(ctx, surveyId);
            templates.insert(ctx.tenantId(), id, cleanName, trimmed(description), ctx.actorId());
            templates.insertVersion(ctx.tenantId(), id, 1, snapshot.definition(), surveyId,
                    snapshot.draftVersion(), ctx.actorId());
            templates.appendEvent(ctx.tenantId(), id, "created", ctx.actorId(), 1, null, null);
            audit.record(ctx, TemplateAudit.CREATE, id, "source=" + surveyId);
            return required(templates.find(id), id);
        });
    }

    /** 再从一份问卷做一个新版本。已上架的模板保持上架的那一版，直到新版本通过审核。 */
    public TemplateView addVersionFromSurvey(TenantContext ctx, UUID templateId, UUID surveyId) {
        if (surveyId == null) {
            throw new InvalidTemplateRequestException("sourceSurveyId is required");
        }
        return tenantScope.call(ctx.tenantId(), () -> {
            access.requireOnSurvey(ctx, Permission.EDIT, surveyId);
            required(templates.lock(templateId), templateId);
            Snapshot snapshot = snapshotOf(ctx, surveyId);
            int version = templates.nextVersion(templateId);
            templates.insertVersion(ctx.tenantId(), templateId, version, snapshot.definition(), surveyId,
                    snapshot.draftVersion(), ctx.actorId());
            templates.appendEvent(ctx.tenantId(), templateId, "version_added", ctx.actorId(), version, null, null);
            audit.record(ctx, TemplateAudit.VERSION, templateId, "version=" + version + " source=" + surveyId);
            return required(templates.find(templateId), templateId);
        });
    }

    // ------------------------------------------------------------ 审核

    /**
     * 送审：模板暂时退出模板库，等审核结果。
     *
     * <p>门槛是**最新版本来源问卷上的编辑权**：谁能改那份问卷，谁就能把从它做出来的模板送审。
     * 这样授权仍然走资源树，不必给模板另立一套权限。
     */
    public TemplateView submitForReview(TenantContext ctx, UUID templateId) {
        return tenantScope.call(ctx.tenantId(), () -> {
            TemplateView current = required(templates.lock(templateId), templateId);
            requireAuthorOfLatestVersion(ctx, templateId);
            TemplateView submitted = templates.submit(templateId).orElseThrow(() ->
                    new TemplateConflictException("template_not_submittable",
                            "模板当前是 " + current.status().code() + "，不能送审"));
            templates.appendEvent(ctx.tenantId(), templateId, "submitted", ctx.actorId(),
                    submitted.currentVersion(), null, null);
            audit.record(ctx, TemplateAudit.SUBMIT, templateId, "version=" + submitted.currentVersion());
            return submitted;
        });
    }

    /** 批准：最新版本上架，模板进入模板库。需要租户级的发布审核权。 */
    public TemplateView approve(TenantContext ctx, UUID templateId) {
        return tenantScope.call(ctx.tenantId(), () -> {
            required(templates.lock(templateId), templateId);
            access.requireInTenant(ctx, Permission.APPROVE_PUBLISH);
            TemplateView approved = templates.approve(templateId, ctx.actorId()).orElseThrow(() ->
                    new TemplateConflictException("template_not_pending", "只有待审的模板可以批准"));
            templates.appendEvent(ctx.tenantId(), templateId, "approved", ctx.actorId(),
                    approved.publishedVersion(), null, null);
            audit.record(ctx, TemplateAudit.APPROVE, templateId, "version=" + approved.publishedVersion());
            return approved;
        });
    }

    /** 驳回：必须给原因，模板不进模板库。 */
    public TemplateView reject(TenantContext ctx, UUID templateId, String reason) {
        String cleanReason = requireReason(reason);
        return tenantScope.call(ctx.tenantId(), () -> {
            required(templates.lock(templateId), templateId);
            access.requireInTenant(ctx, Permission.APPROVE_PUBLISH);
            TemplateView rejected = templates.reject(templateId, ctx.actorId(), cleanReason).orElseThrow(() ->
                    new TemplateConflictException("template_not_pending", "只有待审的模板可以驳回"));
            templates.appendEvent(ctx.tenantId(), templateId, "rejected", ctx.actorId(),
                    rejected.currentVersion(), cleanReason, null);
            audit.record(ctx, TemplateAudit.REJECT, templateId, "version=" + rejected.currentVersion());
            return rejected;
        });
    }

    /** 下架：模板离开模板库，之后不能再被复制；已经复制出去的问卷不受影响。 */
    public TemplateView unpublish(TenantContext ctx, UUID templateId, String reason) {
        return tenantScope.call(ctx.tenantId(), () -> {
            required(templates.lock(templateId), templateId);
            access.requireInTenant(ctx, Permission.APPROVE_PUBLISH);
            TemplateView unpublished = templates.unpublish(templateId, ctx.actorId(), trimmed(reason))
                    .orElseThrow(() -> new TemplateConflictException(
                            "template_not_published", "只有在模板库里的模板可以下架"));
            templates.appendEvent(ctx.tenantId(), templateId, "unpublished", ctx.actorId(), null,
                    trimmed(reason), null);
            audit.record(ctx, TemplateAudit.UNPUBLISH, templateId, "");
            return unpublished;
        });
    }

    public TemplateView unpublish(TenantContext ctx, UUID templateId) {
        return unpublish(ctx, templateId, null);
    }

    // ------------------------------------------------------------ 查询

    /** 模板库：本租户已上架的模板、平台已上架的模板，或两者。未上架的模板不在其中。 */
    public List<TemplateView> list(TenantContext ctx, TemplateScope scope) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.requireMember(ctx);
            List<TemplateView> all = new ArrayList<>();
            if (scope != TemplateScope.PLATFORM) {
                all.addAll(templates.published());
            }
            if (scope != TemplateScope.TENANT) {
                all.addAll(platformTemplates.published());
            }
            return List.copyOf(all);
        });
    }

    /** 审核队列：本租户所有待审模板。 */
    public List<TemplateView> pendingForReview(TenantContext ctx) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.requireInTenant(ctx, Permission.APPROVE_PUBLISH);
            return templates.pending();
        });
    }

    /** 模板详情：任意状态都能看（含本租户未上架的），别的租户的一律 404。 */
    public TemplateDetail get(TenantContext ctx, UUID templateId) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.requireMember(ctx);
            return templates.find(templateId)
                    .map(template -> new TemplateDetail(template, templates.versions(templateId),
                            templates.history(templateId)))
                    .orElseGet(() -> platformDetail(templateId));
        });
    }

    /** 某个版本的定义原文。运营模板只给已上架的那一版。 */
    public JsonNode definition(TenantContext ctx, UUID templateId, int versionNo) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.requireMember(ctx);
            return definitions.parse(templates.definition(templateId, versionNo)
                    .or(() -> platformTemplates.publishedDefinition(templateId, versionNo))
                    .orElseThrow(() -> notFound(templateId)));
        });
    }

    // ------------------------------------------------------------ 复制

    /**
     * 从模板新建一份问卷：读上架那一版的快照，换一套实体 id，再经 {@code SurveyService.create}
     * 落成目标父节点下的一份普通草稿（父节点上的编辑权由它判定）。
     *
     * <p>整个过程在同一个租户作用域里，因此与事件记录同一事务：问卷建出来了，历史就一定有那一行。
     */
    public SurveyView copyToSurvey(TenantContext ctx, UUID templateId, UUID parentId, String title) {
        if (parentId == null) {
            throw new InvalidTemplateRequestException("parentId is required");
        }
        return tenantScope.call(ctx.tenantId(), () -> {
            access.requireMember(ctx);
            Source source = copyableSource(templateId);
            ObjectNode definition = templateDefinitions.toSurvey(definitions.parse(source.definition()));
            String cleanTitle = trimmed(title);
            if (cleanTitle != null && !cleanTitle.isEmpty()) {
                definition.put("title", cleanTitle);
            }
            SurveyView survey = surveys.create(ctx, parentId, definition);
            if (source.scope() == TemplateScope.TENANT) {
                templates.appendEvent(ctx.tenantId(), templateId, "copied", ctx.actorId(),
                        source.versionNo(), null, survey.id());
            }
            audit.record(ctx, TemplateAudit.COPY, templateId,
                    "scope=" + source.scope().code() + " version=" + source.versionNo()
                            + " survey=" + survey.id());
            return survey;
        });
    }

    // ------------------------------------------------------------ 零件

    private record Snapshot(String definition, int draftVersion) {
    }

    /**
     * 要求调用方能编辑最新版本的来源问卷。来源问卷被删或本来就没记（历史数据）时退回
     * "是在职成员"——不把人锁在一个无法推进的状态里。
     */
    private void requireAuthorOfLatestVersion(TenantContext ctx, UUID templateId) {
        UUID source = templates.versions(templateId).stream()
                .reduce((first, second) -> second)
                .map(TemplateVersionView::sourceSurveyId)
                .orElse(null);
        if (source == null) {
            access.requireMember(ctx);
            return;
        }
        access.requireOnSurvey(ctx, Permission.EDIT, source);
    }

    private record Source(TemplateScope scope, int versionNo, String definition) {
    }

    /** 读那份问卷的当前草稿（VIEW 判定由 SurveyService 做），剥掉生产数据后序列化。 */
    private Snapshot snapshotOf(TenantContext ctx, UUID surveyId) {
        var draft = surveys.draft(ctx, surveyId);
        ObjectNode stripped = templateDefinitions.toTemplate(draft.definition());
        return new Snapshot(definitions.serialize(stripped), draft.version());
    }

    /** 可复制的来源：先看本租户的模板，再看平台上架的模板；都没有就是 404。 */
    private Source copyableSource(UUID templateId) {
        Optional<TemplateView> tenantTemplate = templates.find(templateId);
        if (tenantTemplate.isPresent()) {
            TemplateView template = tenantTemplate.get();
            if (template.publishedVersion() == null) {
                throw new TemplateConflictException("template_not_published",
                        "模板当前是 " + template.status().code() + "，不能复制");
            }
            int version = template.publishedVersion();
            return new Source(TemplateScope.TENANT, version,
                    templates.definition(templateId, version).orElseThrow(() -> notFound(templateId)));
        }
        TemplateView platform = platformTemplates.find(templateId)
                .filter(template -> template.publishedVersion() != null)
                .orElseThrow(() -> notFound(templateId));
        int version = platform.publishedVersion();
        return new Source(TemplateScope.PLATFORM, version,
                platformTemplates.publishedDefinition(templateId, version)
                        .orElseThrow(() -> notFound(templateId)));
    }

    /** 运营模板的详情：只有已上架的才对租户可见。 */
    private TemplateDetail platformDetail(UUID templateId) {
        TemplateView template = platformTemplates.find(templateId)
                .filter(candidate -> candidate.publishedVersion() != null)
                .orElseThrow(() -> notFound(templateId));
        return new TemplateDetail(template, platformTemplates.versions(templateId), List.of());
    }

    private static TemplateView required(Optional<TemplateView> template, UUID templateId) {
        return template.orElseThrow(() -> notFound(templateId));
    }

    private static TemplateNotFoundException notFound(UUID templateId) {
        return new TemplateNotFoundException("template not found: " + templateId);
    }

    private static String requireName(String name) {
        String clean = trimmed(name);
        if (clean == null || clean.isEmpty()) {
            throw new InvalidTemplateRequestException("name is required");
        }
        if (clean.length() > MAX_NAME_LENGTH) {
            throw new InvalidTemplateRequestException("name is longer than " + MAX_NAME_LENGTH);
        }
        return clean;
    }

    private static String requireReason(String reason) {
        String clean = trimmed(reason);
        if (clean == null || clean.isEmpty()) {
            throw new InvalidTemplateRequestException("a rejection needs a reason");
        }
        if (clean.length() > MAX_REASON_LENGTH) {
            throw new InvalidTemplateRequestException("reason is longer than " + MAX_REASON_LENGTH);
        }
        return clean;
    }

    private static String trimmed(String value) {
        return value == null ? null : value.strip();
    }
}
