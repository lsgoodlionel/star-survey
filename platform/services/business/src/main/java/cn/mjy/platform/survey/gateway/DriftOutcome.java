package cn.mjy.platform.survey.gateway;

import java.util.List;

/**
 * 漂移检查的结局：{@link Checked}（200，match 或 drift）或 {@link Unavailable}（引擎读不出来、网关拒收、
 * 超时——结论未知，绝不能当作 match）。
 */
public sealed interface DriftOutcome {

    record Checked(boolean drifted, String currentFingerprint, String active, List<Issue> issues,
            List<Rename> renamed) implements DriftOutcome {

        public Checked {
            issues = List.copyOf(issues);
            renamed = List.copyOf(renamed);
        }
    }

    record Unavailable(String reason) implements DriftOutcome {
    }

    /** 一条漂移原因，code 取值见契约 v1.2（E_CODE_DRIFT、E_SURVEY_MISSING …）。 */
    record Issue(String code, String detail) {
    }

    /** 题目 uuid 的代码在引擎里从 from 被改成了 to。 */
    record Rename(String uuid, String from, String to) {
    }
}
