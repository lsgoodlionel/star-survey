package cn.mjy.platform.delivery;

import cn.mjy.platform.shared.security.CurrentTenant;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import java.time.Duration;
import java.time.Instant;
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

/** 租户端：催答规则、新答卷通知规则，以及完成情况的显式登记。 */
@RestController
@RequestMapping("/v1/delivery/surveys/{surveyId}")
public class DeliveryRulesController {

    private final ReminderService reminders;
    private final NotificationService notifications;
    private final CompletionService completions;
    private final CurrentTenant currentTenant;

    DeliveryRulesController(ReminderService reminders, NotificationService notifications,
            CompletionService completions, CurrentTenant currentTenant) {
        this.reminders = reminders;
        this.notifications = notifications;
        this.completions = completions;
        this.currentTenant = currentTenant;
    }

    /** 建催答规则：首邀发出 firstDelaySeconds 之后第一次催，此后每 intervalSeconds 一次，最多 maxReminders 次。 */
    public record CreateReminderRule(
            @NotNull UUID sourceTaskId,
            @NotNull @Min(0) @Max(2_592_000) Long firstDelaySeconds,
            @NotNull @Min(1) @Max(2_592_000) Long intervalSeconds,
            @Min(1) @Max(10) int maxReminders,
            @Size(max = 200) String subject,
            @NotBlank @Size(max = 4000) String bodyTemplate) {
    }

    @PostMapping("/reminder-rules")
    ResponseEntity<ReminderRuleView> createReminderRule(@PathVariable UUID surveyId,
            @Valid @RequestBody CreateReminderRule request) {
        ReminderRuleView rule = reminders.create(currentTenant.require(), surveyId,
                new ReminderService.NewRule(request.sourceTaskId(),
                        Duration.ofSeconds(request.firstDelaySeconds()),
                        Duration.ofSeconds(request.intervalSeconds()),
                        request.maxReminders(), request.subject(), request.bodyTemplate()));
        return ResponseEntity.status(201).body(rule);
    }

    @GetMapping("/reminder-rules")
    List<ReminderRuleView> reminderRules(@PathVariable UUID surveyId) {
        return reminders.list(currentTenant.require(), surveyId);
    }

    /** 建新答卷通知规则：通知本租户的某个成员，最小间隔内的新增合并成一条。 */
    public record CreateNotificationRule(
            @NotBlank @Size(max = 256) String actorId,
            @NotBlank String channel,
            @NotBlank @Size(max = 320) String address,
            @NotNull @Min(60) @Max(86_400) Long minIntervalSeconds) {
    }

    @PostMapping("/notification-rules")
    ResponseEntity<NotificationRuleView> createNotificationRule(@PathVariable UUID surveyId,
            @Valid @RequestBody CreateNotificationRule request) {
        DeliveryChannel channel = DeliveryChannel.fromCode(request.channel())
                .orElseThrow(() -> DeliveryExceptions.invalid("channel must be email or sms"));
        NotificationRuleView rule = notifications.create(currentTenant.require(), surveyId,
                new NotificationService.NewRule(request.actorId(), channel, request.address(),
                        Duration.ofSeconds(request.minIntervalSeconds())));
        return ResponseEntity.status(201).body(rule);
    }

    @GetMapping("/notification-rules")
    List<NotificationRuleView> notificationRules(@PathVariable UUID surveyId) {
        return notifications.list(currentTenant.require(), surveyId);
    }

    /**
     * 显式登记"某个应答者已完成"。在引擎侧的邀请码回读通道接上之前，这是催答判断的唯一事实来源
     * （见 {@link RespondentIdentityResolver}）。
     */
    public record RecordCompletion(@NotBlank @Size(max = 64) String respondentKey, String completedAt) {
    }

    @PostMapping("/completions")
    Map<String, Object> recordCompletion(@PathVariable UUID surveyId,
            @Valid @RequestBody RecordCompletion request) {
        Instant completedAt = request.completedAt() == null || request.completedAt().isBlank()
                ? null
                : parse(request.completedAt());
        boolean recorded = completions.record(currentTenant.require(), surveyId, request.respondentKey(),
                completedAt);
        return Map.of("recorded", recorded);
    }

    private static Instant parse(String value) {
        try {
            return Instant.parse(value);
        } catch (RuntimeException e) {
            throw DeliveryExceptions.invalid("completedAt is not an ISO-8601 instant");
        }
    }
}
