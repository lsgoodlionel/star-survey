package cn.mjy.platform.identity.org;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * 组织同步配置（前缀 {@code platform.identity.org-sync}）。定时全量同步的开关与间隔由
 * {@link OrgDirectorySyncScheduler} 读取（默认关闭，测试里关闭）；batchSize 同时用于管理员触发的同步。
 *
 * @param enabled   是否启用定时全量同步
 * @param batchSize 每页核对的绑定数（按建立时间与主体键集分页）
 */
@ConfigurationProperties("platform.identity.org-sync")
public record OrgSyncProperties(
        @DefaultValue("false") boolean enabled,
        @DefaultValue("200") int batchSize) {

    public OrgSyncProperties {
        if (batchSize <= 0) {
            throw new IllegalArgumentException("platform.identity.org-sync.batch-size must be positive");
        }
    }
}
