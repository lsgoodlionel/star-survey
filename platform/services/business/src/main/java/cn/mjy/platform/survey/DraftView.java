package cn.mjy.platform.survey;

import java.util.UUID;
import tools.jackson.databind.JsonNode;

/** 草稿：version 是乐观锁版本，保存时须作为 expectedVersion 带回。 */
public record DraftView(UUID surveyId, int version, JsonNode definition) {
}
