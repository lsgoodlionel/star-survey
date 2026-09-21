package cn.mjy.platform.engine.support;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.context.annotation.Import;

/**
 * 引擎事件集成测试的统一配置：同一组属性，Spring 测试上下文可在各测试类之间复用。
 * 共享密钥以环境变量同名的属性注入，与生产读取方式一致。
 */
@Target(ElementType.TYPE)
@Retention(RetentionPolicy.RUNTIME)
@SpringBootTest(properties = "PLATFORM_ENGINE_EVENTS_SECRET=" + SignedEventRequests.SECRET)
@AutoConfigureMockMvc
@Import(EngineEventsTestConfig.class)
public @interface EngineEventsIntegrationTest {
}
