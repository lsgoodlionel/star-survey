package cn.mjy.platform.delivery;

import java.time.OffsetDateTime;
import java.util.Map;
import java.util.UUID;

/**
 * 一条投放链接的对外视图。
 *
 * @param label        来源标签，用于渠道归因（R05-08）
 * @param signedParams 已签名的参数集合（含 exp 与 sig）；调用方不能改其中任何一项
 * @param url          作答地址（含签名参数），每次现算，跟随问卷的当前发布版本
 * @param shortUrl     短链地址；未生成短链时为空
 */
public record LinkView(
        UUID id,
        UUID surveyId,
        String label,
        Map<String, String> signedParams,
        String url,
        String shortUrl,
        OffsetDateTime expiresAt,
        OffsetDateTime revokedAt,
        OffsetDateTime createdAt) {

    public LinkView {
        signedParams = Map.copyOf(signedParams);
    }

    public boolean isActive(OffsetDateTime now) {
        return revokedAt == null && (expiresAt == null || expiresAt.isAfter(now));
    }
}
