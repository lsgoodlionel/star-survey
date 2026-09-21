package cn.mjy.platform.engine.support;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

/**
 * 契约测试依赖 {@link PhpJson} 与 PHP 的 {@code json_encode(..., JSON_UNESCAPED_UNICODE)} 输出一致。
 * 期望值是 PHP 8 对同一输入的实际输出（逐字节）。
 */
class PhpJsonTest {

    @Test
    void matchesPhpOutputForTheRelayBodyShape() {
        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("eventId", "2f1c7c1e-9d3a-4b6e-8f00-0a1b2c3d4e5f");
        envelope.put("surveyId", 880001);
        envelope.put("source", "hook");

        // php -r 'echo json_encode(["events"=>[["eventId"=>"2f1c…","surveyId"=>880001,"source"=>"hook"]]], JSON_UNESCAPED_UNICODE);'
        assertThat(PhpJson.encode(Map.of("events", List.of(envelope))))
                .isEqualTo("{\"events\":[{\"eventId\":\"2f1c7c1e-9d3a-4b6e-8f00-0a1b2c3d4e5f\","
                        + "\"surveyId\":880001,\"source\":\"hook\"}]}");
    }

    @Test
    void escapesSlashesButLeavesUnicodeUnescaped() {
        // php -r 'echo json_encode(["a"=>"华东/engine-01"], JSON_UNESCAPED_UNICODE);'  → {"a":"华东\/engine-01"}
        assertThat(PhpJson.encode(Map.of("a", "华东/engine-01"))).isEqualTo("{\"a\":\"华东\\/engine-01\"}");
    }

    @Test
    void escapesQuotesBackslashesAndControlCharactersLikePhp() {
        // php -r 'echo json_encode(["a"=>"q\"b\\\\n\n\t\x01"], JSON_UNESCAPED_UNICODE);'
        // 引号与反斜杠加反斜杠，换行与制表用短转义，0x01 用小写四位十六进制转义。
        assertThat(PhpJson.encode(Map.of("a", "q\"b\\n\n\t")))
                .isEqualTo("{\"a\":\"q\\\"b\\\\n\\n\\t\\u0001\"}");
    }

    @Test
    void encodesAnEmptyEnvelopeListAsAJsonArray() {
        assertThat(PhpJson.encode(Map.of("events", List.of()))).isEqualTo("{\"events\":[]}");
    }
}
