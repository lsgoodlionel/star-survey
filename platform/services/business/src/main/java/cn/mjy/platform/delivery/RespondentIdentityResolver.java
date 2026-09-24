package cn.mjy.platform.delivery;

import cn.mjy.platform.engine.ResponseProjection;
import cn.mjy.platform.shared.TenantId;
import java.util.List;
import java.util.Map;

/**
 * 把已完成的答卷对应回"哪位收件人"（跨模块缺口的显式接缝）。
 *
 * <p>为什么需要它：引擎事件与答卷投影里只有答卷号，<b>没有参与者 token</b>（见
 * {@code cn.mjy.platform.engine.EngineEvent} 与 {@link ResponseProjection}）。平台因此无法只靠投影
 * 判断"张三答完了没有"。ADR 0016 把这条分成两半：发布回执的 {@code invitations[]} 给出"哪个码
 * 发给了谁"，读端点的 {@code includeRespondent} 给出"一份答卷属于哪个码"，本接口是后者的接缝。
 *
 * <p><b>按批问，不按条问</b>：对账一页最多 200 份答卷，读端点一次收 500 个答卷号，
 * 逐条调用会把一次对账放大成两百次 HTTP。
 *
 * <p>认不出是谁的答卷<b>不出现在结果里</b>（匿名问卷、没用邀请码进场的答卷），而不是映射到空值——
 * 对账据此跳过，绝不把它算作某个人答的。没有注册实现时 {@link CompletionReconciler} 什么也不做，
 * 催答只依据显式登记的完成记录（{@link CompletionService}）。
 */
public interface RespondentIdentityResolver {

    /**
     * @param responses 同一 (实例, 问卷, 代次) 下的一页答卷
     * @return 答卷号 → 应答者标识（邀请码），只含确实认得出的那些
     */
    Map<Long, String> respondentKeysOf(TenantId tenant, List<ResponseProjection> responses);
}
