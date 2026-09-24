package cn.mjy.platform.asset;

import cn.mjy.platform.asset.AssetRepository.AssetRow;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.storage.BlobStore;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.List;
import java.util.UUID;
import org.springframework.stereotype.Service;

/**
 * 资产库（ADR 0019）。作者上传的图片 / 音频 / 视频在这里获得平台自己的 id 与版本号，
 * 取代"题型里作者填一串 URL"的临时做法。
 *
 * <p>三道上传闸门（大小、类型白名单、文件头）在 {@link #validated} 里，**先于**任何落盘与落库。
 * 入库与回放用的内容类型一律是嗅探结果，不是调用方声明的那个。
 *
 * <p>字节先写存储、再写元数据：写存储失败就什么都没发生；元数据失败会留下一段孤儿字节
 * （按 ADR 0019 已知限制 6，目前没有后台清扫），但绝不会出现"库里有行、字节没有"这种
 * 让作答页开天窗的状态。
 */
@Service
public class AssetService {

    private static final String KEY_PREFIX = "asset/";

    private final TenantScope tenantScope;
    private final AssetRepository assets;
    private final AssetFileStore files;
    private final AssetAccess access;
    private final AssetAudit audit;
    private final AssetProperties properties;

    AssetService(TenantScope tenantScope, AssetRepository assets, AssetFileStore files, AssetAccess access,
            AssetAudit audit, AssetProperties properties) {
        this.tenantScope = tenantScope;
        this.assets = assets;
        this.files = files;
        this.access = access;
        this.audit = audit;
        this.properties = properties;
    }

    /** 新建一件资产，内容即第 1 版。 */
    public AssetView create(TenantContext ctx, String name, AssetUpload upload) {
        String assetName = requireName(name);
        Checked checked = validated(upload);
        UUID id = UUID.randomUUID();
        String key = storageKey(ctx.tenantId(), id, 1);
        return tenantScope.call(ctx.tenantId(), () -> {
            // 判权在落盘之前：没有 edit 的人连一个字节都不该写进存储。
            access.requireWrite(ctx);
            write(key, upload.content());
            assets.insertAsset(id, assetName, checked.kind(), ctx.actorId());
            assets.insertVersion(id, 1, key, checked.contentType(), checked.byteSize(), checked.sha256(),
                    requireOriginalName(upload), ctx.actorId());
            audit.record(ctx, AssetAudit.CREATE, id, "v1 " + checked.contentType() + " " + checked.byteSize());
            return view(assets.readAsset(id).orElseThrow(), 1);
        });
    }

    /** 给已有资产追加一个新版本；旧版本原样留着（媒体版本留存）。 */
    public AssetView addVersion(TenantContext ctx, UUID assetId, AssetUpload upload) {
        Checked checked = validated(upload);
        return tenantScope.call(ctx.tenantId(), () -> {
            access.requireWrite(ctx);
            assets.findAsset(assetId).orElseThrow(() -> notFound(assetId));
            int versionNo = assets.nextVersion(assetId);
            String key = storageKey(ctx.tenantId(), assetId, versionNo);
            write(key, upload.content());
            assets.insertVersion(assetId, versionNo, key, checked.contentType(), checked.byteSize(),
                    checked.sha256(), requireOriginalName(upload), ctx.actorId());
            audit.record(ctx, AssetAudit.VERSION, assetId, "v" + versionNo + " " + checked.contentType());
            return view(assets.readAsset(assetId).orElseThrow(() -> notFound(assetId)), versionNo);
        });
    }

