package cn.mjy.platform.survey.preview;

import cn.mjy.platform.shared.security.CurrentTenant;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotNull;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class PreviewSessionController {

    private final PreviewSessionService previews;
    private final CurrentTenant currentTenant;

    PreviewSessionController(PreviewSessionService previews, CurrentTenant currentTenant) {
        this.previews = previews;
        this.currentTenant = currentTenant;
    }

    public record CreatePreview(@NotNull UUID requestId,
            @NotNull @Min(PreviewSessionService.MIN_TTL_SECONDS)
            @Max(PreviewSessionService.MAX_TTL_SECONDS) Integer ttlSeconds) {
    }

    @PostMapping("/v1/surveys/{id}/preview-sessions")
    ResponseEntity<PreviewSessionView> create(@PathVariable UUID id,
            @Valid @RequestBody CreatePreview request) {
        PreviewSessionService.Created created = previews.create(
                currentTenant.require(), id, request.requestId(), request.ttlSeconds());
        HttpStatus status = created.fresh() && "ready".equals(created.session().status())
                ? HttpStatus.CREATED : statusOf(created.session());
        return ResponseEntity.status(status)
                .body(created.session());
    }

    @GetMapping("/v1/preview-sessions/{id}")
    PreviewSessionView get(@PathVariable UUID id) {
        return previews.get(currentTenant.require(), id);
    }

    @DeleteMapping("/v1/preview-sessions/{id}")
    ResponseEntity<PreviewSessionView> close(@PathVariable UUID id) {
        PreviewSessionView result = previews.close(currentTenant.require(), id);
        return ResponseEntity.status(statusOf(result)).body(result);
    }

    private static HttpStatus statusOf(PreviewSessionView view) {
        return switch (view.status()) {
            case "creating", "closing" -> HttpStatus.ACCEPTED;
            case "failed", "cleanup_failed" -> HttpStatus.BAD_GATEWAY;
            default -> HttpStatus.OK;
        };
    }
}
