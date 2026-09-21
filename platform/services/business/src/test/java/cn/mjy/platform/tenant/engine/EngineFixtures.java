package cn.mjy.platform.tenant.engine;

import java.util.UUID;

/** 引擎实例测试数据。测试库在多次运行间保留，所以标识一律随机。 */
public final class EngineFixtures {

    private EngineFixtures() {
    }

    public static String uniqueInstanceId() {
        return "eng-" + UUID.randomUUID().toString().substring(0, 12);
    }
}
