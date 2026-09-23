package cn.mjy.platform.survey;

import cn.mjy.platform.survey.gateway.CloseOutcome;
import cn.mjy.platform.survey.gateway.DriftOutcome;
import cn.mjy.platform.survey.gateway.GatewayBinding;
import cn.mjy.platform.survey.gateway.GatewayCloseRequest;
import cn.mjy.platform.survey.gateway.GatewayDriftRequest;
import cn.mjy.platform.survey.gateway.GatewayOutcome;
import cn.mjy.platform.survey.gateway.GatewayRequest;
import cn.mjy.platform.survey.gateway.GatewayResult;
import cn.mjy.platform.survey.gateway.PublishGatewayClient;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.Function;
import org.springframework.context.annotation.Primary;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * 测试用发布网关：按问卷（definition.uuid）编排应答，并像真网关一样按 requestId 幂等——
 * 同一 requestId 再次到达时返回首次的结果，不会"再发布一次"。
 * 标 {@code @Primary}，测试里问卷模块只会调用本替身。
 */
@Primary
@Component
public class FakePublishGateway implements PublishGatewayClient {

    public static final String COMPILER_VERSION = "pubgw-lss-1";
    public static final String FINGERPRINT = "fm1:74e0d199d9839cdc";

    private static final AtomicInteger NEXT_SID = new AtomicInteger(700_000);

    private final JsonMapper json;
    private final List<GatewayRequest> calls = new CopyOnWriteArrayList<>();
    private final Map<UUID, Function<GatewayRequest, GatewayOutcome>> scripts = new ConcurrentHashMap<>();
    private final Map<UUID, GatewayOutcome> resultsByRequestId = new ConcurrentHashMap<>();
    private final Map<UUID, CountDownLatch> holds = new ConcurrentHashMap<>();
    private final Map<UUID, CountDownLatch> entered = new ConcurrentHashMap<>();
    private final List<GatewayCloseRequest> closeCalls = new CopyOnWriteArrayList<>();
    private final Set<Integer> failingCloses = ConcurrentHashMap.newKeySet();
    private final List<GatewayDriftRequest> driftCalls = new CopyOnWriteArrayList<>();
    private final Map<Integer, Function<GatewayDriftRequest, DriftOutcome>> driftScripts = new ConcurrentHashMap<>();

    public FakePublishGateway(JsonMapper json) {
        this.json = json;
    }

    @Override
    public boolean isConfigured() {
        return true;
    }

    @Override
    public GatewayOutcome publish(GatewayRequest request) {
        calls.add(request);
        UUID survey = surveyOf(request);
        entered.computeIfAbsent(survey, k -> new CountDownLatch(1)).countDown();
        awaitRelease(survey);
        return respond(survey, request);
    }

    /** 查已有结果与执行脚本必须原子：同一 requestId 并发到达时只能"发布"一次。 */
    private synchronized GatewayOutcome respond(UUID survey, GatewayRequest request) {
        GatewayOutcome previous = resultsByRequestId.get(request.requestId());
        if (previous != null) {
            return previous;
        }
        GatewayOutcome outcome = scripts.getOrDefault(survey, this::success).apply(request);
        if (!(outcome instanceof GatewayOutcome.Unknown)) {
            resultsByRequestId.put(request.requestId(), outcome);
        }
        return outcome;
    }

    /** 收口：按 sid 记录调用；被 {@link #failClose} 标记的 sid 返回失败，其余一律已收口。 */
    @Override
    public CloseOutcome close(GatewayCloseRequest request) {
        closeCalls.add(request);
        if (failingCloses.contains(request.surveyId())) {
            return new CloseOutcome.NotClosed("http 502 engine_error");
        }
        return new CloseOutcome.Closed("2026-09-21 08:00:00", false);
    }

    /** 漂移检查：按 sid 记录调用；未编排时一律 match（当前指纹即期望指纹）。 */
    @Override
    public DriftOutcome driftCheck(GatewayDriftRequest request) {
        driftCalls.add(request);
        Function<GatewayDriftRequest, DriftOutcome> script = driftScripts.get(request.surveyId());
        if (script != null) {
            return script.apply(request);
        }
        return new DriftOutcome.Checked(false, request.expectedFingerprint(), "Y", List.of(), List.of());
    }

    public void failClose(int sid) {
        failingCloses.add(sid);
    }

    public void allowClose(int sid) {
        failingCloses.remove(sid);
    }

    public List<GatewayCloseRequest> closeCallsFor(int sid) {
        return closeCalls.stream().filter(call -> call.surveyId() == sid).toList();
    }

    public void scriptDrift(int sid, Function<GatewayDriftRequest, DriftOutcome> behaviour) {
        driftScripts.put(sid, behaviour);
    }

