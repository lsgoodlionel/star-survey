package cn.mjy.platform.tenant.engine;

/** 引擎实例的对外表示。 */
public record EngineInstanceView(String id, String tenantId, String baseUrl, String status, String createdAt) {

    public static EngineInstanceView of(EngineInstance instance) {
        return new EngineInstanceView(instance.id(), instance.tenantId().toString(), instance.baseUrl(),
                instance.status().code(), instance.createdAt().toString());
    }
}
