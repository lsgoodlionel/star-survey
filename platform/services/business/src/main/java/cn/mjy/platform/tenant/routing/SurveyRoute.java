package cn.mjy.platform.tenant.routing;

import cn.mjy.platform.shared.TenantId;
import java.util.UUID;

/**
 * 问卷路由：对外只暴露 {@code publicId}；{@code engineSid} 只在所属引擎实例内有意义（ADR 0002 决定 6），
 * 两个租户出现同号 sid 是正常状态。
 */
public record SurveyRoute(UUID publicId, TenantId tenantId, String engineInstanceId, int engineSid) {
}
