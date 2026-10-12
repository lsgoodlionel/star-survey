package cn.mjy.platform.dashboard;

public record DashboardSummary(
        long pendingApprovals,
        long publishExceptions,
        long activePreviews,
        long activeExports) {
}
