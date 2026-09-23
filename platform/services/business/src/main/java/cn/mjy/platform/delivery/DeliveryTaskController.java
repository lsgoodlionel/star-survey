package cn.mjy.platform.delivery;

import cn.mjy.platform.delivery.DeliveryTaskService.NewRecipient;
import cn.mjy.platform.delivery.DeliveryTaskService.NewTask;
import cn.mjy.platform.shared.security.CurrentTenant;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 租户端：批量投放任务。
 *
 * <p>名单只进不出：任务详情与收件人列表返回的地址一律脱敏（{@link Redaction}）。
 */
@RestController
@RequestMapping("/v1/delivery/tasks")
public class DeliveryTaskController {

    private final DeliveryTaskService tasks;
    private final CurrentTenant currentTenant;

    DeliveryTaskController(DeliveryTaskService tasks, CurrentTenant currentTenant) {
        this.tasks = tasks;
        this.currentTenant = currentTenant;
    }

    /** 名单里的一行。invitationCode 即引擎参与者 token（发布后从引擎回读得到）。 */
    public record Recipient(
            @NotBlank @Size(max = 320) String address,
            @Size(max = 128) String displayName,
            @Size(max = 64) String invitationCode,
            Map<String, String> params) {
    }

    /** 建任务。 */
    public record CreateTask(
            @NotNull UUID surveyId,
            @NotBlank String channel,
            UUID linkId,
            @Size(max = 200) String subject,
            @NotBlank @Size(max = 4000) String bodyTemplate,
            @NotEmpty List<@Valid Recipient> recipients,
            @Size(max = 128) String idempotencyKey) {
    }

    @PostMapping
    ResponseEntity<TaskView> create(@Valid @RequestBody CreateTask request) {
        DeliveryChannel channel = DeliveryChannel.fromCode(request.channel())
                .orElseThrow(() -> DeliveryExceptions.invalid("channel must be email or sms"));
        List<NewRecipient> recipients = request.recipients().stream()
                .map(row -> new NewRecipient(row.address(), row.displayName(), row.invitationCode(), row.params()))
                .toList();
        TaskView task = tasks.create(currentTenant.require(), new NewTask(request.surveyId(), channel,
                request.linkId(), request.subject(), request.bodyTemplate(), recipients,
                request.idempotencyKey()));
        return ResponseEntity.status(201).body(task);
    }

    @GetMapping("/{taskId}")
    TaskView status(@PathVariable UUID taskId) {
        return tasks.status(currentTenant.require(), taskId);
    }

    @GetMapping("/{taskId}/recipients")
    List<RecipientView> recipients(@PathVariable UUID taskId) {
        return tasks.recipients(currentTenant.require(), taskId);
    }

    @PostMapping("/{taskId}/cancel")
    TaskView cancel(@PathVariable UUID taskId) {
        return tasks.cancel(currentTenant.require(), taskId);
    }
}
