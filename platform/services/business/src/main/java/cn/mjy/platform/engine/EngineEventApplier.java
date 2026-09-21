package cn.mjy.platform.engine;

import cn.mjy.platform.shared.TenantId;
import java.util.List;
import org.springframework.stereotype.Component;

/**
 * 在一个租户事务内依次应用事件：收件箱去重 → 推动投影 → 需要时写发件箱。
 * 三步同事务：任何一步失败整批回滚，收件箱里也不会留下"收到了却没生效"的记录，重投即可恢复。
 */
@Component
class EngineEventApplier {

    private final EngineEventInbox inbox;
    private final ResponseProjectionRepository projections;
    private final EngineOutboxRepository outbox;

    EngineEventApplier(EngineEventInbox inbox, ResponseProjectionRepository projections,
            EngineOutboxRepository outbox) {
        this.inbox = inbox;
        this.projections = projections;
        this.outbox = outbox;
    }

    /** 调用方负责事务与租户作用域（{@code TenantScope}）。 */
    IngestionResult applyAll(TenantId tenant, List<EngineEvent> events) {
        int accepted = 0;
        for (EngineEvent event : events) {
            if (apply(tenant, event)) {
                accepted++;
            }
        }
        return new IngestionResult(events.size(), accepted, events.size() - accepted);
    }

    private boolean apply(TenantId tenant, EngineEvent event) {
        if (!inbox.record(tenant, event)) {
            return false;
        }
        projections.advance(tenant, event)
                .filter(transition -> transition.to().outboxEventType().isPresent())
                .ifPresent(transition -> outbox.append(tenant, event, transition));
        return true;
    }
}