    public List<GatewayDriftRequest> driftCallsFor(int sid) {
        return driftCalls.stream().filter(call -> call.surveyId() == sid).toList();
    }

    /** 为某份问卷指定应答；未指定时一律成功。 */
    public void script(UUID survey, Function<GatewayRequest, GatewayOutcome> behaviour) {
        scripts.put(survey, behaviour);
    }

    /** 模拟"网关其实发布成功了，但应答在路上丢了"：记下成功结果，本次调用却返回结果未知。 */
    public void timeoutAfterPublishing(UUID survey) {
        scripts.put(survey, request -> {
            resultsByRequestId.put(request.requestId(), success(request));
            return new GatewayOutcome.Unknown("read timed out");
        });
    }

    /** 让该问卷的下一次调用停在网关里，直到 {@link #release} 被调用。 */
    public void hold(UUID survey) {
        holds.put(survey, new CountDownLatch(1));
        entered.put(survey, new CountDownLatch(1));
    }

    public boolean awaitEntered(UUID survey, long seconds) throws InterruptedException {
        return entered.get(survey).await(seconds, TimeUnit.SECONDS);
    }

    /** 等到该问卷累计收到 count 次调用（被 hold 住的调用在进入时即计数）。 */
    public boolean awaitCalls(UUID survey, int count, long seconds) throws InterruptedException {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(seconds);
        while (callsFor(survey).size() < count) {
            if (System.nanoTime() > deadline) {
                return false;
            }
            TimeUnit.MILLISECONDS.sleep(10);
        }
        return true;
    }

    public void release(UUID survey) {
        CountDownLatch latch = holds.remove(survey);
        if (latch != null) {
            latch.countDown();
        }
    }

    public List<GatewayRequest> callsFor(UUID survey) {
        List<GatewayRequest> result = new ArrayList<>();
        for (GatewayRequest call : calls) {
            if (surveyOf(call).equals(survey)) {
                result.add(call);
            }
        }
        return result;
    }

    public GatewayOutcome success(GatewayRequest request) {
        JsonNode definition = json.readTree(request.definitionJson());
        int sid = NEXT_SID.incrementAndGet();
        List<GatewayBinding.QuestionBinding> questions = new ArrayList<>();
        int column = 100;
        for (JsonNode group : definition.get("groups")) {
            for (JsonNode question : group.get("questions")) {
                String field = "Q" + (column++);
                questions.add(new GatewayBinding.QuestionBinding(
                        question.get("uuid").asString(), question.get("code").asString(),
                        question.get("type").asString(), fields(question, field)));
            }
        }
        GatewayBinding binding = new GatewayBinding(request.engineInstanceId(), sid,
                definition.get("uuid").asString(), COMPILER_VERSION, "fm1", FINGERPRINT,
                definition.get("language").asString(), "2026-09-22T08:00:00Z", questions);
        return new GatewayOutcome.Published(
                new GatewayResult(true, sid, null, List.of(), false, null, binding));
    }

    /**
     * 每道题一列，排序题（{@code R}）除外：它像真网关那样多出「名次」虚列
     * （JSON 主列 ＋ aid 为 1…n 的名次列，见 {@code pubgw/qtypes.py} 的 _ranking_rows）。
     */
    private static List<GatewayBinding.FieldBinding> fields(JsonNode question, String field) {
        List<GatewayBinding.FieldBinding> fields = new ArrayList<>();
        fields.add(new GatewayBinding.FieldBinding(field, "", 0));
        if ("R".equals(question.get("type").asString())) {
            int rank = 0;
            for (JsonNode ignored : question.path("subquestions")) {
                rank++;
                fields.add(new GatewayBinding.FieldBinding(field + "_S" + rank, Integer.toString(rank), 0));
            }
        }
        return List.copyOf(fields);
    }

    public static GatewayOutcome rejected(String... failures) {
        return new GatewayOutcome.Failed(422,
                new GatewayResult(false, null, "validate", List.of(failures), false, null, null));
    }

    public static GatewayOutcome engineFailed(int orphanSid, String... failures) {
        Integer orphan = orphanSid > 0 ? orphanSid : null;
        return new GatewayOutcome.Failed(502,
                new GatewayResult(false, orphanSid > 0 ? orphanSid : 1234, "activate", List.of(failures),
                        orphan == null, orphan, null));
    }

    private UUID surveyOf(GatewayRequest request) {
        return UUID.fromString(json.readTree(request.definitionJson()).get("uuid").asString());
    }

    private void awaitRelease(UUID survey) {
        CountDownLatch latch = holds.get(survey);
        if (latch == null) {
            return;
        }
        try {
            if (!latch.await(30, TimeUnit.SECONDS)) {
                throw new IllegalStateException("held gateway call was never released");
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException(e);
        }
    }
}
