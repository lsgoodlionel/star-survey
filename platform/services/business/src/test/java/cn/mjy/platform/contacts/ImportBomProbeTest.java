package cn.mjy.platform.contacts;

import static org.assertj.core.api.Assertions.assertThat;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;

class ImportBomProbeTest {
    @Test
    void plainCsvAndBomEmailOnlyPreserveValues() {
        var parser = new ImportRowParser(JsonMapper.builder().build());
        assertThat(parser.toColumns(parser.parse("csv", "name,email\nSynthetic Person,person@example.invalid\n").getFirst()))
                .containsEntry("name", "Synthetic Person").containsEntry("email", "person@example.invalid");
        assertThat(parser.toColumns(parser.parse("csv", "\ufeffemail\nperson@example.invalid\n").getFirst()))
                .containsEntry("email", "person@example.invalid");
    }

    @Test
    void csvUtf8BomPreservesFirstHeader() {
        var parser = new ImportRowParser(JsonMapper.builder().build());
        var rows = parser.parse("csv", "\ufeffname,email\nSynthetic Person,person@example.invalid\n");
        assertThat(rows).hasSize(1);
        assertThat(parser.toColumns(rows.getFirst()))
                .containsEntry("name", "Synthetic Person")
                .containsEntry("email", "person@example.invalid");
    }

    @Test
    void jsonUtf8BomIsAcceptedAsValidJson() {
        var parser = new ImportRowParser(JsonMapper.builder().build());
        var rows = parser.parse("json", "﻿[{\"name\":\"Synthetic Person\",\"email\":\"person@example.invalid\"}]");
        assertThat(rows).hasSize(1);
        assertThat(parser.toColumns(rows.getFirst()))
                .containsEntry("name", "Synthetic Person")
                .containsEntry("email", "person@example.invalid");
    }
}
