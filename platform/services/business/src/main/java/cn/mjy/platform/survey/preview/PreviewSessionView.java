package cn.mjy.platform.survey.preview;

import java.time.OffsetDateTime;
import java.util.UUID;

public record PreviewSessionView(
        UUID id,
        UUID requestId,
        UUID surveyId,
        int draftVersion,
        String requestedBy,
        String engineInstanceId,
        Integer engineSid,
        String generation,
        String previewUrl,
        OffsetDateTime expiresAt,
        String status,
        String failure,
        int cleanupAttempts,
        OffsetDateTime createdAt,
        OffsetDateTime updatedAt,
        OffsetDateTime closedAt) {
}
