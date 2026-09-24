package cn.mjy.platform.asset;

import cn.mjy.platform.asset.AssetRepository.AssetRow;
import cn.mjy.platform.asset.AssetService.Checked;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.time.Clock;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * 作答者上传的入库（ADR 0019 决定 8）：资产行由<b>上传会话</b>驱动创建，
 * {@code upload_token} 即资产 id，<b>绝不信任答卷里那张文件清单</b>。
 *
 * <p>三条不变式，每一条都有用例钉着：
 * <ol>
 *   <li><b>只 INSERT，从不 UPDATE。</b>撞上已有 id 时必须核对它确实是同一次上传，
 *       否则拒收——这挡住"引擎侧编一个 UUID 去顶掉一件作者素材"。</li>
 *   <li><b>平台的三道闸门一条不退。</b>引擎的上传限制是引擎的，不是平台的
 *       （{@link AssetService#validated} 与作者上传共用同一条，含剥 EXIF）。</li>
 *   <li><b>令牌不落库。</b>只存租户内不可逆指纹；匿名作答没有令牌，指纹为空，
 *       后果是一张绑定票都签不出来（失败即关闭）。</li>
 * </ol>
 */
@Service
public class ResponderAssetService {

    private static final Logger log = LoggerFactory.getLogger(ResponderAssetService.class);

    /** 资产名不承载信息：原始文件名可能带个人信息，不拿它当名字。 */
    private static final String NAME_PREFIX = "作答上传 ";

    private final TenantScope tenantScope;
    private final AssetRepository assets;
    private final AssetService assetService;
    private final AssetAccess access;
    private final AssetTickets tickets;
    private final AssetProperties properties;
    private final Clock clock;

    ResponderAssetService(TenantScope tenantScope, AssetRepository assets, AssetService assetService,
            AssetAccess access, AssetTickets tickets, AssetProperties properties) {
        this.tenantScope = tenantScope;
        this.assets = assets;
        this.assetService = assetService;
        this.access = access;
        this.tickets = tickets;
        this.properties = properties;
        this.clock = Clock.systemUTC();
    }

    /**
     * 把一次上传会话落成平台资产。同一次上传拉多少遍都只有一件资产。
     *
     * @throws AssetConflictException   这个 id 已被别的东西占着（{@code asset_id_taken}）
     * @throws InvalidAssetException    过不了平台的上传闸门
     * @throws AssetAccessDeniedException 调用方没有 {@code edit}
     */
    public AssetView ingest(TenantContext ctx, ResponderUpload upload) {
        UUID assetId = requireUploadToken(upload);
        Checked checked = assetService.validated(new AssetUpload(upload.contentType(), upload.originalName(),
                upload.content()));
        return tenantScope.call(ctx.tenantId(), () -> {
            access.requireWrite(ctx);
            Optional<AssetRow> existing = assets.readAsset(assetId);
            if (existing.isPresent()) {
                return replayed(ctx, existing.get(), upload, checked);
            }
            String respondentKey = upload.hasRespondentToken()
                    ? tickets.fingerprint(ctx.tenantId(), upload.respondentToken()) : null;
            return assetService.store(ctx, assetId, NAME_PREFIX + upload.questionCode(), checked,
                    new AssetUpload(upload.contentType(), upload.originalName(), checked.content()),
                    AssetOrigin.RESPONDENT, respondentKey, upload);
        });
    }

    /**
     * 这个 id 已经有行了。只有"同一次上传又来一遍"才算幂等重放：来源证据与内容摘要都要对上。
     * 其余一律拒收——撞上作者素材、同一个 token 带着别的字节回来，都是篡改。
     */
    private AssetView replayed(TenantContext ctx, AssetRow row, ResponderUpload upload, Checked checked) {
        if (row.origin() != AssetOrigin.RESPONDENT || !assets.intakeMatches(row.id(), upload)) {
            throw taken(row.id());
        }
        boolean sameBytes = assets.findVersion(row.id(), row.currentVersion())
                .map(version -> version.sha256().equals(checked.sha256()))
                .orElse(false);
        if (!sameBytes) {
            throw taken(row.id());
        }
        return assetService.get(ctx, row.id());
    }

    private static AssetConflictException taken(UUID assetId) {
        // 原因只进服务端日志的必要性在这里不成立：调用方是我们自己的网关，不是匿名可达面。
        log.warn("refused a responder upload whose token collides with an existing asset: {}", assetId);
        return new AssetConflictException(AssetConflictException.ASSET_ID_TAKEN,
                "asset id " + assetId + " is already taken by something else");
    }

    /** 这件资产是谁放进来的。 */
    public AssetOrigin originOf(TenantContext ctx, UUID assetId) {
        return row(ctx, assetId).origin();
    }

    /** 作答者指纹（小写十六进制），没有绑定时为空。<b>只给平台内部判定用</b>，不进 REST 应答。 */
    public Optional<String> respondentKeyOf(TenantContext ctx, UUID assetId) {
        return Optional.ofNullable(row(ctx, assetId).respondentKey());
    }

    /** 一份答卷上有哪些作答者上传。作者审阅答卷时靠它找到那段录音。 */
    public List<ResponderAssetView> forResponse(TenantContext ctx, String engineInstanceId, long engineSid,
            String generation, long responseId) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.requireRead(ctx);
            return assets.listIntake(engineInstanceId, engineSid, generation, responseId);
        });
    }

    /**
     * 给一件作答者资产签一张绑定本人的取件票（ADR 0019 决定 9）。
     *
     * <p>票据里<b>不含令牌</b>——调用方拿到的是一个必须由作答者补上 {@code rt} 才能用的地址。
     * 但签发这一步需要令牌本身来算指纹，而平台库里只有指纹……所以这里<b>反过来</b>：
     * 用库里已经存着的那个指纹直接拼上下文，根本不需要再见到令牌。
     *
     * @throws AssetConflictException 匿名作答没有绑定，签不出票（{@code no_respondent_binding}）
     */
    public AssetTicket mintTicket(TenantContext ctx, UUID assetId, int versionNo) {
        requireTicketsConfigured();
        AssetRow row = row(ctx, assetId);
        if (row.origin() != AssetOrigin.RESPONDENT || row.respondentKey() == null) {
            throw new AssetConflictException(AssetConflictException.NO_RESPONDENT_BINDING,
                    "asset " + assetId + " has no respondent binding; it cannot be fetched with a bound ticket");
        }
        return tickets.mintForFingerprint(ctx.tenantId(), assetId, versionNo, row.respondentKey(),
                clock.instant().plus(properties.ticketTtl()));
    }

    private AssetRow row(TenantContext ctx, UUID assetId) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.requireRead(ctx);
            return assets.readAsset(assetId)
                    .orElseThrow(() -> new AssetNotFoundException("asset not found: " + assetId));
        });
    }

    private void requireTicketsConfigured() {
        if (!tickets.isConfigured() || properties.publicBaseUrl().isBlank()) {
            throw new AssetUnavailableException(AssetKeys.MASTER_SECRET_PROPERTY
                    + " or platform.asset.public-base-url is not configured; tickets cannot be minted");
        }
    }

    private static UUID requireUploadToken(ResponderUpload upload) {
        if (upload == null || upload.uploadToken() == null) {
            throw new InvalidAssetException("a responder upload must carry its upload session token");
        }
        return upload.uploadToken();
    }
}
