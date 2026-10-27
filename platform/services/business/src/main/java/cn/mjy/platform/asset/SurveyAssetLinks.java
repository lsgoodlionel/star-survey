package cn.mjy.platform.asset;

import cn.mjy.platform.asset.AssetRepository.AssetRow;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.InvalidDefinitionException;
import cn.mjy.platform.survey.PublishUnavailableException;
import cn.mjy.platform.survey.SurveyAssetSource;
import cn.mjy.platform.survey.gateway.GatewayRequest;
import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.net.URISyntaxException;
import java.security.MessageDigest;
import java.time.Clock;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import java.util.Map;
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

    private static final long MAX_BRAND_BYTES = 5L * 1024 * 1024;

    private static final Map<String, String> LOGO_EXTENSIONS = Map.of(
            "image/png", ".png",
            "image/jpeg", ".jpg",
            "image/gif", ".gif",
            "image/webp", ".webp");
    private static final Map<String, String> FAVICON_EXTENSIONS = Map.ofEntries(
            Map.entry("image/png", ".png"),
            Map.entry("image/jpeg", ".jpg"),
            Map.entry("image/gif", ".gif"),
            Map.entry("image/webp", ".webp"),
            Map.entry("image/x-icon", ".ico"),
            Map.entry("image/vnd.microsoft.icon", ".ico"));

    private final TenantScope tenantScope;
    private final AssetRepository assets;
    private final AssetTickets tickets;
    private final AssetAudit audit;
    private final AssetProperties properties;
    private final AssetService assetService;
    private final Clock clock;

    SurveyAssetLinks(TenantScope tenantScope, AssetRepository assets, AssetTickets tickets, AssetAudit audit,
            AssetProperties properties, AssetService assetService) {
        this.tenantScope = tenantScope;
        this.assets = assets;
        this.tickets = tickets;
        this.audit = audit;
        this.properties = properties;
        this.assetService = assetService;
        this.clock = Clock.systemUTC();
    }

    @Override
    public ObjectNode materialize(TenantContext ctx, UUID surveyId, int draftVersion, ObjectNode definition) {
        // 先挡住「作者自己把 assetVersion 填好了」那种引用——它可能一个 assetId 都不带，
        // 下面按 assetId 找引用的那一步根本访问不到它（独立安全审查抓到的一条）。
        SurveyAssetSource.requirePlatformKeysUnwritten(definition);
        ObjectNode copy = definition.deepCopy();
        List<String> problems = new ArrayList<>();
        List<SurveyAssetSource.BrandReference> brandReferences =
                SurveyAssetSource.brandReferences(copy, problems);
        List<ObjectNode> references = SurveyAssetSource.referencesOutsideBranding(copy);
        if (!problems.isEmpty()) {
            throw new InvalidDefinitionException(problems);
        }
        if (references.isEmpty() && brandReferences.isEmpty() && problems.isEmpty()) {
            return definition;
        }
        requireConfigured();
        return tenantScope.call(ctx.tenantId(), () -> {
            for (ObjectNode reference : references) {
                resolve(ctx, surveyId, draftVersion, reference, problems, false, null);
            }
            for (SurveyAssetSource.BrandReference reference : brandReferences) {
                resolve(ctx, surveyId, draftVersion, reference.value(), problems, true, reference.slot());
            }
            if (!problems.isEmpty()) {
                throw new InvalidDefinitionException(problems);
            }
            return copy;
        });
    }

    @Override
    public List<GatewayRequest.BrandAsset> brandAssets(TenantId tenant, ObjectNode materializedDefinition) {
        JsonNode brandingNode = materializedDefinition.get("branding");
        if (!(brandingNode instanceof ObjectNode branding)) {
            return List.of();
        }
        List<GatewayRequest.BrandAsset> bundle = new ArrayList<>();
        for (String slot : SurveyAssetSource.BRAND_SLOTS) {
            JsonNode node = branding.get(slot);
            if (node == null) {
                continue;
            }
            if (!(node instanceof ObjectNode reference)) {
                throw invalidBrandAsset(slot, "must be a platform materialized object");
            }
            bundle.add(readBrandAsset(tenant, slot, reference));
        }
        return List.copyOf(bundle);
    }

    private GatewayRequest.BrandAsset readBrandAsset(TenantId tenant, String slot, ObjectNode reference) {
        UUID assetId = pinnedAssetId(tenant, slot, reference);
        int version = requiredPositiveInt(reference, "assetVersion", slot);
        String contentType = requiredText(reference, "contentType", slot);
        long sizeBytes = requiredPositiveLong(reference, "sizeBytes", slot);
        String expectedSha256 = requiredText(reference, "sha256", slot);
        String fileName = requiredText(reference, "fileName", slot);
        Map<String, String> extensions = slot.equals("faviconAsset") ? FAVICON_EXTENSIONS : LOGO_EXTENSIONS;
        String extension = extensions.get(contentType);
        String expectedName = extension == null ? "" : brandFileName(slot, extension);
        if (sizeBytes > MAX_BRAND_BYTES || extension == null || !expectedName.equals(fileName)
                || !expectedSha256.matches("[0-9a-f]{64}")) {
            throw invalidBrandAsset(slot, "does not match the pinned brand metadata");
        }
        return readPinnedContent(tenant, assetId, version, slot, fileName, contentType, sizeBytes, expectedSha256);
    }

    private GatewayRequest.BrandAsset readPinnedContent(TenantId tenant, UUID assetId, int version, String slot,
            String fileName, String contentType, long sizeBytes, String expectedSha256) {
        AssetContent content = assetService.open(tenant, assetId, version);
        if (!contentType.equals(content.contentType()) || sizeBytes != content.byteSize()) {
            try {
                content.content().close();
            } catch (IOException ignored) {
                // The mismatch is the actionable error; never hide it with a close failure.
            }
            throw invalidBrandAsset(slot, "does not match stored asset metadata");
        }
        try (InputStream stream = content.content()) {
            byte[] bytes = stream.readNBytes(Math.toIntExact(MAX_BRAND_BYTES + 1));
            if (bytes.length != sizeBytes || bytes.length > MAX_BRAND_BYTES) {
                throw invalidBrandAsset(slot, "does not match stored asset bytes");
            }
            String sha256 = java.util.HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
            if (!sha256.equals(content.sha256())) {
                throw invalidBrandAsset(slot, "does not match stored asset digest");
            }
            if (!sha256.equals(expectedSha256)) {
                throw invalidBrandAsset(slot, "does not match the pinned brand digest");
            }
            return new GatewayRequest.BrandAsset(slot, fileName, contentType, sizeBytes, sha256,
                    Base64.getEncoder().encodeToString(bytes));
        } catch (IOException e) {
            throw new AssetUnavailableException("asset bytes are unreadable: " + assetId + " v" + version, e);
        } catch (java.security.NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 is unavailable", e);
        }
    }

    private UUID pinnedAssetId(TenantId tenant, String slot, ObjectNode reference) {
        String source = requiredText(reference, SurveyAssetSource.ASSET_URL, slot);
        try {
            URI uri = new URI(source);
            String[] parts = uri.getPath() == null ? new String[0] : uri.getPath().split("/");
            if (!"https".equals(uri.getScheme()) || uri.getHost() == null || uri.getUserInfo() != null
                    || parts.length != 5 || !"a".equals(parts[1]) || !tenant.value().toString().equals(parts[2])
                    || uri.getQuery() == null || !uri.getQuery().contains("exp=") || !uri.getQuery().contains("sig=")) {
                throw invalidBrandAsset(slot, "has an invalid platform asset URL");
            }
            UUID assetId = UUID.fromString(parts[3]);
            int urlVersion = Integer.parseInt(parts[4]);
            if (urlVersion != requiredPositiveInt(reference, SurveyAssetSource.ASSET_VERSION, slot)) {
                throw invalidBrandAsset(slot, "has a URL version that does not match assetVersion");
            }
            return assetId;
        } catch (URISyntaxException | IllegalArgumentException e) {
            throw invalidBrandAsset(slot, "has an invalid platform asset URL");
        }
    }

    private static String requiredText(ObjectNode reference, String key, String slot) {
        JsonNode value = reference.get(key);
        if (value == null || !value.isString() || value.asString().isBlank()) {
            throw invalidBrandAsset(slot, "is missing " + key);
        }
        return value.asString();
    }

    private static int requiredPositiveInt(ObjectNode reference, String key, String slot) {
        JsonNode value = reference.get(key);
        if (value == null || !value.canConvertToInt() || value.intValue() <= 0) {
            throw invalidBrandAsset(slot, "has an invalid " + key);
        }
        return value.intValue();
    }

    private static long requiredPositiveLong(ObjectNode reference, String key, String slot) {
        JsonNode value = reference.get(key);
        if (value == null || !value.canConvertToLong() || value.longValue() <= 0) {
            throw invalidBrandAsset(slot, "has an invalid " + key);
        }
        return value.longValue();
    }

    private static InvalidDefinitionException invalidBrandAsset(String slot, String detail) {
        return new InvalidDefinitionException(List.of("branding." + slot + " " + detail));
    }

    private void resolve(TenantContext ctx, UUID surveyId, int draftVersion, ObjectNode reference,
            List<String> problems, boolean brandAsset, String brandSlot) {
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
        if (row.origin() != AssetOrigin.AUTHOR) {
            // 这不是洁癖，是一条直通路：引用一旦成立，发布会把作答者上传的内容改写成一张
            // **bearer** 取件票发到所有作答者的浏览器上，决定 9 那一整套当场被绕开。
            // 理由不写进 problems：与"这个 id 根本没有"同一句话，免得作者靠报错分辨
            // 「这个 UUID 是不是某位作答者的录音」。
            problems.add("unknown asset: " + assetId);
            return;
        }
        if (row.status() != AssetStatus.ACTIVE) {
            problems.add("asset " + assetId + " is archived and cannot be used in a new publish");
            return;
        }
        int versionNo = row.currentVersion();
        String contentType = null;
        long sizeBytes = 0;
        String sha256 = null;
        if (brandAsset) {
            AssetVersionView version = assets.findVersion(assetId, versionNo).orElse(null);
            Map<String, String> extensions = brandSlot.equals("faviconAsset")
                    ? FAVICON_EXTENSIONS : LOGO_EXTENSIONS;
            if (version == null || row.kind() != AssetKind.IMAGE || !extensions.containsKey(version.contentType())
                    || version.byteSize() > MAX_BRAND_BYTES) {
                problems.add("branding." + brandSlot + " must reference an image asset");
                return;
            }
            contentType = version.contentType();
            sizeBytes = version.byteSize();
            sha256 = version.sha256();
        }
        String url = tickets.mint(ctx.tenantId(), assetId, versionNo, clock.instant().plus(properties.ticketTtl()))
                .url(properties.publicBaseUrl());
        reference.remove(SurveyAssetSource.ASSET_ID);
        reference.put(SurveyAssetSource.ASSET_URL, url);
        reference.put(SurveyAssetSource.ASSET_VERSION, versionNo);
        if (brandAsset) {
            Map<String, String> extensions = brandSlot.equals("faviconAsset")
                    ? FAVICON_EXTENSIONS : LOGO_EXTENSIONS;
            reference.put("contentType", contentType);
            reference.put("sizeBytes", sizeBytes);
            reference.put("sha256", sha256);
            reference.put("fileName", brandFileName(brandSlot, extensions.get(contentType)));
        }
        assets.insertReference(assetId, versionNo, surveyId, draftVersion);
        audit.record(ctx, AssetAudit.REFERENCE, assetId, "v" + versionNo + " survey/" + surveyId);
    }

    private static String brandFileName(String brandSlot, String extension) {
        return (brandSlot.equals("logoAsset") ? "mjy-brand-logo" : "mjy-brand-favicon") + extension;
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
