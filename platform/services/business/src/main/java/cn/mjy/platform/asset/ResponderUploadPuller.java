package cn.mjy.platform.asset;

import cn.mjy.platform.shared.TenantContext;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * 把一页答卷的作答者上传从引擎拉进资产库（ADR 0019 决定 8）。
 *
 * <p>顺序是：<b>先认作答者，再拉清单，再逐件取字节</b>。
 *
 * <ul>
 *   <li><b>清单驱动</b>：拉哪些件完全由插件的上传会话表说了算，答卷字段里那张文件清单
 *       一个字都不参与——本类连那个形状都拿不到。</li>
 *   <li><b>幂等</b>：已经拉过的 token 直接跳过，连字节都不再取一次。判据是
 *       {@code platform_asset_intake} 里有没有那一行，不是本地缓存。</li>
 *   <li><b>失败关闭</b>：清单或字节读不到就整批抛，绝不把"读不到"解释成"这页没有上传"
 *       （与 ADR 0013 决定 8 同一条口径）。</li>
 *   <li><b>坏文件不拖垮整页</b>：过不了平台上传闸门的那一件计入 {@code refused} 并继续——
 *       它是一件坏文件，不是一次读取故障，两者不该混为一谈。</li>
 * </ul>
 *
 * <p><b>参与者令牌不落库</b>：这里现读现用，只用来算指纹（决定 9），用完即弃。
 */
@Service
public class ResponderUploadPuller {

    private static final Logger log = LoggerFactory.getLogger(ResponderUploadPuller.class);

    private final ResponderUploadSource uploads;
    private final RespondentTokens respondents;
    private final ResponderAssetService assets;

    /**
     * 「这份答卷是用哪个邀请码答的」这条窄接缝。生产实现是
     * {@code cn.mjy.platform.response.RespondentSource} 的适配器；资产模块因此不依赖答卷模块的全部。
     */
    @FunctionalInterface
    public interface RespondentTokens {

        /** @return 答卷号 → 参与者令牌，<b>只含确实认得出的那些</b> */
        Map<Long, String> of(String engineInstanceId, long engineSid, List<Long> responseIds);
    }

    public ResponderUploadPuller(ResponderUploadSource uploads, RespondentTokens respondents,
            ResponderAssetService assets) {
        this.uploads = uploads;
        this.respondents = respondents;
        this.assets = assets;
    }

    public ResponderUploadPullResult pull(TenantContext ctx, String engineInstanceId, long engineSid,
            String generation, List<Long> responseIds) {
        if (responseIds.isEmpty()) {
            return new ResponderUploadPullResult(0, 0, 0);
        }
        Map<Long, String> tokens = respondents.of(engineInstanceId, engineSid, responseIds);
        List<ResponderUploadRef> manifest = uploads.manifest(engineInstanceId, engineSid, generation, responseIds);
        Set<UUID> already = ingestedAlready(ctx, engineInstanceId, engineSid, generation, responseIds);
        int ingested = 0;
        int skipped = 0;
        int refused = 0;
        for (ResponderUploadRef ref : manifest) {
            if (already.contains(ref.uploadToken())) {
                skipped++;
                continue;
            }
            // 取字节读不到就整批抛：一页拉一半而没人察觉，比报错危险得多。
            ResponderUploadBytes bytes = uploads.content(engineInstanceId, engineSid, generation,
                    ref.uploadToken());
            requireMatching(ref, bytes);
            try {
                assets.ingest(ctx, upload(engineInstanceId, engineSid, generation, bytes,
                        tokens.get(bytes.ref().responseId())));
                ingested++;
            } catch (InvalidAssetException e) {
                // 坏文件：记下来继续。不记文件名、不记字节。
                log.warn("a responder upload on {} sid {} response {} did not pass the platform gate: {}",
                        engineInstanceId, engineSid, ref.responseId(), e.getMessage());
                refused++;
            }
        }
        log.info("responder upload pull on {} sid {}: {} ingested, {} already present, {} refused",
                engineInstanceId, engineSid, ingested, skipped, refused);
        return new ResponderUploadPullResult(ingested, skipped, refused);
    }

    /** 已经拉过哪些：判据是来源证据表，不是本地缓存。 */
    private Set<UUID> ingestedAlready(TenantContext ctx, String engineInstanceId, long engineSid,
            String generation, List<Long> responseIds) {
        return responseIds.stream()
                .flatMap(id -> assets.forResponse(ctx, engineInstanceId, engineSid, generation, id).stream())
                .map(ResponderAssetView::assetId)
                .collect(Collectors.toUnmodifiableSet());
    }

    /** 网关交回来的那一件必须就是清单上点名的那一件。 */
    private static void requireMatching(ResponderUploadRef wanted, ResponderUploadBytes got) {
        if (got == null || !wanted.uploadToken().equals(got.ref().uploadToken())) {
            throw new ResponderUploadsUnavailableException(
                    "the gateway returned a different upload than the one that was asked for");
        }
    }

    private static ResponderUpload upload(String engineInstanceId, long engineSid, String generation,
            ResponderUploadBytes bytes, String respondentToken) {
        ResponderUploadRef ref = bytes.ref();
        return new ResponderUpload(ref.uploadToken(), engineInstanceId, engineSid, generation, ref.responseId(),
                ref.questionCode(), respondentToken, contentTypeOf(ref), ref.originalName(), bytes.content());
    }

    /**
     * 引擎不记内容类型，只记扩展名。这里按扩展名给出一个<b>声明</b>，
     * 而声明只用来和嗅探结果对照——真正算数的仍然是平台嗅探出来的那个（决定 3）。
     */
    private static String contentTypeOf(ResponderUploadRef ref) {
        return AssetContentTypes.fromExtension(ref.extension());
    }
}
