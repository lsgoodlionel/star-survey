package cn.mjy.platform.engine.support;

import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Primary;

/**
 * 只在引擎事件测试里生效的替身。标 {@code @Primary}：租户车道合入真实目录后，
 * 本组测试仍用替身，其他测试不受影响（{@code @TestConfiguration} 不会被组件扫描带入）。
 */
@TestConfiguration(proxyBeanMethods = false)
public class EngineEventsTestConfig {

    @Bean
    @Primary
    FakeEngineInstanceDirectory fakeEngineInstanceDirectory() {
        return new FakeEngineInstanceDirectory();
    }
}
