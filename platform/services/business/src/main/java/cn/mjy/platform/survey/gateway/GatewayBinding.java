package cn.mjy.platform.survey.gateway;

import java.util.List;

/**
 * 网关返回的绑定记录（{@code BindingRecord.to_dict()}）：定义跑在哪个实例的哪个 sid 上、结构指纹，
 * 以及"题目 UUID → 题目代码 → 答卷列名"三段映射。
 */
public record GatewayBinding(
        String engineInstance,
        int surveyId,
        String definitionUuid,
        String compilerVersion,
        String fingerprintVersion,
        String fingerprint,
        String language,
        String publishedAt,
        List<QuestionBinding> questions) {

    public GatewayBinding {
        questions = List.copyOf(questions);
    }

    public record QuestionBinding(String uuid, String code, String type, List<FieldBinding> fields) {

        public QuestionBinding {
            fields = List.copyOf(fields);
        }
    }

    public record FieldBinding(String fieldname, String aid, int scale) {
    }
}
