package cn.mjy.platform.response;

/** 导出收尾超过配置的资源预算；属于确定性失败，不应重试。 */
final class ExportBudgetExceededException extends RuntimeException {
    ExportBudgetExceededException(String message) {
        super(message);
    }
}
