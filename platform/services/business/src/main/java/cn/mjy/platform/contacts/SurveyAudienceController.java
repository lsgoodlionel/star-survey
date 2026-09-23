package cn.mjy.platform.contacts;

import cn.mjy.platform.shared.security.CurrentTenant;
import java.util.List;
import java.util.UUID;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 一份问卷的受众：选定要邀请哪一批联系人。发布时按这条规则物化成参与者（ADR 0016 邀请码）。
 * 只返回条件与人数，<b>不返回名册</b>——个人信息走通讯录自己的接口，按数据范围逐页取。
 */
@RestController
@RequestMapping("/v1/surveys/{surveyId}/audience")
class SurveyAudienceController {

    /** 受众条件；三项至少给一项。{@code includeDescendants} 不给按 true。 */
    record AudienceBody(UUID listId, UUID orgUnitId, Boolean includeDescendants, String tag) {
    }

    /** 条件 + 按调用者数据范围当前能邀请到多少人。 */
    record AudienceSummary(SurveyAudienceView audience, int recipients) {
    }

    private final CurrentTenant currentTenant;
    private final SurveyAudienceService audiences;

    SurveyAudienceController(CurrentTenant currentTenant, SurveyAudienceService audiences) {
        this.currentTenant = currentTenant;
        this.audiences = audiences;
    }

    @PutMapping
    AudienceSummary set(@PathVariable UUID surveyId, @RequestBody AudienceBody body) {
        SurveyAudienceView saved = audiences.set(currentTenant.require(), surveyId, body.listId(),
                body.orgUnitId(), body.includeDescendants() == null || body.includeDescendants(), body.tag());
        return summary(surveyId, saved);
    }

    @GetMapping
    ResponseEntity<AudienceSummary> get(@PathVariable UUID surveyId) {
        return audiences.find(currentTenant.require(), surveyId)
                .map(audience -> ResponseEntity.ok(summary(surveyId, audience)))
                .orElseGet(() -> ResponseEntity.notFound().build());
    }

    @DeleteMapping
    ResponseEntity<Void> clear(@PathVariable UUID surveyId) {
        audiences.clear(currentTenant.require(), surveyId);
        return ResponseEntity.noContent().build();
    }

    private AudienceSummary summary(UUID surveyId, SurveyAudienceView audience) {
        List<ContactView> recipients = audiences.recipients(currentTenant.require(), surveyId);
        return new AudienceSummary(audience, recipients.size());
    }
}
