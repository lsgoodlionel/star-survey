package cn.mjy.platform.tenant.api;

/** 本车道端点统一的错误体：机器可读的 error 码 + 给人看的 message。 */
public record ApiError(String error, String message) {
}
