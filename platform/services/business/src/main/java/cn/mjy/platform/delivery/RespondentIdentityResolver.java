package cn.mjy.platform.delivery;

import cn.mjy.platform.engine.ResponseProjection;
import cn.mjy.platform.shared.TenantId;
import java.util.Optional;

/**
 * 把一份已完成的答卷对应回"哪位收件人"（跨模块缺口的显式接缝）。
 *
 * <p>为什么需要它：引擎事件与答卷投影里只有答卷号，<b>没有参与者 token</b>（见
 * {@code cn.mjy.platform.engine.EngineEvent}）。平台因此无法只靠投影判断"张三答完了没有"。
 * ADR 0016 已把这条列为缺口（网关 {@code add_participants} 总让引擎生成 token，平台要发的邀请码必须发布后回读）。
 *
 * <p>本接口就是那条回读通道的接缝：拿到答卷的自然键，回答它属于哪个应答者标识（邀请码）。
 * 主代码里没有实现——没有回读通道时 {@link CompletionReconciler} 什么也不做，
 * 催答只依据显式登记的完成记录（{@link CompletionService}）。接上引擎侧的回读后，
 * 实现本接口并注册为 bean，对账即自动生效。
 */
public interface RespondentIdentityResolver {

    Optional<String> respondentKeyOf(TenantId tenant, ResponseProjection response);
}
