package cn.mjy.platform.survey;

import java.time.Instant;
import java.util.UUID;

/**
 * 决定历史中的一条（只追加）。event：submitted / approved / rejected / withdrawn / voided /
 * publish_started / published；publishRequestId 只在与发布尝试相关的事件上有值。
 */
public record PublishApprovalEventView(
        String event,
        String actor,
        int draftVersion,
        String reason,
        UUID publishRequestId,
        Instant at) {
}
