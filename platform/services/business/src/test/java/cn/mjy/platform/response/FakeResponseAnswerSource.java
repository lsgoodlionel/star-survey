package cn.mjy.platform.response;

import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import org.springframework.context.annotation.Primary;
import org.springframework.stereotype.Component;

/**
 * 测试用作答来源：按 (实例, sid, 答卷号) 存作答，像真网关一样只返回请求的列、缺的答卷不返回。
 * 标 {@code @Primary}，测试里答卷查询只会调用本替身。
 */
@Primary
@Component
public class FakeResponseAnswerSource implements ResponseAnswerSource {

    /** 一次调用的记录。 */
    public record Call(String engineInstanceId, long engineSid, List<Long> responseIds, List<String> fieldnames) {
    }

    private record Key(String instance, long sid, long responseId) {
    }

    private final Map<Key, Map<String, String>> rows = new ConcurrentHashMap<>();
    private final Map<String, Boolean> failing = new ConcurrentHashMap<>();
    private final List<Call> calls = new CopyOnWriteArrayList<>();

    public void put(String instance, long sid, long responseId, Map<String, String> values) {
        rows.put(new Key(instance, sid, responseId), new HashMap<>(values));
    }

    /** 让某个实例的读取失败（网关不可达、引擎报错）。 */
    public void failFor(String instance) {
        failing.put(instance, true);
    }

    public List<Call> callsFor(String instance) {
        return calls.stream().filter(c -> c.engineInstanceId().equals(instance)).toList();
    }

    @Override
    public AnswerBatch read(String engineInstanceId, long engineSid, List<Long> responseIds, List<String> fieldnames) {
        calls.add(new Call(engineInstanceId, engineSid, List.copyOf(responseIds), List.copyOf(fieldnames)));
        if (failing.containsKey(engineInstanceId)) {
            throw new ResponseAnswersUnavailableException("fake gateway failure for " + engineInstanceId);
        }
        Map<Long, Map<String, String>> found = new LinkedHashMap<>();
        for (Long id : responseIds) {
            Map<String, String> stored = rows.get(new Key(engineInstanceId, engineSid, id));
            if (stored == null) {
                continue;
            }
            Map<String, String> projected = new LinkedHashMap<>();
            for (String field : fieldnames) {
                projected.put(field, stored.get(field));
            }
            found.put(id, projected);
        }
        return new AnswerBatch(found);
    }
}
