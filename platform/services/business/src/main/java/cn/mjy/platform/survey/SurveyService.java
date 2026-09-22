package cn.mjy.platform.survey;

import cn.mjy.platform.access.AccessResources;
import cn.mjy.platform.access.Permission;
import cn.mjy.platform.access.ResourceKind;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.SurveyRepository.SurveyRow;
import java.util.List;
import java.util.Objects;
import java.util.UUID;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * 租户内的问卷定义：创建、草稿保存（乐观锁）、查询问卷 / 草稿 / 已发布版本。
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

    SurveyService(TenantScope tenantScope, SurveyAccess access, AccessResources resources,
            SurveyDefinitions definitions, SurveyRepository surveys, PublishedVersionRepository versions,
            SurveyViews views, SurveyAudit audit) {
        this.tenantScope = tenantScope;
        this.access = access;
        this.resources = resources;
        this.definitions = definitions;
        this.surveys = surveys;
        this.versions = versions;
        this.views = views;
        this.audit = audit;
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

    /** 保存草稿：expectedVersion 必须等于当前草稿版本，否则 409（并发编辑冲突）。 */
    public DraftView saveDraft(TenantContext ctx, UUID surveyId, int expectedVersion, JsonNode definition) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.require(ctx, Permission.EDIT, surveyId);
            ObjectNode normalized = definitions.normalize(definition, surveyId);
            int saved = surveys.updateDraft(surveyId, expectedVersion, definitions.title(normalized),
                            definitions.serialize(normalized))
                    .orElseThrow(() -> versionConflict(surveyId, expectedVersion));
            audit.record(ctx, SurveyAudit.DRAFT_SAVE, surveyId, "version=" + saved);
            return new DraftView(surveyId, saved, normalized);
        });
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
