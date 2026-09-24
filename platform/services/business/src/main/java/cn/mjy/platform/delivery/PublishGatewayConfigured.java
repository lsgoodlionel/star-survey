package cn.mjy.platform.delivery;

import cn.mjy.platform.response.HttpResponseAnswerSource;
import org.springframework.context.annotation.Condition;
import org.springframework.context.annotation.ConditionContext;
import org.springframework.core.type.AnnotatedTypeMetadata;

/**
 * 发布网关是否配置好（地址与足够长的共享密钥）。没配时不注册回读实现，
 * 催答退回只认显式登记的完成记录——而不是把"读不到"当成"没人答完"。
 */
class PublishGatewayConfigured implements Condition {

    @Override
    public boolean matches(ConditionContext context, AnnotatedTypeMetadata metadata) {
        String url = context.getEnvironment().getProperty("platform.pubgw.url", "");
        String secret = context.getEnvironment().getProperty("platform.pubgw.secret", "");
        return !url.isBlank() && secret.length() >= HttpResponseAnswerSource.MIN_SECRET_BYTES;
    }
}
