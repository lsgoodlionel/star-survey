package cn.mjy.platform.survey;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.Scheduled;

/**
 * 定时触发发布核对。{@code platform.survey.reconcile.enabled=false} 时本类整个不装配（连同调度设施），
 * 任务只能手动调用 {@link PublishReconciler#runOnce()}——测试配置即如此，避免后台线程与测试抢数据。
 * fixedDelay：上一轮结束后才开始计时，同一进程内不会重叠；多副本之间靠行锁（SKIP LOCKED）与收尾时的
 * requestId 校验保证只记录一次结局。
 */
@Configuration(proxyBeanMethods = false)
@EnableScheduling
@ConditionalOnProperty(prefix = "platform.survey.reconcile", name = "enabled", havingValue = "true",
        matchIfMissing = true)
class PublishReconciliationScheduler {

    private static final Logger log = LoggerFactory.getLogger(PublishReconciliationScheduler.class);

    private final PublishReconciler reconciler;

    PublishReconciliationScheduler(PublishReconciler reconciler) {
        this.reconciler = reconciler;
    }

    @Scheduled(fixedDelayString = "${platform.survey.reconcile.interval:PT1M}",
            initialDelayString = "${platform.survey.reconcile.initial-delay:PT30S}")
    void tick() {
        try {
            reconciler.runOnce();
        } catch (RuntimeException e) {
            log.error("publish reconciliation round failed", e);
        }
    }
}
