package cn.mjy.platform.survey;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

/**
 * 一次漂移检查的记录（只追加，V530）。outcome：
 * <ul>
 *   <li>{@value #MATCH}：引擎里的结构与发布时一致、问卷仍激活；</li>
 *   <li>{@value #DRIFT}：有人绕过平台改了引擎里的问卷，issues / renamed 说明改了什么；</li>
 *   <li>{@value #ERROR}：引擎或网关读不出来，结论未知（detail 说明原因）——绝不当作 match。</li>
 * </ul>
 */
public record DriftCheckView(
        long id,
        UUID surveyId,
        int version,
        String engineInstanceId,
        int engineSid,
        String expectedFingerprint,
        String currentFingerprint,
        String outcome,
        List<Issue> issues,
        List<Rename> renamed,
        String detail,
        String checkedBy,
        OffsetDateTime checkedAt) {

    public static final String MATCH = "match";
    public static final String DRIFT = "drift";
    public static final String ERROR = "error";

    public DriftCheckView {
        issues = List.copyOf(issues);
        renamed = List.copyOf(renamed);
    }

    public record Issue(String code, String detail) {
    }

    /** 题目（平台题目 UUID）的代码在引擎里从 from 被改成了 to。 */
    public record Rename(String questionUuid, String from, String to) {
    }
}
