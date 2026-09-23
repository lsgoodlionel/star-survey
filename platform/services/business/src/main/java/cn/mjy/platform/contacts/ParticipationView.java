package cn.mjy.platform.contacts;

import java.time.OffsetDateTime;
import java.util.UUID;

/** 联系人 ↔ 引擎参与者令牌的映射（ADR 0016 邀请码 / 引擎 tokens_&lt;sid&gt;）。令牌是凭据，不得进日志。 */
public record ParticipationView(UUID id, UUID contactId, UUID surveyId, int versionNo,
        String engineInstanceId, int engineSid, String participantToken, OffsetDateTime issuedAt) {
}
