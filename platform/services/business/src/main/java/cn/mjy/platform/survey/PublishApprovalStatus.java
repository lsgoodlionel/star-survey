package cn.mjy.platform.survey;

import com.fasterxml.jackson.annotation.JsonValue;
import java.util.Locale;

/**
 * 发布审批申请的状态机（与 V510 的 CHECK 约束一致）：
 * <pre>
 * pending ──> approved ──> published（批准的草稿版本发布成功）
 *    │           ├──> voided（草稿再次保存）
 *    │           └──> withdrawn（申请人撤回）
 *    ├──> rejected（审批人驳回，须给原因）
 *    ├──> withdrawn（申请人撤回）
 *    └──> voided（草稿再次保存 / 问卷已按别的途径发布）
 * </pre>
 * pending 与 approved 是"未结"状态，同一问卷同一时刻最多一条（部分唯一索引保证）；其余都是终态。
 */
public enum PublishApprovalStatus {
    PENDING,
    APPROVED,
    REJECTED,
    WITHDRAWN,
    VOIDED,
    PUBLISHED;

    @JsonValue
    public String code() {
        return name().toLowerCase(Locale.ROOT);
    }

    public boolean isOpen() {
        return this == PENDING || this == APPROVED;
    }

    public static PublishApprovalStatus fromCode(String code) {
        for (PublishApprovalStatus status : values()) {
            if (status.code().equals(code)) {
                return status;
            }
        }
        throw new IllegalArgumentException("unknown approval status: " + code);
    }
}
