package cn.mjy.platform.asset;

import cn.mjy.platform.asset.AssetRepository.AssetRow;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.InvalidDefinitionException;
import cn.mjy.platform.survey.PublishUnavailableException;
import cn.mjy.platform.survey.SurveyAssetSource;
import java.time.Clock;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * 发布接缝的资产侧实现（ADR 0019 决定 5）：把定义里的 {@code assetId} 换成签名取件地址，
 * 并把"这一版问卷用了这一版资产"登记下来。
 *
 * <p>整件事在<b>一个</b>租户事务里完成，与发布尝试落库同一把行锁之下：改写不成功就整笔回滚，
 * 不会出现"引用登记了、定义却没改写"或者反过来。
 */
@Component
class SurveyAssetLinks implements SurveyAssetSource {

    private final TenantScope tenantScope;
    private final AssetRepository assets;
    private final AssetTickets tickets;
    private final AssetAudit audit;
    private final AssetProperties properties;
    private final Clock clock;

    SurveyAssetLinks(TenantScope tenantScope, AssetRepository assets, AssetTickets tickets, AssetAudit audit,
            AssetProperties properties) {
        this.tenantScope = tenantScope;
        this.assets = assets;
        this.tickets = tickets;
        this.audit = audit;
        this.properties = properties;
        this.clock = Clock.systemUTC();
    }

    @Override
    public ObjectNode materialize(TenantContext ctx, UUID surveyId, int draftVersion, ObjectNode definition) {
        // 先挡住「作者自己把 assetVersion 填好了」那种引用——它可能一个 assetId 都不带，
        // 下面按 assetId 找引用的那一步根本访问不到它（独立安全审查抓到的一条）。
        SurveyAssetSource.requirePlatformKeysUnwritten(definition);
        ObjectNode copy = definition.deepCopy();
        List<ObjectNode> references = SurveyAssetSource.references(copy);
        if (references.isEmpty()) {
            return definition;
        }
        requireConfigured();
        return tenantScope.call(ctx.tenantId(), () -> {
            List<String> problems = new ArrayList<>();
            for (ObjectNode reference : references) {
                resolve(ctx, surveyId, draftVersion, reference, problems);
            }
            if (!problems.isEmpty()) {
                throw new InvalidDefinitionException(problems);
            }
            return copy;
        });
    }

    private void resolve(TenantContext ctx, UUID surveyId, int draftVersion, ObjectNode reference,
            List<String> problems) {
        if (reference.has(SurveyAssetSource.ASSET_URL)) {
            // 作者自带地址就等于绕开资产服务；宁可发布失败，也不把一串来路不明的 URL 发到作答页上。
            problems.add("an asset reference may not carry its own " + SurveyAssetSource.ASSET_URL);
            return;
        }
        UUID assetId = assetId(reference, problems);
        if (assetId == null) {
            return;
        }
        AssetRow row = assets.readAsset(assetId).orElse(null);
        if (row == null) {
            // 别的租户的资产在行级安全下等同不存在，与"这个 id 根本没有"同一句话。
            problems.add("unknown asset: " + assetId);
            return;
        }
        if (row.status() != AssetStatus.ACTIVE) {
            problems.add("asset " + assetId + " is archived and cannot be used in a new publish");
            return;
        }
        int versionNo = row.currentVersion();
        String url = tickets.mint(ctx.tenantId(), assetId, versionNo, clock.instant().plus(properties.ticketTtl()))
                .url(properties.publicBaseUrl());
        reference.remove(SurveyAssetSource.ASSET_ID);
        reference.put(SurveyAssetSource.ASSET_URL, url);
        reference.put(SurveyAssetSource.ASSET_VERSION, versionNo);
        assets.insertReference(assetId, versionNo, surveyId, draftVersion);
        audit.record(ctx, AssetAudit.REFERENCE, assetId, "v" + versionNo + " survey/" + surveyId);
    }

    private static UUID assetId(ObjectNode reference, List<String> problems) {
        JsonNode value = reference.get(SurveyAssetSource.ASSET_ID);
        if (value == null || !value.isString()) {
            problems.add(SurveyAssetSource.ASSET_ID + " must be a string");
            return null;
        }
        try {
            return UUID.fromString(value.asString());
        } catch (IllegalArgumentException e) {
            problems.add(SurveyAssetSource.ASSET_ID + " must be a UUID");
            return null;
        }
    }

    /**
     * 签不出票或没有对外地址时，带资产的问卷一律发不出去——失败即关闭。
     *
     * <p>这里抛的是<b>问卷模块</b>的 {@link PublishUnavailableException} 而不是资产模块自己的异常：
     * 资产模块的错误映射只挂在 {@code cn.mjy.platform.asset} 上，而这条路径是从问卷控制器进来的，
     * 抛资产异常会落到没人接的地方，对外变成 500。语义本来就是"发布这件事当下做不了"，
     * 与"网关没配"同一类，503。
     */
    private void requireConfigured() {
        if (!tickets.isConfigured()) {
            throw new PublishUnavailableException(
                    AssetKeys.MASTER_SECRET_PROPERTY + " is not configured; surveys that reference assets"
                            + " cannot be published");
        }
        if (properties.publicBaseUrl().isBlank()) {
            throw new PublishUnavailableException("platform.asset.public-base-url is not configured;"
                    + " surveys that reference assets cannot be published");
        }
    }
}
