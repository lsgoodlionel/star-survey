package cn.mjy.platform.delivery;

import cn.mjy.platform.engine.ResponseProjection;
import cn.mjy.platform.shared.TenantId;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.stereotype.Component;

/**
 * 假的"答卷 → 应答者"回读通道：站在真实引擎回读的位置上。
 * 默认什么都不认（与主代码里"没有实现"的现状一致），测试按需登记映射。
 */
@Component
class FakeRespondentIdentityResolver implements RespondentIdentityResolver {

    private final Map<String, String> keys = new ConcurrentHashMap<>();

    @Override
    public Map<Long, String> respondentKeysOf(TenantId tenant, List<ResponseProjection> responses) {
        Map<Long, String> found = new LinkedHashMap<>();
        for (ResponseProjection response : responses) {
            // 认不出的答卷不进结果（与真实实现一致），而不是映射到 null。
            String key = keys.get(key(tenant, response.key().engineInstanceId(), response.key().surveyId(),
                    response.key().generation(), response.key().responseId()));
            if (key != null) {
                found.put(response.key().responseId(), key);
            }
        }
        return found;
    }

    void map(TenantId tenant, String instance, long sid, String generation, long responseId,
            String respondentKey) {
        keys.put(key(tenant, instance, sid, generation, responseId), respondentKey);
    }

    void reset() {
        keys.clear();
    }

    private static String key(TenantId tenant, String instance, long sid, String generation, long responseId) {
        return tenant + "|" + instance + "|" + sid + "|" + generation + "|" + responseId;
    }
}
