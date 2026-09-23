package cn.mjy.platform.delivery;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.Scheduled;

/**
 * 唯一的投放定时任务：一轮里依次推进发送、对账完成、生成催答、生成新答卷通知。
 *
 * <p><b>默认关闭</b>（{@code platform.delivery.enabled} 缺省为 false，测试同样关闭）：
 * 真实渠道商凭据尚未接入，不该有后台线程去发东西。关闭时本类整个不装配（连同调度设施），
 * 任务只能手动调用 {@link DeliveryWorker}——测试即如此，避免后台线程与测试抢数据。
 *
 * <p>fixedDelay：上一轮结束后才开始计时，同一进程内不会重叠；多副本之间靠收件人行锁
 * （FOR UPDATE SKIP LOCKED）与发送台账的唯一键保证不重复发送。
 */
@Configuration(proxyBeanMethods = false)
@EnableScheduling
@ConditionalOnProperty(prefix = "platform.delivery", name = "enabled", havingValue = "true")
class DeliveryScheduler {

    private static final Logger log = LoggerFactory.getLogger(DeliveryScheduler.class);

    private final DeliveryWorker worker;

    DeliveryScheduler(DeliveryWorker worker) {
        this.worker = worker;
    }

    @Scheduled(fixedDelayString = "${platform.delivery.interval:PT10S}",
            initialDelayString = "${platform.delivery.initial-delay:PT30S}")
    void tick() {
        try {
            worker.runOnce();
        } catch (RuntimeException e) {
            log.error("delivery round failed", e);
        }
    }
}
