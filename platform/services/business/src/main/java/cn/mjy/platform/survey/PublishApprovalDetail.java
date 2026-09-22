package cn.mjy.platform.survey;

import java.util.List;

/** 申请及其完整决定历史（按发生顺序）。 */
public record PublishApprovalDetail(PublishApprovalView request, List<PublishApprovalEventView> history) {

    public PublishApprovalDetail {
        history = List.copyOf(history);
    }
}
