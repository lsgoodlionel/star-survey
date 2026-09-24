package cn.mjy.platform.asset;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.tenant.engine.EngineInstanceService;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;

/**
 * 拉取作答者上传时的租户隔离（独立安全审查抓到的 CRITICAL）。
 *
 * <p>问题的形状：拉取端点把 {@code engineInstanceId} / {@code engineSid} / {@code generation}
 * <b>原样从请求体里接过来</b>，却从不核对那台引擎是不是调用方这个租户的。
 * 于是甲租户一个普通的 {@code edit} 成员，只要点名乙租户的实例标识与答卷号，
 * 就能把<b>乙租户作答者的录音拉进甲租户自己的资产库</b>，再用
 * {@code GET /v1/assets/{id}/versions/{n}/content} 读出来——
 * 决定 9 那一整套按身份授权从侧面被整个绕开，ADR 0002 的租户隔离也一并作废。
 *
 * <p>本仓库其余每一条读路径都<b>不接受</b>调用方直接给引擎坐标：它们从租户作用域内的
 * 问卷行解析出来（{@code ResponseQueryService} 那条）。拉取端点是本切片里唯一的例外，
 * 因此也是唯一需要显式补判定的地方：{@code engine_instance} 本来就带
 * {@code UNIQUE (tenant_id, id)} 与行级安全，在租户作用域内查不到就说明不是你的。
 */
@AssetIntegrationTest
class ResponderUploadTenantIsolationTest {

    private static final long SID = 4242L;
    private static final String GENERATION = "gen-1";

    @Autowired
    private AssetFixture fixture;

    @Autowired
    private ResponderAssetService responderAssets;

    @Autowired
    private EngineInstanceService instances;

    private TenantContext alpha;
    private TenantContext beta;
    private String alphaEngine;
    private String betaEngine;

    @BeforeEach
    void twoTenantsEachWithTheirOwnEngine() {
        AssetFixture.EngineWorkspace a = fixture.newTenantWithEngine();
        AssetFixture.EngineWorkspace b = fixture.newTenantWithEngine();
        alpha = a.owner();
        beta = b.owner();
        alphaEngine = a.engineInstanceId();
        betaEngine = b.engineInstanceId();
    }

    /** 本用例是整条的核心：点名别人的引擎，一个字节都不许拉进来。 */
    @Test
    void atenantCannotPullUploadsFromAnotherTenantsEngine() {
        RecordingUploadSource source = new RecordingUploadSource();
        ResponderUploadPuller puller = puller(source);

        assertThatThrownBy(() -> puller.pull(alpha, betaEngine, SID, GENERATION, List.of(7L)))
                .isInstanceOf(AssetNotFoundException.class);

        // 判定必须发生在**出网之前**：否则别人的作答者令牌与文件名已经进过本租户的进程了。
        assertThat(source.manifestCalls()).as("不该为别人的引擎发出任何请求").isZero();
        assertThat(responderAssets.forResponse(alpha, betaEngine, SID, GENERATION, 7L)).isEmpty();
    }

    /** 压根没登记过的实例标识同样拒绝——与"不是你的"同一句话。 */
    @Test
    void anUnregisteredEngineInstanceIsRefused() {
        RecordingUploadSource source = new RecordingUploadSource();

        assertThatThrownBy(() -> puller(source).pull(alpha, "never-registered-engine", SID, GENERATION,
                List.of(7L)))
                .isInstanceOf(AssetNotFoundException.class);
        assertThat(source.manifestCalls()).isZero();
    }

    /** 自己的引擎照常能拉——挡住的是越界，不是功能。 */
    @Test
    void atenantCanStillPullFromItsOwnEngine() {
        UUID token = UUID.randomUUID();
        RecordingUploadSource source = new RecordingUploadSource().with(token, 7L);

        ResponderUploadPullResult result = puller(source).pull(alpha, alphaEngine, SID, GENERATION, List.of(7L));

        assertThat(result.ingested()).isEqualTo(1);
        assertThat(source.manifestCalls()).isEqualTo(1);
    }

    private ResponderUploadPuller puller(ResponderUploadSource source) {
        return new ResponderUploadPuller(source, (instance, sid, ids) -> java.util.Map.of(), responderAssets,
                instances);
    }

    /** 记下有没有真的出网。 */
    private static final class RecordingUploadSource implements ResponderUploadSource {

        private final List<ResponderUploadRef> refs = new ArrayList<>();
        private int manifestCalls;

        RecordingUploadSource with(UUID token, long responseId) {
            refs.add(new ResponderUploadRef(token, responseId, "REC1", "a.webm", "webm",
                    AssetFixture.webm().length));
            return this;
        }

        int manifestCalls() {
            return manifestCalls;
        }

        @Override
        public List<ResponderUploadRef> manifest(String engineInstanceId, long engineSid, String generation,
                List<Long> responseIds) {
            manifestCalls++;
            return List.copyOf(refs);
        }

        @Override
        public ResponderUploadBytes content(String engineInstanceId, long engineSid, String generation,
                UUID uploadToken) {
            return new ResponderUploadBytes(refs.stream()
                    .filter(ref -> ref.uploadToken().equals(uploadToken)).findFirst().orElseThrow(),
                    AssetFixture.webm());
        }
    }
}
