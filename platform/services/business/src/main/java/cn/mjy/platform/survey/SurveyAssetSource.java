package cn.mjy.platform.survey;

import cn.mjy.platform.shared.TenantContext;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * 「定义里的资产引用解析成作答页取得到的地址」这条跨模块接缝（ADR 0019 决定 5）。
 *
 * <p>为什么需要它：问卷模块只管发布，不认识资产库；资产模块管着上传、版本与取件票，
 * 却不参与发布。形状照抄 {@link SurveyParticipantSource}——实现在
 * {@code cn.mjy.platform.asset}，问卷模块只依赖本接口。
 *
 * <p><b>引用的形状是通用的，不绑任何一种题型</b>：定义里<b>任何一处</b>带
 * {@code assetId} 的对象都会被解析。轮播图写在 {@code themeOptions.slides[]} 里，
 * 将来的视频题、录音题写在别处，这里一行都不用改。
 *
 * <p>没有注册实现时（资产模块未装），带资产引用的问卷<b>发不出去</b>，
 * 而不是发出一份图全裂的问卷。
 */
public interface SurveyAssetSource {

    /** 作者在草稿里写的引用键。 */
    String ASSET_ID = "assetId";

    /** 平台在发布时写进去的签名地址；作者自己写了它即判定义非法。 */
    String ASSET_URL = "url";

    /**
     * 平台在发布时写进去的版本号，快照里因此看得出当时用的是第几版。
     *
     * <p><b>这是「平台写、作者写不了」的那把锁。</b>光靠"带 url 的引用不许同时带 assetId"挡不住
     * ——作者可以干脆不写 {@code assetId}，直接填一串自己的地址，改写器根本不会访问那个对象
     * （独立安全审查抓到的一条）。所以规则反过来定：<b>草稿里任何一处出现 assetVersion 都判定义非法</b>，
     * 而每一种带资产的题型主题都必须把它列为必填。于是作者自带的地址凑不齐一条合法的引用。
     */
    String ASSET_VERSION = "assetVersion";

    /**
     * 把定义里每一处 {@code assetId} 换成签名地址与版本号，并登记这一版的引用。
     *
     * <p>解析取的是<b>发布那一刻</b>的当前版本，写进快照；作者之后上传新版本，
     * 已发布的那一版纹丝不动（媒体版本留存）。
     *
     * @return 改写后的副本；定义里一处资产引用都没有时原样返回
     * @throws InvalidDefinitionException 资产不存在、属于别的租户、已停用，或作者自带了 {@code url}
     */
    ObjectNode materialize(TenantContext ctx, UUID surveyId, int draftVersion, ObjectNode definition);

    /** 定义里全部带 {@code assetId} 的对象节点，按出现顺序。 */
    static List<ObjectNode> references(JsonNode definition) {
        return objectsWith(definition, ASSET_ID);
    }

    /**
     * 草稿里不许出现平台自己要写的键。**在解析资产引用之前调用**，因为它要管的正是
     * 「一个 assetId 都没有、却已经把地址与版本号填好了」那种引用。
     *
     * @throws InvalidDefinitionException 定义里任何一处带 {@code assetVersion}
     */
    static void requirePlatformKeysUnwritten(ObjectNode definition) {
        if (!objectsWith(definition, ASSET_VERSION).isEmpty()) {
            throw new InvalidDefinitionException(List.of(
                    ASSET_VERSION + " is written by the platform when it resolves an asset reference;"
                            + " a draft may not carry it"));
        }
    }

    /** 资产模块未装时的兜底：有引用就发不出去。 */
    static ObjectNode requireNoAssetReferences(ObjectNode definition) {
        requirePlatformKeysUnwritten(definition);
        if (!references(definition).isEmpty()) {
            throw new InvalidDefinitionException(List.of(
                    "the definition references platform assets but the asset service is not available"));
        }
        return definition;
    }

    private static List<ObjectNode> objectsWith(JsonNode definition, String key) {
        List<ObjectNode> found = new ArrayList<>();
        collect(definition, key, found);
        return found;
    }

    private static void collect(JsonNode node, String key, List<ObjectNode> found) {
        if (node instanceof ObjectNode object) {
            if (object.has(key)) {
                found.add(object);
            }
            for (String name : object.propertyNames()) {
                collect(object.get(name), key, found);
            }
            return;
        }
        if (node != null && node.isArray()) {
            node.forEach(child -> collect(child, key, found));
        }
    }
}
