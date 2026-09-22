package cn.mjy.platform.survey;

import cn.mjy.platform.survey.SurveyRepository.SurveyRow;
import org.springframework.stereotype.Component;

/** 由一行问卷拼出对外视图（附最近一次发布尝试）。必须在 {@code TenantScope} 内调用。 */
@Component
class SurveyViews {

    private final PublishAttemptRepository attempts;

    SurveyViews(PublishAttemptRepository attempts) {
        this.attempts = attempts;
    }

    SurveyView of(SurveyRow row) {
        PublishAttemptView lastPublish = row.currentRequestId() == null
                ? null
                : attempts.find(row.currentRequestId()).map(PublishAttemptRepository.AttemptRow::view).orElse(null);
        return new SurveyView(row.id(), row.title(), row.status(), row.draftVersion(), row.publishedVersion(),
                lastPublish);
    }
}
