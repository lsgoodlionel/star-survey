package cn.mjy.platform.asset;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.survey.InvalidDefinitionException;
import java.io.ByteArrayInputStream;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

class SurveyBrandAssetBundleTest {

    @Test
    void acceptsPinnedBrandBytesOnTheConfiguredLoopbackHttpOrigin() {
        TenantId tenant = new TenantId(UUID.randomUUID());
        UUID assetId = UUID.randomUUID();
        byte[] bytes = AssetFixture.png();
        AssetService assetService = mock(AssetService.class);
        when(assetService.open(tenant, assetId, 1)).thenReturn(new AssetContent("image/png", bytes.length,
                sha256(bytes), new ByteArrayInputStream(bytes)));
        SurveyAssetLinks links = new SurveyAssetLinks(mock(cn.mjy.platform.shared.tenant.TenantScope.class),
                mock(AssetRepository.class), mock(AssetTickets.class), mock(AssetAudit.class),
                new AssetProperties("", 1024, java.time.Duration.ofDays(1), "http://127.0.0.1:61903"), assetService);
        ObjectNode definition = JsonMapper.builder().build().createObjectNode();
        definition.putObject("branding").putObject("logoAsset")
                .put("url", "http://127.0.0.1:61903/a/" + tenant.value() + "/" + assetId + "/1?exp=1&sig=test")
                .put("assetVersion", 1)
                .put("contentType", "image/png")
                .put("sizeBytes", bytes.length)
                .put("sha256", sha256(bytes))
                .put("fileName", "mjy-brand-logo.png");

        assertThat(links.brandAssets(tenant, definition)).hasSize(1);

        ((ObjectNode) definition.get("branding").get("logoAsset"))
                .put("url", "http://survey.example/a/" + tenant.value() + "/" + assetId + "/1?exp=1&sig=test");
        assertThatThrownBy(() -> links.brandAssets(tenant, definition))
                .isInstanceOf(InvalidDefinitionException.class)
                .hasMessageContaining("invalid platform asset URL");
    }

    @Test
    void rejectsBytesWhoseDigestNoLongerMatchesThePinnedAssetVersion() {
        TenantId tenant = new TenantId(UUID.randomUUID());
        UUID assetId = UUID.randomUUID();
        AssetService assetService = mock(AssetService.class);
        byte[] bytes = AssetFixture.png();
        when(assetService.open(tenant, assetId, 1)).thenReturn(new AssetContent("image/png", bytes.length,
                "0".repeat(64), new ByteArrayInputStream(bytes)));
        SurveyAssetLinks links = new SurveyAssetLinks(mock(cn.mjy.platform.shared.tenant.TenantScope.class),
                mock(AssetRepository.class), mock(AssetTickets.class), mock(AssetAudit.class),
                new AssetProperties("", 1024, java.time.Duration.ofDays(1), "https://survey.example"), assetService);
        ObjectNode definition = JsonMapper.builder().build().createObjectNode();
        ObjectNode branding = definition.putObject("branding");
        branding.putObject("logoAsset")
                .put("url", "https://survey.example/a/" + tenant.value() + "/" + assetId + "/1?exp=1&sig=test")
                .put("assetVersion", 1)
                .put("contentType", "image/png")
                .put("sizeBytes", bytes.length)
                .put("sha256", sha256(bytes))
                .put("fileName", "mjy-brand-logo.png");

        assertThatThrownBy(() -> links.brandAssets(tenant, definition))
                .isInstanceOf(InvalidDefinitionException.class)
                .hasMessageContaining("stored asset digest");
    }

    @Test
    void rejectsBundleWhoseDigestDoesNotMatchThePinnedDefinitionIdentity() {
        TenantId tenant = new TenantId(UUID.randomUUID());
        UUID assetId = UUID.randomUUID();
        AssetService assetService = mock(AssetService.class);
        byte[] bytes = AssetFixture.png();
        when(assetService.open(tenant, assetId, 1)).thenReturn(new AssetContent("image/png", bytes.length,
                sha256(bytes), new ByteArrayInputStream(bytes)));
        SurveyAssetLinks links = new SurveyAssetLinks(mock(cn.mjy.platform.shared.tenant.TenantScope.class),
                mock(AssetRepository.class), mock(AssetTickets.class), mock(AssetAudit.class),
                new AssetProperties("", 1024, java.time.Duration.ofDays(1), "https://survey.example"), assetService);
        ObjectNode definition = JsonMapper.builder().build().createObjectNode();
        ObjectNode branding = definition.putObject("branding");
        branding.putObject("logoAsset")
                .put("url", "https://survey.example/a/" + tenant.value() + "/" + assetId + "/1?exp=1&sig=test")
                .put("assetVersion", 1)
                .put("contentType", "image/png")
                .put("sizeBytes", bytes.length)
                .put("sha256", "0".repeat(64))
                .put("fileName", "mjy-brand-logo.png");

        assertThatThrownBy(() -> links.brandAssets(tenant, definition))
                .isInstanceOf(InvalidDefinitionException.class)
                .hasMessageContaining("pinned brand digest");
    }

    private static String sha256(byte[] bytes) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
        } catch (java.security.NoSuchAlgorithmException e) {
            throw new AssertionError(e);
        }
    }
}
