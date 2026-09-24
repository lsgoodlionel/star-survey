package cn.mjy.platform.response;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * 纯单元：从定义快照里认出「有副表的题」。网关不存绑定，副表题由平台点名
 * （契约 response-read-v1「扩展表作答」），认错了就要么少读一道题、要么被网关 400 顶回来。
 */
class ExtensionQuestionsTest {

    private final JsonMapper json = JsonMapper.builder().build();

    private JsonNode definition(String questions) {
        return json.readTree("{\"groups\":[{\"questions\":[" + questions + "]}]}");
    }

    @Test
    void returnsOnlySideTableThemesInDefinitionOrder() {
        JsonNode definition = definition("""
                {"code":"QA","type":"L","theme":"mjy-grouped-options"},
                {"code":"QTABLE","type":"T","theme":"mjy-repeating-table"},
                {"code":"QPLAIN","type":"S"},
                {"code":"QHEAT","type":"T","theme":"mjy-heatmap"},
                {"code":"QBLANK","type":"P","theme":"mjy-inline-blank"}
                """);

        assertThat(ExtensionQuestions.codes(definition)).containsExactly("QTABLE", "QHEAT");
    }

    @Test
    void coversEveryStructuredThemeTheFieldDictionaryCallsAJsonEnvelope() {
        StringBuilder questions = new StringBuilder();
        int index = 0;
        for (String theme : QuestionTypeColumns.STRUCTURED_THEMES) {
            questions.append(index++ > 0 ? "," : "")
                    .append("{\"code\":\"Q").append(index).append("\",\"type\":\"T\",\"theme\":\"")
                    .append(theme).append("\"}");
        }

        assertThat(ExtensionQuestions.codes(definition(questions.toString())))
                .hasSize(QuestionTypeColumns.STRUCTURED_THEMES.size());
    }

    @Test
    void groupsWithoutQuestionsAndQuestionsWithoutCodesAreSkipped() {
        assertThat(ExtensionQuestions.codes(json.readTree("{}"))).isEmpty();
        assertThat(ExtensionQuestions.codes(definition("{\"type\":\"T\",\"theme\":\"mjy-repeating-table\"}")))
                .isEmpty();
    }

    /** 同一个题目代码出现两次（理论上不会发生）只点名一次：网关要求互不相同，重复即 400。 */
    @Test
    void duplicateCodesAreNamedOnlyOnce() {
        JsonNode definition = definition("""
                {"code":"QTABLE","type":"T","theme":"mjy-repeating-table"},
                {"code":"QTABLE","type":"T","theme":"mjy-shelf"}
                """);

        assertThat(ExtensionQuestions.codes(definition)).containsExactly("QTABLE");
    }
}
