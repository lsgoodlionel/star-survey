package cn.mjy.platform.response;

import static cn.mjy.platform.engine.EngineEventType.COMPLETED;
import static cn.mjy.platform.response.ResponseFixture.GENERATION;
import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.response.ResponseFixture.Published;
import cn.mjy.platform.survey.PublishedVersionView.QuestionFieldView;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * 排序题（{@code R}）的导出：引擎只存一列按名次排列的项代码 JSON，名次列是虚列，
 * 由网关读端点摊开（{@code pubgw/ranking.py}）。平台照收：每个名次一列，没排到的位置为空。
 */
@SpringBootTest
class ResponseExportRankingTest {

    private static final String RANKING_CODE = "QRANK";

    @Autowired
    private ExportFixture fixture;

    @Autowired
    private FakeResponseAnswerSource answers;

    private Published p;
    private Map<String, String> rankingFields;

    @BeforeEach
    void aPublishedRankingSurveyWithOneResponse() {
        p = fixture.responses().publishedSurvey(fixture.responses().rankingDefinition());
        rankingFields = new LinkedHashMap<>();
        for (QuestionFieldView field : p.v1().fields()) {
            if (RANKING_CODE.equals(field.code())) {
                rankingFields.put(field.aid(), field.fieldname());
            }
        }
        fixture.responses().response(p.tenant(), p.instance(), p.sid(), GENERATION, 1, COMPLETED);
        // 网关读端点返回的形状：主列是 JSON，名次列已摊开，第 3 位没人排 → 空串。
        answers.put(p.instance(), p.sid(), 1, Map.of(
                rankingFields.get(""), "[\"I3\",\"I1\"]",
                rankingFields.get("1"), "I3",
                rankingFields.get("2"), "I1",
                rankingFields.get("3"), ""));
    }

    private static String cell(List<List<String>> rows, String column) {
        int index = rows.get(0).indexOf(column);
        assertThat(index).as("column " + column + " in " + rows.get(0)).isNotNegative();
        return rows.get(2).get(index);
    }

    @Test
    void eachRankIsItsOwnColumnHoldingTheItemRankedThere() {
        ExportJobView job = fixture.runToEnd(p.owner(), fixture.create(p.owner(), p, "csv"));
        List<List<String>> rows = ExportFixture.unzipCsv(fixture.download(p.owner(), job)).get("responses.csv");

        assertThat(job.status()).isEqualTo("completed");
        assertThat(cell(rows, RANKING_CODE)).isEqualTo("[\"I3\",\"I1\"]");
        assertThat(cell(rows, RANKING_CODE + "[1]")).isEqualTo("I3");
        assertThat(cell(rows, RANKING_CODE + "[2]")).isEqualTo("I1");
        assertThat(cell(rows, RANKING_CODE + "[3]")).isEmpty();
    }

    @Test
    void theFieldDictionaryNamesEveryRankAndListsTheItems() {
        ExportJobView job = fixture.runToEnd(p.owner(), fixture.create(p.owner(), p, "csv"));
        List<List<String>> dictionary = ExportFixture.unzipCsv(fixture.download(p.owner(), job)).get("fields.csv");

        assertThat(dictionary).anySatisfy(row -> assertThat(row)
                .contains(RANKING_CODE + "[1]", rankingFields.get("1"), "R", "1", "排序 [第1位]"));
        assertThat(dictionary).anySatisfy(row -> assertThat(row)
                .contains(RANKING_CODE, rankingFields.get(""), "R", "排序", "I1=价格; I2=品牌; I3=口碑"));
    }

    @Test
    void theExportAsksTheGatewayForTheRankColumnsToo() {
        fixture.runToEnd(p.owner(), fixture.create(p.owner(), p, "csv"));

        List<String> requested = answers.callsFor(p.instance()).getLast().fieldnames();
        assertThat(requested).contains(rankingFields.get("1"), rankingFields.get("2"), rankingFields.get("3"));
    }
}
