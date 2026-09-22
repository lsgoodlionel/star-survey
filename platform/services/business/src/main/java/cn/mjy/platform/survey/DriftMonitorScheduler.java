package cn.mjy.platform.survey;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.Scheduled;

/**
 * 定时漂移巡检。只有 {@code platform.survey.drift-check.enabled=true} 时才装配（默认关闭，测试里关闭），
 * 否则只能按需检查（{@link SurveyDriftService#check}）或手动调用 {@link SurveyDriftService#runScheduledRound()}。
 */
@Configuration(proxyBeanMethods = false)
@EnableScheduling
@ConditionalOnProperty(prefix = "platform.survey.drift-check", name = "enabled", havingValue = "true")
class DriftMonitorScheduler {

    private static final Logger log = LoggerFactory.getLogger(DriftMonitorScheduler.class);

    private final SurveyDriftService drift;

    DriftMonitorScheduler(SurveyDriftService drift) {
        this.drift = drift;
    }

    @Scheduled(fixedDelayString = "${platform.survey.drift-check.interval:PT15M}",
            initialDelayString = "${platform.survey.drift-check.initial-delay:PT2M}")
    void tick() {
        try {
            drift.runScheduledRound();
        } catch (RuntimeException e) {
            log.error("drift monitor round failed", e);
        }
    }
}
