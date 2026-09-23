package cn.mjy.platform.contacts;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.Scheduled;

/**
 * 定时推进联系人导入作业。{@code platform.contacts.import.enabled=false} 时整个不装配，
 * 测试直接调用 {@link ContactImportWorker}。fixedDelay 保证同一进程内不重叠；多副本之间靠租约。
 */
@Configuration(proxyBeanMethods = false)
@EnableScheduling
@ConditionalOnProperty(prefix = "platform.contacts.import", name = "enabled", havingValue = "true",
        matchIfMissing = true)
class ContactImportScheduler {

    private static final Logger log = LoggerFactory.getLogger(ContactImportScheduler.class);

    private final ContactImportWorker worker;

    ContactImportScheduler(ContactImportWorker worker) {
        this.worker = worker;
    }

    @Scheduled(fixedDelayString = "${platform.contacts.import.interval:PT10S}",
            initialDelayString = "${platform.contacts.import.initial-delay:PT20S}")
    void tick() {
        try {
            worker.runOnce();
        } catch (RuntimeException e) {
            log.error("contact import round failed", e);
        }
    }
}
