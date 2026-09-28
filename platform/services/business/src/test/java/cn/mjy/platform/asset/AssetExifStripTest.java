package cn.mjy.platform.asset;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.shared.TenantContext;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Duration;
import java.time.Instant;
import java.util.HexFormat;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.web.servlet.MockMvc;

/**
 * 「回放出去的字节里没有 EXIF」——从服务层与取件端点两头钉住（ADR 0019 决定 10）。
 *
 * <p>{@link AssetImageMetadataTest} 证明剥离器本身对；这里证明<b>它真的接在入库路径上</b>，
 * 而且入库记的 {@code sha256} 与字节数是剥完之后那一份。
 */
@AssetIntegrationTest
@AutoConfigureMockMvc
class AssetExifStripTest {

    @Autowired
    private MockMvc mvc;

    @Autowired
    private AssetFixture fixture;

    @Autowired
    private AssetService assets;

    @Autowired
    private ResponderAssetService responderAssets;

    @Autowired
    private AssetTickets tickets;

    private TenantContext owner;

    @BeforeEach
    void seed() {
        owner = fixture.newTenant();
    }

    @Test
    void anAuthorPhotoLosesItsGpsBeforeItIsEverStored() throws Exception {
        byte[] withGps = ImageBytes.jpegWithExif();
        AssetView asset = assets.create(owner, "现场照片", new AssetUpload("image/jpeg", "IMG_1.jpg", withGps));

        byte[] served = fetchBytes(asset.id());

        assertThat(new String(served, StandardCharsets.ISO_8859_1)).doesNotContain(ImageBytes.GPS_MARKER);
        assertThat(served.length).isLessThan(withGps.length);
    }

    /** 作答者上传同样过这一道：作答现场的 GPS 不该落在平台盘上。 */
    @Test
    void aresponderPhotoLosesItsGpsToo() {
        UUID token = UUID.randomUUID();
        byte[] withGps = ImageBytes.pngWithMetadata();

        AssetView view = responderAssets.ingest(owner, new ResponderUpload(token, "engine-a", 4242L, "gen-1",
                11L, "Q1", "invite-code-alice", "image/png", "shot.png", withGps));

        assertThat(view.byteSize()).isLessThan(withGps.length);
        assertThat(view.sha256()).isEqualTo(sha256(AssetImageMetadata.strip("image/png", withGps)));
    }

    /** 摘要与字节数记的是剥完那份——那才是将来回放出去的东西。 */
    @Test
    void thestoredDigestIsOfTheStrippedBytes() {
        byte[] withGps = ImageBytes.jpegWithExif();

        AssetView asset = assets.create(owner, "现场照片", new AssetUpload("image/jpeg", "IMG_1.jpg", withGps));

        assertThat(asset.sha256()).isEqualTo(sha256(AssetImageMetadata.strip("image/jpeg", withGps)))
                .isNotEqualTo(sha256(withGps));
        assertThat(asset.byteSize()).isEqualTo(AssetImageMetadata.strip("image/jpeg", withGps).length);
    }

    /** 剥不动的结构不入库：宁可 400，也不存一份"以为剥过了"的字节。 */
    @Test
    void anUnparsableImageNeverReachesTheStore() {
        assertThat(org.assertj.core.api.Assertions.catchThrowable(() -> assets.create(owner, "坏图",
                new AssetUpload("image/png", "bad.png", ImageBytes.pngWithAtruncatedChunk()))))
                .isInstanceOf(InvalidAssetException.class);
        assertThat(assets.list(owner, 50)).isEmpty();
    }

    private byte[] fetchBytes(UUID assetId) throws Exception {
        Map<String, String> ticket = tickets
                .mint(owner.tenantId(), assetId, 1, Instant.now().plus(Duration.ofDays(1))).query();
        var request = get("/a/{t}/{id}/1", owner.tenantId().value(), assetId);
        ticket.forEach(request::param);
        return mvc.perform(request).andExpect(status().isOk()).andReturn().getResponse().getContentAsByteArray();
    }

    private static String sha256(byte[] content) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(content));
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }
}
