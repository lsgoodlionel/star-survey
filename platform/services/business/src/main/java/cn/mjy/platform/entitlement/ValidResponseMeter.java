package cn.mjy.platform.entitlement;

import cn.mjy.platform.entitlement.LedgerEntries.LedgerActor;
import cn.mjy.platform.shared.TenantId;
import java.time.Duration;
import java.util.Objects;
import org.springframework.stereotype.Service;

/**
 * "有效完成答卷"计量的窄接口，供引擎事件模块（{@code cn.mjy.platform.engine}）调用。
 *
 * <p>计费口径（P-03）：只有被引擎判定为<b>有效且完成</b>的答卷才调用 {@link #captureValidCompleted}；
 * 预览、导入、删除、编辑一律不调用本类任何方法，因此永远不会计费。
 *
 * <p>推荐流程（按问卷累计，额度即采集档位 P-02）：
 * <ol>
 *   <li>受访者开始作答时 {@link #admit}：预占 1 个名额，名额用尽返回 NEED_UPGRADE / QUOTA_EXCEEDED；</li>
 *   <li>答卷有效完成时 {@link #captureValidCompleted}：结算，计入账单；</li>
 *   <li>放弃、甄别淘汰、判为无效时 {@link #releaseUncompleted}：释放名额，不计费。</li>
 * </ol>
 * 三个方法都以（问卷, 答卷）为幂等键，引擎事件重投不会重复预占或重复计费。
 * 未经 admit 直接收到完成事件时，在额度允许的范围内预占并立即结算；额度不足时返回
 * {@link SettlementOutcome#NOT_ADMITTED}（带原因），不计费，由调用方隔离并告警。
 *
 * <p>租户必须由调用方按引擎实例归属（{@code EngineInstanceDirectory}）确定，不得取自事件体。
 */
@Service
public class ValidResponseMeter {

    /** 引擎事件在流水里的操作者标识。 */
    static final String ENGINE_ACTOR = "system:engine-events";
    /** 作答名额的预占有效期：覆盖长问卷与中途离开再回来的情况。 */
    static final Duration ADMISSION_TTL = Duration.ofDays(7);
    private static final long ONE_RESPONSE = 1;

    private final UsageLedger ledger;

    public ValidResponseMeter(UsageLedger ledger) {
        this.ledger = ledger;
    }

    /** 为一份即将开始的答卷预占 1 个采集名额。 */
    public ReservationResult admit(TenantId tenant, String surveyId, String responseId, String traceId) {
        return ledger.reserveAs(tenant, actor(traceId), request(surveyId, responseId));
    }

    /** 答卷被判定为有效完成：计入计费用量。 */
    public SettlementResult captureValidCompleted(TenantId tenant, String surveyId, String responseId,
                                                  String traceId) {
        String key = key(surveyId, responseId);
        SettlementResult captured = ledger.captureAs(tenant, actor(traceId), key, ONE_RESPONSE);
        if (captured.outcome() != SettlementOutcome.NOT_FOUND) {
            return captured;
        }
        ReservationResult admitted = admit(tenant, surveyId, responseId, traceId);
        if (!admitted.isGranted()) {
            return SettlementResult.notAdmitted(admitted.decision().reason());
        }
        return ledger.captureAs(tenant, actor(traceId), key, ONE_RESPONSE);
    }

    /** 答卷未完成、被淘汰或判为无效：释放名额，不计费。 */
    public SettlementResult releaseUncompleted(TenantId tenant, String surveyId, String responseId,
                                               String traceId) {
        return ledger.releaseAs(tenant, actor(traceId), key(surveyId, responseId));
    }

    private static ReservationRequest request(String surveyId, String responseId) {
        return new ReservationRequest(key(surveyId, responseId), Capabilities.RESPONSE_COLLECT, surveyId,
                ONE_RESPONSE, null, ADMISSION_TTL);
    }

    private static String key(String surveyId, String responseId) {
        Objects.requireNonNull(surveyId, "surveyId");
        Objects.requireNonNull(responseId, "responseId");
        // 问卷标识带长度前缀，任何字符组合都不会拼出相同的键。
        return "response:" + surveyId.length() + ":" + surveyId + ":" + responseId;
    }

    private static LedgerActor actor(String traceId) {
        return new LedgerActor(ENGINE_ACTOR, traceId);
    }
}
