package cn.mjy.platform.survey;

import java.time.Instant;
import java.util.UUID;

/**
 * 一条发布审批申请。draftVersion 是提交时绑定的草稿版本；decidedBy / decidedAt 为最近一次状态变化的操作者与时间
 * （批准、驳回、撤回；作废与发布由其他动作引起时记录触发者）；reason 仅驳回或作废时有值。
 */
public record PublishApprovalView(
        UUID id,
        UUID surveyId,
        int draftVersion,
        PublishApprovalStatus status,
        String applicant,
        Instant submittedAt,
        String decidedBy,
        Instant decidedAt,
        String reason) {
}
