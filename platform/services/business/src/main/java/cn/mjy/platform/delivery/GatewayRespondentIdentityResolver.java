package cn.mjy.platform.delivery;

import cn.mjy.platform.engine.ResponseProjection;
import cn.mjy.platform.response.RespondentSource;
import cn.mjy.platform.response.ResponseAnswersUnavailableException;
import cn.mjy.platform.shared.TenantId;
import java.util.List;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
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

    private static final Logger log = LoggerFactory.getLogger(GatewayRespondentIdentityResolver.class);

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
        } catch (ResponseAnswersUnavailableException e) {
            // 读不到就当作这一轮认不出任何人：对账不前进游标、不登记完成，下一轮重来。
            // 绝不能把"读不到"解释成"这些人都没答完"，那会误发一批催答。
            log.warn("could not read respondents on {} sid {}: {}", first.key().engineInstanceId(),
                    first.key().surveyId(), e.getMessage());
            return Map.of();
        }
    }
}
