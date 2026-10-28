package cn.mjy.platform.delivery;

import cn.mjy.platform.delivery.DeliveryTaskService.NewTask;
import cn.mjy.platform.shared.security.CurrentTenant;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import java.util.UUID;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** 从已保存的问卷受众创建投放任务；请求体刻意不接受联系人地址。 */
@RestController
@RequestMapping("/v1/surveys/{surveyId}/delivery-tasks")
class SurveyAudienceDeliveryController {

    record CreateAudienceTask(@NotBlank String channel, UUID linkId, @Size(max = 200) String subject,
            @NotBlank @Size(max = 4000) String bodyTemplate, @Size(max = 128) String idempotencyKey) {
    }

    private final DeliveryTaskService tasks;
    private final CurrentTenant currentTenant;

    SurveyAudienceDeliveryController(DeliveryTaskService tasks, CurrentTenant currentTenant) {
        this.tasks = tasks;
        this.currentTenant = currentTenant;
    }

    @PostMapping
    ResponseEntity<TaskView> create(@PathVariable UUID surveyId, @Valid @RequestBody CreateAudienceTask request) {
        DeliveryChannel channel = DeliveryChannel.fromCode(request.channel())
                .orElseThrow(() -> DeliveryExceptions.invalid("channel must be email or sms"));
        TaskView task = tasks.createFromAudience(currentTenant.require(), new NewTask(surveyId, channel,
                request.linkId(), request.subject(), request.bodyTemplate(), null, request.idempotencyKey()));
        return ResponseEntity.status(201).body(task);
    }
}
