package cn.mjy.platform.asset;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;

class AssetViewJsonContractTest {

    private final JsonMapper json = JsonMapper.builder().build();

    @Test
    void assetEnumsUseTheDocumentedLowercaseApiCodes() {
        assertThat(json.writeValueAsString(AssetKind.IMAGE)).isEqualTo("\"image\"");
        assertThat(json.writeValueAsString(AssetStatus.ACTIVE)).isEqualTo("\"active\"");
    }
}
