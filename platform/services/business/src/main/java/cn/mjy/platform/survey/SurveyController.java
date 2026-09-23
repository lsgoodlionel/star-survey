package cn.mjy.platform.survey;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.security.CurrentTenant;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;
import java.util.List;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;

/**
 * 租户端问卷接口。租户与操作者只取自已验签令牌。
 *
 * <p>发布的 HTTP 状态：published 200；publish_failed 按网关结局 422（定义被拒）或 502（引擎失败 / 网关拒收）；
 * pending_reconciliation 202（结果未知，再次调用即以同一 requestId 核对）。
 */
@RestController
@RequestMapping("/v1/surveys")
public class SurveyController {

    private static final int REJECTED_DEFINITION = 422;

    private final SurveyService surveys;
    private final SurveyPublishService publisher;
    private final CurrentTenant currentTenant;

    public SurveyController(SurveyService surveys, SurveyPublishService publisher, CurrentTenant currentTenant) {
        this.surveys = surveys;
        this.publisher = publisher;
        this.currentTenant = currentTenant;
    }

    public record CreateSurvey(@NotNull UUID parentId, @NotNull JsonNode definition) {
    }

    public record SaveDraft(@NotNull @Positive Integer expectedVersion, @NotNull JsonNode definition) {
    }

    /** 恢复旧版：expectedVersion 是当前草稿的乐观锁版本，与保存草稿同一把锁。 */
    public record RestoreVersion(@NotNull @Positive Integer expectedVersion) {
    }

    /**
     * 批量文本导入的预览请求：只有原文，不写库。
     * 长度在反序列化之后立刻挡一道（解析器里还有按字节与按行数的精确上限）。
     */
    public record PreviewImport(@NotBlank @Size(max = QuestionTextParser.MAX_TEXT_BYTES) String text) {
    }

    /**
     * 确认导入：accept 是预览里要保留的题目序号；groupUuid 为空时新建一个分组。
     * expectedVersion 与保存草稿同一把乐观锁。
     */
    public record ImportQuestions(@NotNull @Positive Integer expectedVersion,
            @NotBlank @Size(max = QuestionTextParser.MAX_TEXT_BYTES) String text,
            @NotEmpty @Size(max = QuestionTextParser.MAX_QUESTIONS) List<@NotNull Integer> accept,
            String groupUuid) {
    }

    @PostMapping
    public ResponseEntity<SurveyView> create(@Valid @RequestBody CreateSurvey request) {
        TenantContext ctx = currentTenant.require();
        return ResponseEntity.status(HttpStatus.CREATED)
                .body(surveys.create(ctx, request.parentId(), request.definition()));
    }

    @GetMapping("/{id}")
    public SurveyView get(@PathVariable UUID id) {
        return surveys.get(currentTenant.require(), id);
    }

    @GetMapping("/{id}/draft")
    public DraftView draft(@PathVariable UUID id) {
        return surveys.draft(currentTenant.require(), id);
    }

    @PutMapping("/{id}/draft")
    public DraftView saveDraft(@PathVariable UUID id, @Valid @RequestBody SaveDraft request) {
        return surveys.saveDraft(currentTenant.require(), id, request.expectedVersion(), request.definition());
    }

    @PostMapping("/{id}/publish")
    public ResponseEntity<PublishOutcome> publish(@PathVariable UUID id) {
        PublishOutcome outcome = publisher.publish(currentTenant.require(), id);
        return ResponseEntity.status(statusOf(outcome.survey())).body(outcome);
    }

    @GetMapping("/{id}/versions")
    public List<PublishedVersionView> versions(@PathVariable UUID id) {
        return surveys.versions(currentTenant.require(), id);
    }

    @GetMapping("/{id}/versions/{version}")
    public PublishedVersionView version(@PathVariable UUID id, @PathVariable int version) {
        return surveys.version(currentTenant.require(), id, version);
    }

    /** 批量文本导入的预览（需要编辑权）：解析原文并返回题目、题型与坏行，不写库。 */
    @PostMapping("/{id}/import/preview")
    public SurveyImportPreview previewImport(@PathVariable UUID id, @Valid @RequestBody PreviewImport request) {
        return surveys.previewImport(currentTenant.require(), id, request.text());
    }

    /** 确认导入：把预览里被选中的题目并进草稿。 */
    @PostMapping("/{id}/import")
    public DraftView importQuestions(@PathVariable UUID id, @Valid @RequestBody ImportQuestions request) {
        return surveys.importQuestions(currentTenant.require(), id, request.expectedVersion(), request.text(),
                request.accept(), request.groupUuid());
    }

    /** 把第 version 版恢复为当前草稿（需要编辑权）。只改草稿：在线版本与公开路由不动。 */
    @PostMapping("/{id}/versions/{version}/restore")
    public DraftView restoreVersion(@PathVariable UUID id, @PathVariable int version,
            @Valid @RequestBody RestoreVersion request) {
        return surveys.restore(currentTenant.require(), id, version, request.expectedVersion());
    }

    private static HttpStatus statusOf(SurveyView survey) {
        return switch (survey.status()) {
            case PUBLISHED -> HttpStatus.OK;
            case PUBLISH_FAILED -> isRejectedDefinition(survey) ? HttpStatus.UNPROCESSABLE_CONTENT : HttpStatus.BAD_GATEWAY;
            case PENDING_RECONCILIATION, PUBLISHING, DRAFT -> HttpStatus.ACCEPTED;
        };
    }

    private static boolean isRejectedDefinition(SurveyView survey) {
        return survey.lastPublish() != null && survey.lastPublish().gatewayStatus() != null
                && survey.lastPublish().gatewayStatus() == REJECTED_DEFINITION;
    }
}
