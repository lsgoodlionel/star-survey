package cn.mjy.platform.identity.org;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.Scheduled;

/**
 * 定时全量组织同步。只有 {@code platform.identity.org-sync.enabled=true} 时才装配（默认关闭，测试里关闭），
 * 否则只能由管理员按连接触发（{@code POST /v1/org-connections/{id}/sync}）或直接调用
 * {@link OrgDirectorySyncService#runScheduledRound()}。
 */
@Configuration(proxyBeanMethods = false)
@EnableScheduling
@ConditionalOnProperty(prefix = "platform.identity.org-sync", name = "enabled", havingValue = "true")
class OrgDirectorySyncScheduler {

    private static final Logger log = LoggerFactory.getLogger(OrgDirectorySyncScheduler.class);

    private final OrgDirectorySyncService sync;

    OrgDirectorySyncScheduler(OrgDirectorySyncService sync) {
        this.sync = sync;
    }

    @Scheduled(fixedDelayString = "${platform.identity.org-sync.interval:PT6H}",
            initialDelayString = "${platform.identity.org-sync.initial-delay:PT10M}")
    void tick() {
        try {
            OrgDirectorySyncService.SyncResult result = sync.runScheduledRound();
            log.info("scheduled organisation sync: checked {}, revoked {}, unknown {}", result.checked(),
                    result.revoked(), result.unknown());
        } catch (RuntimeException e) {
            log.error("scheduled organisation sync round failed", e);
        }
    }
}
