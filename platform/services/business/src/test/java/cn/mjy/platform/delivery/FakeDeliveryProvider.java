package cn.mjy.platform.delivery;

import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * 进程内的假渠道商：记录每次投递，并<b>按幂等键去重</b>——真实服务商也是这么做的，
 * 平台"崩溃后用同一个幂等键重发"的正确性正是建立在这个契约上（见 {@link DeliveryProvider}）。
 *
 * <p>可以编排：让第 n 次调用抛异常（模拟进程在发送途中死掉）、让某个地址被永久拒绝、让接下来几次要求重试。
 */
abstract class FakeDeliveryProvider implements DeliveryProvider {

    /** 一次成功投递的记录。 */
    record Delivered(String address, String subject, String body, String idempotencyKey) {
    }

    /** 模拟"进程在调用渠道商的途中死掉"。 */
    static class SimulatedCrash extends RuntimeException {

        SimulatedCrash(String message) {
            super(message);
        }
    }

    private final Map<String, Delivered> byIdempotencyKey = new ConcurrentHashMap<>();
    private final List<DeliveryMessage> calls = new CopyOnWriteArrayList<>();
    private final Map<String, String> rejectedAddresses = new ConcurrentHashMap<>();
    private final AtomicInteger crashAfter = new AtomicInteger(-1);
    private final AtomicInteger retriesLeft = new AtomicInteger();

    @Override
    public SendResult send(DeliveryMessage message) {
        calls.add(message);
        if (crashAfter.get() >= 0 && calls.size() > crashAfter.get()) {
            throw new SimulatedCrash("provider died after " + crashAfter.get() + " deliveries");
        }
        String rejection = rejectedAddresses.get(message.address());
        if (rejection != null) {
            return SendResult.rejected(rejection);
        }
        if (retriesLeft.get() > 0) {
            retriesLeft.decrementAndGet();
            return SendResult.retry("temporarily_unavailable");
        }
        // 幂等：同一个键再来一次不产生第二条投递，原样返回首次的结果。
        Delivered existing = byIdempotencyKey.get(message.idempotencyKey());
        if (existing != null) {
            return SendResult.accepted(existing.idempotencyKey());
        }
        Delivered delivered = new Delivered(message.address(), message.subject(), message.body(),
                message.idempotencyKey());
        byIdempotencyKey.put(message.idempotencyKey(), delivered);
        return SendResult.accepted(message.idempotencyKey());
    }

    /** 实际投递出去的消息（按幂等键去重后）。 */
    List<Delivered> delivered() {
        return List.copyOf(byIdempotencyKey.values());
    }

    Optional<Delivered> deliveredTo(String address) {
        return byIdempotencyKey.values().stream().filter(d -> d.address().equals(address)).findFirst();
    }

    /** 包括重复调用在内的全部调用次数。 */
    int callCount() {
        return calls.size();
    }

    List<DeliveryMessage> calls() {
        return List.copyOf(calls);
    }

    void crashAfter(int deliveries) {
        crashAfter.set(deliveries);
    }

    void stopCrashing() {
        crashAfter.set(-1);
    }

    void rejectAddress(String address, String errorCode) {
        rejectedAddresses.put(address, errorCode);
    }

    void failTemporarily(int times) {
        retriesLeft.set(times);
    }

    void reset() {
        byIdempotencyKey.clear();
        calls.clear();
        rejectedAddresses.clear();
        crashAfter.set(-1);
        retriesLeft.set(0);
    }
}
