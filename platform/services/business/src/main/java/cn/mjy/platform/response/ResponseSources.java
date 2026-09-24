package cn.mjy.platform.response;

import cn.mjy.platform.access.DecisionReason;
import cn.mjy.platform.response.FieldDictionary.FieldEntry;
import cn.mjy.platform.response.FieldDictionary.VersionFields;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.survey.PublishedVersionView;
import cn.mjy.platform.survey.SurveyAccessDeniedException;
import cn.mjy.platform.survey.SurveyNotFoundException;
import cn.mjy.platform.survey.SurveyService;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;
import org.springframework.stereotype.Component;

/**
 * 问卷的已发布版本 → 答卷来源（每个 (实例, sid) 一个，带版本号与字段字典）。
 * 版本只经问卷模块的公开服务方法读取（其中再判一次 view），本模块不读问卷的表。
 */
@Component
class ResponseSources {

    /**
     * 一个答卷来源：投影行按 (实例, sid) 归入它，作答按它的列名读取。
     *
     * @param extensionQuestions 该版本里有副表的题目代码；网关不存绑定，副表题由平台点名
     *                           （契约 response-read-v1「扩展表作答」）
     */
    record Source(int version, String engineInstanceId, long engineSid, List<FieldEntry> fields,
            List<String> extensionQuestions) {

        Source {
            fields = List.copyOf(fields);
            extensionQuestions = List.copyOf(extensionQuestions);
        }

        List<String> fieldnames() {
            return fields.stream().map(FieldEntry::fieldname).distinct().toList();
        }
    }

    private record EngineSurvey(String instance, long sid) {
    }

    private final SurveyService surveys;

    ResponseSources(SurveyService surveys) {
        this.surveys = surveys;
    }

    /** 每个版本一段字段字典，按版本号升序。 */
    List<VersionFields> dictionary(TenantContext ctx, UUID surveyId) {
        return versions(ctx, surveyId).stream()
                .map(v -> new VersionFields(v.version(), v.engineInstanceId(), v.engineSid(), v.language(),
                        FieldDictionaryBuilder.fields(v.definition(), v.fields())))
                .toList();
    }

    /**
     * 答卷来源，按版本号升序。多个版本指向同一 (实例, sid) 时（当前发布流程不会产生），
     * 归入版本号最大者，保证同一份答卷只出现一次（ADR 0013 决定 2）。
     */
    List<Source> sources(TenantContext ctx, UUID surveyId) {
        Map<EngineSurvey, Source> bySid = new LinkedHashMap<>();
        for (PublishedVersionView v : versions(ctx, surveyId)) {
            bySid.put(new EngineSurvey(v.engineInstanceId(), v.engineSid()),
                    new Source(v.version(), v.engineInstanceId(), v.engineSid(),
                            FieldDictionaryBuilder.fields(v.definition(), v.fields()),
                            ExtensionQuestions.codes(v.definition())));
        }
        return bySid.values().stream().sorted(Comparator.comparingInt(Source::version)).toList();
    }

    static Set<String> sensitiveFieldnames(List<Source> sources) {
        return sources.stream()
                .flatMap(s -> s.fields().stream())
                .filter(FieldEntry::sensitive)
                .map(FieldEntry::fieldname)
                .collect(Collectors.toUnmodifiableSet());
    }

    private List<PublishedVersionView> versions(TenantContext ctx, UUID surveyId) {
        try {
            return surveys.versions(ctx, surveyId);
        } catch (SurveyNotFoundException e) {
            throw new ResponseNotFoundException("survey not found: " + surveyId);
        } catch (SurveyAccessDeniedException e) {
            DecisionReason reason = e.reason();
            throw new ResponseAccessDeniedException(reason, e.getMessage());
        }
    }
}
