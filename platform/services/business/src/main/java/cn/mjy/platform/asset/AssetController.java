package cn.mjy.platform.asset;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.security.CurrentTenant;
import java.io.IOException;
import java.net.URI;
import java.util.List;
import java.util.UUID;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

/**
 * 作者端资产库（ADR 0019）。全部挂在主安全链上——**必须带平台令牌**，租户只从令牌里取。
 * 匿名的取件端点是另一条链上的只读 GET，见 {@link AssetPublicController}。
 */
@RestController
@RequestMapping("/v1/assets")
class AssetController {

    private static final int DEFAULT_LIMIT = 50;
    private static final int MAX_LIMIT = 200;

    private final AssetService assets;
    private final ResponderAssetService responderAssets;
    private final CurrentTenant currentTenant;
    private final String publicBaseUrl;

    AssetController(AssetService assets, ResponderAssetService responderAssets, CurrentTenant currentTenant,
            AssetProperties properties) {
        this.assets = assets;
        this.responderAssets = responderAssets;
        this.currentTenant = currentTenant;
        this.publicBaseUrl = properties.publicBaseUrl();
    }

    @PostMapping(consumes = "multipart/form-data")
    ResponseEntity<AssetView> create(@RequestParam("name") String name, @RequestParam("file") MultipartFile file)
            throws IOException {
        TenantContext ctx = currentTenant.require();
        AssetView view = assets.create(ctx, name, upload(file));
        return ResponseEntity.created(URI.create("/v1/assets/" + view.id())).body(view);
    }

    @PostMapping(value = "/{id}/versions", consumes = "multipart/form-data")
    AssetView addVersion(@PathVariable UUID id, @RequestParam("file") MultipartFile file) throws IOException {
        return assets.addVersion(currentTenant.require(), id, upload(file));
    }

    @GetMapping
    List<AssetView> list(@RequestParam(name = "limit", required = false) Integer limit) {
        return assets.list(currentTenant.require(), limit(limit));
    }

    @GetMapping("/{id}")
    AssetView get(@PathVariable UUID id) {
        return assets.get(currentTenant.require(), id);
    }

    /**
     * 取一个版本的字节。<b>走主安全链</b>：租户级 {@code view}，与取件票完全无关。
     *
     * <p>这是作答者上传（录音、录像、画布快照）在审阅答卷时的读法——
     * 作者不该为了听一段录音去伪造一张绑定票（ADR 0019 决定 9）。
     */
    @GetMapping("/{id}/versions/{version}/content")
    ResponseEntity<?> content(@PathVariable UUID id, @PathVariable int version) {
        TenantContext ctx = currentTenant.require();
        return AssetResponses.serve(id, version, assets.openAsStaff(ctx, id, version));
    }

    /**
     * 给一件作答者上传的资产签一张绑定本人的取件票（ADR 0019 决定 9）。
     *
     * <p>应答里的地址<b>不含令牌</b>：作答页必须自己补上 {@code rt} 才凑得出一次合法取件。
     */
    @PostMapping("/{id}/respondent-ticket")
    RespondentTicketView respondentTicket(@PathVariable UUID id,
            @RequestParam(name = "version", required = false) Integer version) {
        TenantContext ctx = currentTenant.require();
        int versionNo = version == null ? assets.get(ctx, id).currentVersion() : version;
        return new RespondentTicketView(responderAssets.mintTicket(ctx, id, versionNo).url(publicBaseUrl),
                versionNo);
    }

    /** 绑定取件票的对外视图。{@code url} 还差一个 {@code rt} 才能用——这是故意的。 */
    public record RespondentTicketView(String url, int version) {
    }

    /** 停用：不再能被新的发布引用，已发布的问卷照常回放。 */
    @PostMapping("/{id}/archive")
    AssetView archive(@PathVariable UUID id) {
        TenantContext ctx = currentTenant.require();
        assets.archive(ctx, id);
        return assets.get(ctx, id);
    }

    @DeleteMapping("/{id}")
    ResponseEntity<Void> delete(@PathVariable UUID id) {
        assets.delete(currentTenant.require(), id);
        return ResponseEntity.noContent().build();
    }

    /** 文件名与声明类型都来自客户端，只作为线索：真正算数的是服务端的嗅探结果。 */
    private static AssetUpload upload(MultipartFile file) throws IOException {
        if (file == null || file.isEmpty()) {
            throw new InvalidAssetException("the uploaded file is empty");
        }
        return new AssetUpload(file.getContentType(), file.getOriginalFilename(), file.getBytes());
    }

    private static int limit(Integer requested) {
        if (requested == null || requested <= 0) {
            return DEFAULT_LIMIT;
        }
        return Math.min(requested, MAX_LIMIT);
    }
}
