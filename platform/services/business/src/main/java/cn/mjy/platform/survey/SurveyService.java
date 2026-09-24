package cn.mjy.platform.survey;

import cn.mjy.platform.access.AccessResources;
import cn.mjy.platform.access.Permission;
import cn.mjy.platform.access.ResourceKind;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.SurveyRepository.SurveyRow;
import java.util.List;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * 租户内的问卷定义：创建、草稿保存（乐观锁）、批量文本导入、旧版恢复、查询问卷 / 草稿 / 已发布版本。
 * 每个操作都在调用方租户的 {@link TenantScope} 内完成，权限统一经 access 模块判定。
 */
@Service
public class SurveyService {

    private final TenantScope tenantScope;
    private final SurveyAccess access;
    private final AccessResources resources;
    private final SurveyDefinitions definitions;
    private final SurveyRepository surveys;
    private final PublishedVersionRepository versions;
    private final SurveyViews views;
    private final SurveyAudit audit;
    private final PublishApprovalGate approvalGate;
    private final QuestionImports imports;

    SurveyService(TenantScope tenantScope, SurveyAccess access, AccessResources resources,
            SurveyDefinitions definitions, SurveyRepository surveys, PublishedVersionRepository versions,
            SurveyViews views, SurveyAudit audit, PublishApprovalGate approvalGate, QuestionImports imports) {
        this.tenantScope = tenantScope;
        this.access = access;
        this.resources = resources;
        this.definitions = definitions;
        this.surveys = surveys;
        this.versions = versions;
        this.views = views;
        this.audit = audit;
        this.approvalGate = approvalGate;
        this.imports = imports;
    }

    /**
     * 在项目或文件夹下新建问卷（需要父节点上的编辑权限）。公开 UUID 由服务端生成，
     * 同时登记为资源树上的问卷节点，授权才能落在它上面。
     */
    public SurveyView create(TenantContext ctx, UUID parentId, JsonNode definition) {
        Objects.requireNonNull(ctx, "ctx");
        if (parentId == null) {
            throw new InvalidSurveyRequestException("parentId is required");
        }
        UUID id = UUID.randomUUID();
        return tenantScope.call(ctx.tenantId(), () -> {
            access.require(ctx, Permission.EDIT, parentId);
            ObjectNode normalized = definitions.normalize(definition, id);
            String title = definitions.title(normalized);
            registerResource(ctx, id, parentId, title);
            surveys.insert(ctx.tenantId(), id, title, definitions.serialize(normalized), ctx.actorId());
            audit.record(ctx, SurveyAudit.CREATE, id, "parent=" + parentId);
            return views.of(requireSurvey(id));
        });
    }

