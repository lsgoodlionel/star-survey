package cn.mjy.platform.response;

import java.io.IOException;
import java.io.OutputStream;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import org.springframework.context.annotation.Primary;
import org.springframework.stereotype.Component;

/**
 * 测试用附件来源：按 (实例, sid, 答卷号, 存储名) 存字节，像真网关一样——没有的那一份回
 * {@link Outcome#NOT_FOUND}，配置过的实例可以整体演成暂时不可达。
 */
@Primary
@Component
public class FakeResponseAttachmentSource implements ResponseAttachmentSource {

    private record Key(String instance, long sid, long responseId, String storedName) {
    }

    private final Map<Key, byte[]> bytes = new ConcurrentHashMap<>();
    private final Map<String, Boolean> failing = new ConcurrentHashMap<>();
    private final List<AttachmentQuery> calls = new CopyOnWriteArrayList<>();

    public void put(String instance, long sid, long responseId, String storedName, String content) {
        bytes.put(new Key(instance, sid, responseId, storedName), content.getBytes(StandardCharsets.UTF_8));
    }

    public void failFor(String instance) {
        failing.put(instance, true);
    }

    public void recover(String instance) {
        failing.remove(instance);
    }

    public List<AttachmentQuery> calls() {
        return new ArrayList<>(calls);
    }

    public void clearCalls() {
        calls.clear();
    }

    @Override
    public Outcome fetch(AttachmentQuery query, OutputStream sink) {
        calls.add(query);
        if (failing.containsKey(query.engineInstanceId())) {
            throw new ResponseAttachmentsUnavailableException("fake attachment failure for "
                    + query.engineInstanceId());
        }
        byte[] content = bytes.get(new Key(query.engineInstanceId(), query.engineSid(), query.responseId(),
                query.storedName()));
        if (content == null) {
            return Outcome.NOT_FOUND;
        }
        if (content.length > query.maxBytes()) {
            return Outcome.TOO_LARGE;
        }
        try {
            sink.write(content);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        return Outcome.STORED;
    }
}