    public List<AssetView> list(TenantContext ctx, int limit) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.requireRead(ctx);
            return assets.listActive(limit).stream().map(row -> view(row, row.currentVersion())).toList();
        });
    }

    public AssetView get(TenantContext ctx, UUID assetId) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.requireRead(ctx);
            AssetRow row = assets.readAsset(assetId).orElseThrow(() -> notFound(assetId));
            return view(row, row.currentVersion());
        });
    }

    public List<AssetVersionView> versions(TenantContext ctx, UUID assetId) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.requireRead(ctx);
            assets.readAsset(assetId).orElseThrow(() -> notFound(assetId));
            return assets.listVersions(assetId);
        });
    }

    /** 停用：不再能被新的发布引用，已发布的问卷照常回放。 */
    public void archive(TenantContext ctx, UUID assetId) {
        tenantScope.run(ctx.tenantId(), () -> {
            access.requireWrite(ctx);
            assets.findAsset(assetId).orElseThrow(() -> notFound(assetId));
            assets.updateStatus(assetId, AssetStatus.ARCHIVED);
            audit.record(ctx, AssetAudit.ARCHIVE, assetId, "");
        });
    }

    /** 删除：只有从没被任何一版问卷引用过的资产可以删。 */
    public void delete(TenantContext ctx, UUID assetId) {
        tenantScope.run(ctx.tenantId(), () -> {
            access.requireWrite(ctx);
            assets.findAsset(assetId).orElseThrow(() -> notFound(assetId));
            if (assets.hasReferences(assetId)) {
                throw new AssetConflictException(AssetConflictException.ASSET_IN_USE,
                        "asset " + assetId + " is referenced by a published survey; archive it instead");
            }
            assets.deleteAsset(assetId);
            audit.record(ctx, AssetAudit.DELETE, assetId, "");
        });
        deleteBytes(assetPrefix(ctx.tenantId(), assetId));
    }

    /**
     * 回放一个版本的字节。**没有 TenantContext**：匿名取件端点验签之后带着票据里那个
     * （已签名的）租户标识调进来，元数据仍在行级安全作用域内读出，隔离的判定点没有变。
     */
    public AssetContent open(TenantId tenant, UUID assetId, int versionNo) {
        AssetVersionView version = tenantScope.call(tenant, () -> assets.findVersion(assetId, versionNo)
                .orElseThrow(() -> notFound(assetId)));
        try {
            InputStream content = files.open(version.storageKey());
            return new AssetContent(version.contentType(), version.byteSize(), version.sha256(), content);
        } catch (IOException e) {
            throw new AssetUnavailableException("asset bytes are unreadable: " + assetId + " v" + versionNo, e);
        }
    }

    /** 嗅探与校验的结果：之后一切都按这里认出来的来。 */
    private record Checked(String contentType, AssetKind kind, long byteSize, String sha256) {
    }

    private Checked validated(AssetUpload upload) {
        byte[] content = upload == null ? null : upload.content();
        if (content == null || content.length == 0) {
            throw new InvalidAssetException("the uploaded file is empty");
        }
        if (content.length > properties.maxBytes()) {
            throw new InvalidAssetException("the uploaded file is too large: " + content.length
                    + " bytes, the limit is " + properties.maxBytes());
        }
        String contentType = AssetContentTypes.normalise(upload.contentType());
        AssetKind kind = AssetContentTypes.kindOf(contentType)
                .orElseThrow(() -> new InvalidAssetException("unsupported content type: " + contentType));
        if (!AssetContentTypes.contentMatches(contentType, content)) {
            throw new InvalidAssetException("the file content does not match its declared type " + contentType);
        }
        return new Checked(contentType, kind, content.length, sha256(content));
    }

    private void write(String key, byte[] content) {
        try (BlobStore.Upload upload = files.create(key)) {
            OutputStream stream = upload.stream();
            stream.write(content);
            upload.commit();
        } catch (IOException e) {
            throw new AssetUnavailableException("cannot store the asset bytes", e);
        }
    }

    private void deleteBytes(String prefix) {
        try {
            files.deletePrefix(prefix);
        } catch (IOException e) {
            throw new AssetUnavailableException("cannot delete the asset bytes", e);
        }
    }

    private AssetView view(AssetRow row, int versionNo) {
        AssetVersionView version = assets.findVersion(row.id(), versionNo)
                .orElseThrow(() -> notFound(row.id()));
        return new AssetView(row.id(), row.name(), row.kind(), row.status(), versionNo, version.contentType(),
                version.byteSize(), version.sha256(), row.createdAt(), row.updatedAt());
    }

    private static AssetNotFoundException notFound(UUID assetId) {
        return new AssetNotFoundException("asset not found: " + assetId);
    }

    private static String requireName(String name) {
        if (name == null || name.isBlank() || name.length() > 256) {
            throw new InvalidAssetException("the asset name must be 1–256 characters");
        }
        return name.trim();
    }

    /** 原始文件名只作为元数据留档；缺了就记一个占位，绝不用它拼路径。 */
    private static String requireOriginalName(AssetUpload upload) {
        String name = upload.originalName();
        if (name == null || name.isBlank()) {
            return "unnamed";
        }
        return name.length() > 256 ? name.substring(0, 256) : name;
    }

    private static String storageKey(TenantId tenant, UUID assetId, int versionNo) {
        return assetPrefix(tenant, assetId) + "/v" + versionNo;
    }

    private static String assetPrefix(TenantId tenant, UUID assetId) {
        return KEY_PREFIX + tenant.value() + "/" + assetId;
    }

    private static String sha256(byte[] content) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(content));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 unavailable", e);
        }
    }
}
