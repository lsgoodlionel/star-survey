package cn.mjy.platform.delivery;

import cn.mjy.platform.engine.ResponseProjection;
import cn.mjy.platform.response.RespondentSource;
import cn.mjy.platform.response.ResponseAnswersUnavailableException;
import cn.mjy.platform.shared.TenantId;
import java.util.List;
import java.util.Map;
import org.springframework.context.annotation.Conditional;
import org.springframework.stereotype.Component;

/**
 * 经发布网关读端点回答"这份答卷是用哪个邀请码答的"（ADR 0016 缺口 (b)）。
 *
 * <p>只在网关配置好时注册为 bean；没配时不注册，{@link CompletionReconciler} 照旧空转，
 * 催答退回只认显式登记的完成记录，而不是把"读不到"当成"没人答完"。
 *
 * <p>令牌<b>不落库</b>：每轮对账现读现用，用完即弃。平台库里因此不会多出一份可以直接进入问卷的凭据
 * （网关侧的回执存档已经有同样的遗留，不再多一处）。
 */
@Component
@Conditional(PublishGatewayConfigured.class)
class GatewayRespondentIdentityResolver implements RespondentIdentityResolver {

    private final RespondentSource answers;

    GatewayRespondentIdentityResolver(RespondentSource answers) {
        this.answers = answers;
    }

    @Override
    public Map<Long, String> respondentKeysOf(TenantId tenant, List<ResponseProjection> responses) {
        if (responses.isEmpty()) {
            return Map.of();
        }
        ResponseProjection first = responses.get(0);
        List<Long> ids = responses.stream().map(response -> response.key().responseId()).toList();
        try {
            return answers.readRespondents(first.key().engineInstanceId(), first.key().surveyId(), ids);
        } catch (ResponseAnswersUnavailableException ignored) {
            // 不可用不等于合法的匿名空映射：抛出异常让对账事务回滚，游标留待下一轮重试。
            // DeliveryWorker 会跳过本租户本轮催答和发送，继续其他租户。
            // Worker 会记录异常；这里不携带源异常正文或 cause，防止泄露令牌或原始网关内容。
            throw new ResponseAnswersUnavailableException("respondent identity lookup unavailable");
        }
    }
}
