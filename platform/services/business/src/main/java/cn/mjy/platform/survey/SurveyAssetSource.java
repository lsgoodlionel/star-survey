package cn.mjy.platform.survey;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.survey.gateway.GatewayRequest;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ArrayNode;
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

    /** 只允许由平台固定的品牌文件元数据；作者态品牌对象仅能包含 assetId。 */
    List<String> BRAND_SLOTS = List.of("logoAsset", "faviconAsset", "coverAsset", "backgroundAsset");

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

    /**
     * 从已物化、已钉住版本的品牌引用重建网关资源包。
     *
     * <p>发布重试只持久化定义快照，不能也不应重新查询资产的当前版本；实现必须只读取
     * {@code branding.*Asset} 中的平台物化信息。没有品牌资源时返回空包，保留旧网关兼容性。
     */
    default List<GatewayRequest.BrandAsset> brandAssets(TenantId tenant, ObjectNode materializedDefinition) {
        if (materializedDefinition.path("branding").has("logoAsset")
                || materializedDefinition.path("branding").has("faviconAsset")
                || materializedDefinition.path("branding").has("coverAsset")
                || materializedDefinition.path("branding").has("backgroundAsset")) {
            throw new InvalidDefinitionException(List.of(
                    "the definition references platform brand assets but the asset service is not available"));
        }
        return List.of();
    }

    /** 定义里全部带 {@code assetId} 的对象节点，按出现顺序。 */
    static List<ObjectNode> references(JsonNode definition) {
        return objectsWith(definition, ASSET_ID);
    }

    /** 仅供普通引用使用；两个品牌资产槽位由品牌解析器单独解释。 */
    static List<ObjectNode> referencesOutsideBranding(ObjectNode definition) {
        List<ObjectNode> found = new ArrayList<>();
        collectOutsideBranding(definition, ASSET_ID, found, definition, false);
        return found;
    }

    /** 定位两个有明确定义的品牌引用，不把 branding 下的任意对象解释成资产引用。 */
    static List<BrandReference> brandReferences(ObjectNode definition, List<String> problems) {
        JsonNode brandingNode = definition.get("branding");
        if (!(brandingNode instanceof ObjectNode branding)) {
            return List.of();
        }
        List<BrandReference> references = new ArrayList<>();
        for (String slot : BRAND_SLOTS) {
            JsonNode node = branding.get(slot);
            if (node == null) {
                continue;
            }
            String legacyKey = slot.replace("Asset", "File");
            if (branding.has(legacyKey)) {
                problems.add("branding may not contain both " + legacyKey + " and " + slot);
            }
            if (!(node instanceof ObjectNode reference)) {
                problems.add("branding." + slot + " must be an object containing only " + ASSET_ID);
                continue;
            }
            if (reference.size() != 1 || !reference.has(ASSET_ID)) {
                problems.add("branding." + slot + " must contain only " + ASSET_ID);
                continue;
            }
            references.add(new BrandReference(slot, reference));
        }
        return references;
    }

    /** 品牌槽位及其对象节点。 */
    record BrandReference(String slot, ObjectNode value) {
    }

    /** 品牌对象中的 assetId 不参与通用递归，由 {@link #brandReferences} 严格验证后单独物化。 */
    private static void collectOutsideBranding(JsonNode node, String key, List<ObjectNode> found,
            ObjectNode definition, boolean insideBrandAsset) {
        if (node instanceof ObjectNode object) {
            if (!insideBrandAsset && object.has(key)) {
                found.add(object);
            }
            for (String name : object.propertyNames()) {
                boolean brandSlot = BRAND_SLOTS.contains(name);
                collectOutsideBranding(object.get(name), key, found, definition,
                        insideBrandAsset || (object == definition.get("branding") && brandSlot));
            }
        } else if (node instanceof ArrayNode array) {
            for (JsonNode child : array) {
                collectOutsideBranding(child, key, found, definition, insideBrandAsset);
            }
        }
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