    /**
     * 保存草稿：expectedVersion 必须等于当前草稿版本，否则 409（并发编辑冲突）。
     * 保存成功即作废该问卷的未结发布申请（ADR 0011：批准绑定草稿版本），与保存同一事务、同一把行锁。
     */
    public DraftView saveDraft(TenantContext ctx, UUID surveyId, int expectedVersion, JsonNode definition) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.require(ctx, Permission.EDIT, surveyId);
            ObjectNode normalized = definitions.normalize(definition, surveyId);
            return writeDraft(ctx, surveyId, expectedVersion, normalized, SurveyAudit.DRAFT_SAVE, "");
        });
    }

    /**
     * 旧版恢复（WP-01 01.4）：把第 {@code versionNo} 版发布时的定义取回来当作当前草稿。
     *
     * <p>与 ADR 0012 的语义一致——<b>只写草稿</b>：已发布版本仍不可变、公开路由仍指向原来的在线版本、
     * 引擎里的问卷一个都不动（不调网关），因此已发布版本的答卷不受任何影响。旧内容要重新上线，
     * 照常走"发布"，于是它成为一个全新的版本（新引擎问卷、新 sid），历史版本与其答卷原样保留。
     *
     * <p>并发保护与 {@link #saveDraft} 相同：expectedVersion 必须等于当前草稿版本，否则 409，
     * 并发的恢复与保存只有一个能赢。恢复同样算改稿，未结的发布申请随之作废。
     *
     * <p>历史定义按当下的规则重新校验：校验规则收紧后恢复不出去的旧稿宁可 422 报错，也不静默写入坏草稿。
     */
    public DraftView restore(TenantContext ctx, UUID surveyId, int versionNo, int expectedVersion) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.require(ctx, Permission.EDIT, surveyId);
            // 资源树里的项目 / 文件夹也能通过 EDIT 判定，必须确认它确实是问卷，否则会 404 在版本上而不是问卷上。
            requireSurvey(surveyId);
            PublishedVersionView source = versions.find(surveyId, versionNo)
                    .orElseThrow(() -> new SurveyNotFoundException(
                            "published version " + versionNo + " of survey " + surveyId + " not found"));
            ObjectNode normalized = definitions.normalize(source.definition(), surveyId);
            return writeDraft(ctx, surveyId, expectedVersion, normalized, SurveyAudit.DRAFT_RESTORE,
                    "from=" + versionNo);
        });
    }

    /**
     * 批量文本导入的预览（WP-01 01.1，需求 R01-02）：只解析、不写库。
     * 题目代码按当前草稿已用过的代码往后排，作者看到的题号、顺序与代码就是确认后写进去的那一份。
     */
    public SurveyImportPreview previewImport(TenantContext ctx, UUID surveyId, String text) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.require(ctx, Permission.EDIT, surveyId);
            SurveyRow row = requireSurvey(surveyId);
            return imports.preview(definitions.parse(row.draftDefinition()), text);
        });
    }

    /**
     * 确认导入：按预览里的序号 {@code accept} 取舍，把选中的题目并进草稿。
     * 没指定 {@code groupUuid} 时新建一个分组，指定时追加到该分组末尾。
     * 并发保护与保存草稿相同（expectedVersion），因此预览之后草稿若被别人改过，这次导入会 409 而不是按过时的编号写入。
     */
    public DraftView importQuestions(TenantContext ctx, UUID surveyId, int expectedVersion, String text,
            List<Integer> accept, String groupUuid) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.require(ctx, Permission.EDIT, surveyId);
            SurveyRow row = requireSurvey(surveyId);
            JsonNode draft = definitions.parse(row.draftDefinition());
            if (!(draft instanceof ObjectNode current)) {
                throw new InvalidSurveyRequestException("draft definition is not a JSON object");
            }
            ObjectNode merged = imports.merge(current, text, accept, groupUuid);
            ObjectNode normalized = definitions.normalize(merged, surveyId);
            return writeDraft(ctx, surveyId, expectedVersion, normalized, SurveyAudit.DRAFT_IMPORT,
                    "imported=" + Set.copyOf(accept).size());
        });
    }

    /** 乐观锁写入草稿并记审计、作废未结申请。调用方已在租户作用域内完成权限判定与定义规范化。 */
    private DraftView writeDraft(TenantContext ctx, UUID surveyId, int expectedVersion, ObjectNode normalized,
            String action, String details) {
        int saved = surveys.updateDraft(surveyId, expectedVersion, definitions.title(normalized),
                        definitions.serialize(normalized))
                .orElseThrow(() -> versionConflict(surveyId, expectedVersion));
        audit.record(ctx, action, surveyId, "version=" + saved + (details.isEmpty() ? "" : " " + details));
        approvalGate.voidOnDraftSave(ctx, surveyId, saved);
        return new DraftView(surveyId, saved, normalized);
    }

    public SurveyView get(TenantContext ctx, UUID surveyId) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.require(ctx, Permission.VIEW, surveyId);
            return views.of(requireSurvey(surveyId));
        });
    }

    public DraftView draft(TenantContext ctx, UUID surveyId) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.require(ctx, Permission.VIEW, surveyId);
            SurveyRow row = requireSurvey(surveyId);
            return new DraftView(surveyId, row.draftVersion(), definitions.parse(row.draftDefinition()));
        });
    }

    public List<PublishedVersionView> versions(TenantContext ctx, UUID surveyId) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.require(ctx, Permission.VIEW, surveyId);
            // 资源树里的项目 / 文件夹也能通过 VIEW 判定，必须确认它确实是问卷，否则会返回空列表而不是 404。
            requireSurvey(surveyId);
            return versions.list(surveyId);
        });
    }

    public PublishedVersionView version(TenantContext ctx, UUID surveyId, int versionNo) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.require(ctx, Permission.VIEW, surveyId);
            return versions.find(surveyId, versionNo).orElseThrow(() -> new SurveyNotFoundException(
                    "published version " + versionNo + " of survey " + surveyId + " not found"));
        });
    }

    private void registerResource(TenantContext ctx, UUID id, UUID parentId, String title) {
        try {
            resources.register(ctx.tenantId(), id, ResourceKind.SURVEY, parentId, title);
        } catch (IllegalArgumentException e) {
            // 父节点是问卷（叶子）等结构性错误；父节点不存在的情况已由上面的权限判定挡成 404。
            throw new InvalidSurveyRequestException(e.getMessage());
        }
    }

    private SurveyRow requireSurvey(UUID surveyId) {
        return surveys.find(surveyId).orElseThrow(() -> new SurveyNotFoundException("survey not found: " + surveyId));
    }

    private SurveyConflictException versionConflict(UUID surveyId, int expectedVersion) {
        SurveyRow current = requireSurvey(surveyId);
        return new SurveyConflictException(SurveyConflictException.VERSION_CONFLICT,
                "draft version is " + current.draftVersion() + ", expected " + expectedVersion);
    }
}
