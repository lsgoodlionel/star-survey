package cn.mjy.platform.delivery;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * 投放车道的集成测试标注：给出触达主密钥与平台对外地址，其余沿用共享的测试配置。
 *
 * <p>定时发送不必在这里关闭——{@code platform.delivery.enabled} 缺省就是 false，
 * 任务只能由测试手动调用 {@link DeliveryWorker}。
 *
 * <p>限速窗口设得很短（1 秒），这样"限速把一轮截断"的用例不用等一分钟。
 */
@Target(ElementType.TYPE)
@Retention(RetentionPolicy.RUNTIME)
@SpringBootTest(properties = {
        "PLATFORM_DELIVERY_SECRET=" + DeliveryIntegrationTest.MASTER_SECRET,
        "platform.delivery.public-base-url=https://survey.example",
        "platform.delivery.rate-window=PT1S",
        // 租约设为 0：测试是单线程的，不存在"另一个副本正在发"，崩溃后要能立刻接着做。
        "platform.delivery.send-lease=PT0S"
})
public @interface DeliveryIntegrationTest {

    /** 至少 32 字节，只用于测试。 */
    String MASTER_SECRET = "delivery-master-secret-for-tests-only-0123456789";
}
