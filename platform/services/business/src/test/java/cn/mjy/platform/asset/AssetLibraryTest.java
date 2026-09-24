package cn.mjy.platform.asset;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantContext;
import java.io.IOException;
import java.io.InputStream;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/** 资产库的基本能力：上传、留版本、列出、停用、删除守卫（ADR 0019 决定 1 与 6）。 */
@SpringBootTest
class AssetLibraryTest {

    @Autowired
    private AssetFixture fixture;

    @Autowired
    private AssetService assets;

    private TenantContext owner;

    @BeforeEach
    void seed() {
        owner = fixture.newTenant();
    }

    @Test
    void uploadingAnImageCreatesVersionOneAndRecordsItsDigest() {
        AssetView view = assets.create(owner, "封面", upload("image/png", "cover.png", AssetFixture.png()));

        assertThat(view.id()).isNotNull();
        assertThat(view.kind()).isEqualTo(AssetKind.IMAGE);
        assertThat(view.status()).isEqualTo(AssetStatus.ACTIVE);
        assertThat(view.currentVersion()).isEqualTo(1);
        assertThat(view.contentType()).isEqualTo("image/png");
        assertThat(view.byteSize()).isEqualTo(AssetFixture.png().length);
        assertThat(view.sha256()).matches("[0-9a-f]{64}");
    }

    @Test
    void uploadingAgainOntoTheSameAssetKeepsTheOldVersionReadable() throws IOException {
        AssetView first = assets.create(owner, "封面", upload("image/png", "cover.png", AssetFixture.png()));
        AssetView second = assets.addVersion(owner, first.id(), upload("image/jpeg", "cover.jpg",
                AssetFixture.jpeg()));

        assertThat(second.currentVersion()).isEqualTo(2);
        assertThat(second.contentType()).isEqualTo("image/jpeg");
        // 第一版必须还在：媒体版本留存就是这一条。
        assertThat(bytesOf(owner, first.id(), 1)).isEqualTo(AssetFixture.png());
        assertThat(bytesOf(owner, first.id(), 2)).isEqualTo(AssetFixture.jpeg());
        assertThat(assets.versions(owner, first.id())).hasSize(2);
    }

    @Test
    void listReturnsTheTenantsActiveAssetsNewestFirst() {
        AssetView older = assets.create(owner, "甲", upload("image/png", "a.png", AssetFixture.png()));
        AssetView newer = assets.create(owner, "乙", upload("image/gif", "b.gif", AssetFixture.gif()));

        List<AssetView> listed = assets.list(owner, 50);

        assertThat(listed).extracting(AssetView::id).containsExactly(newer.id(), older.id());
    }

    @Test
    void archivingHidesAnAssetFromTheLibraryButKeepsItReadable() throws IOException {
        AssetView view = assets.create(owner, "封面", upload("image/png", "cover.png", AssetFixture.png()));

        assets.archive(owner, view.id());

        assertThat(assets.list(owner, 50)).isEmpty();
        assertThat(assets.get(owner, view.id()).status()).isEqualTo(AssetStatus.ARCHIVED);
        assertThat(bytesOf(owner, view.id(), 1)).isEqualTo(AssetFixture.png());
    }

    @Test
    void deletingAnUnreferencedAssetRemovesItsMetadataAndItsBytes() {
        AssetView view = assets.create(owner, "封面", upload("image/png", "cover.png", AssetFixture.png()));

        assets.delete(owner, view.id());

        assertThatThrownBy(() -> assets.get(owner, view.id())).isInstanceOf(AssetNotFoundException.class);
        assertThatThrownBy(() -> assets.open(owner.tenantId(), view.id(), 1))
                .isInstanceOf(AssetNotFoundException.class);
    }

    @Test
    void anotherTenantsAssetIsSimplyNotThere() {
        AssetView mine = assets.create(owner, "封面", upload("image/png", "cover.png", AssetFixture.png()));
        TenantContext stranger = fixture.newTenant();

        assertThatThrownBy(() -> assets.get(stranger, mine.id())).isInstanceOf(AssetNotFoundException.class);
        assertThatThrownBy(() -> assets.open(stranger.tenantId(), mine.id(), 1))
                .isInstanceOf(AssetNotFoundException.class);
        assertThat(assets.list(stranger, 50)).isEmpty();
    }

    @Test
    void readingAVersionThatDoesNotExistIsNotFound() {
        AssetView view = assets.create(owner, "封面", upload("image/png", "cover.png", AssetFixture.png()));

        assertThatThrownBy(() -> assets.open(owner.tenantId(), view.id(), 2))
                .isInstanceOf(AssetNotFoundException.class);
        assertThatThrownBy(() -> assets.open(owner.tenantId(), UUID.randomUUID(), 1))
                .isInstanceOf(AssetNotFoundException.class);
    }

    @Test
    void aViewerSeesTheLibraryButCannotUploadIntoIt() {
        AssetView existing = assets.create(owner, "封面", upload("image/png", "c.png", AssetFixture.png()));
        TenantContext viewer = fixture.viewer(owner, "viewer");

        assertThat(assets.list(viewer, 50)).extracting(AssetView::id).containsExactly(existing.id());
        assertThatThrownBy(() -> assets.create(viewer, "别的", upload("image/png", "d.png", AssetFixture.png())))
                .isInstanceOf(AssetAccessDeniedException.class);
        assertThatThrownBy(() -> assets.archive(viewer, existing.id()))
                .isInstanceOf(AssetAccessDeniedException.class);
        assertThatThrownBy(() -> assets.delete(viewer, existing.id()))
                .isInstanceOf(AssetAccessDeniedException.class);
    }

    private byte[] bytesOf(TenantContext ctx, UUID assetId, int version) throws IOException {
        try (InputStream stream = assets.open(ctx.tenantId(), assetId, version).content()) {
            return stream.readAllBytes();
        }
    }

    private static AssetUpload upload(String contentType, String name, byte[] content) {
        return new AssetUpload(contentType, name, content);
    }
}
