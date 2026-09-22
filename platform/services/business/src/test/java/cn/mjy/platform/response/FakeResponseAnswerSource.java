package cn.mjy.platform.response;

import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicInteger;
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
    private final Map<String, AtomicInteger> crashes = new ConcurrentHashMap<>();
    private final Map<String, Synthesizer> synthesizers = new ConcurrentHashMap<>();

    /** 模拟执行者进程在读取中途死掉：不是业务异常，调用栈上谁也不该吞掉它。 */
    public static final class SimulatedCrash extends Error {

        SimulatedCrash() {
            super("simulated process crash");
        }
    }

    /** 按 (答卷号, 列名) 现算作答，规模测试用，避免在内存里存几十万行。 */
    @FunctionalInterface
    public interface Synthesizer {
        String value(long responseId, String fieldname);
    }

    public void put(String instance, long sid, long responseId, Map<String, String> values) {
        rows.put(new Key(instance, sid, responseId), new HashMap<>(values));
    }

    /** 让某个实例的读取失败（网关不可达、引擎报错）。 */
    public void failFor(String instance) {
        failing.put(instance, true);
    }

    public void recover(String instance) {
        failing.remove(instance);
        crashes.remove(instance);
        synthesizers.remove(instance);
    }

    /** 该实例再成功读取 calls 次之后，下一次读取抛 {@link SimulatedCrash}。 */
    public void crashAfter(String instance, int calls) {
        crashes.put(instance, new AtomicInteger(calls));
    }

    public void synthesize(String instance, Synthesizer synthesizer) {
        synthesizers.put(instance, synthesizer);
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
        AtomicInteger crash = crashes.get(engineInstanceId);
        if (crash != null && crash.getAndDecrement() <= 0) {
            crashes.remove(engineInstanceId);
            throw new SimulatedCrash();
        }
        Synthesizer synthesizer = synthesizers.get(engineInstanceId);
        if (synthesizer != null) {
            return synthesized(synthesizer, responseIds, fieldnames);
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

    private static AnswerBatch synthesized(Synthesizer synthesizer, List<Long> responseIds, List<String> fieldnames) {
        Map<Long, Map<String, String>> found = new LinkedHashMap<>();
        for (Long id : responseIds) {
            Map<String, String> values = new LinkedHashMap<>();
            for (String field : fieldnames) {
                values.put(field, synthesizer.value(id, field));
            }
            found.put(id, values);
        }
        return new AnswerBatch(found);
    }
}
