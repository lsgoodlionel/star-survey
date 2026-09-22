package cn.mjy.platform.response;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.Scheduled;

/**
 * 定时推进导出作业并清理到期文件。{@code platform.response.export.enabled=false} 时整个不装配，
 * 测试直接调用 {@link ResponseExportWorker}。fixedDelay：同一进程内不重叠；多副本之间靠租约。
 */
@Configuration(proxyBeanMethods = false)
@EnableScheduling
@ConditionalOnProperty(prefix = "platform.response.export", name = "enabled", havingValue = "true",
        matchIfMissing = true)
class ResponseExportScheduler {

    private static final Logger log = LoggerFactory.getLogger(ResponseExportScheduler.class);

    private final ResponseExportWorker worker;

    ResponseExportScheduler(ResponseExportWorker worker) {
        this.worker = worker;
    }

    @Scheduled(fixedDelayString = "${platform.response.export.interval:PT10S}",
            initialDelayString = "${platform.response.export.initial-delay:PT20S}")
    void tick() {
        try {
            worker.runOnce();
        } catch (RuntimeException e) {
            log.error("response export round failed", e);
        }
        try {
            worker.expireDue();
        } catch (RuntimeException e) {
            log.error("response export expiry round failed", e);
        }
    }
}
