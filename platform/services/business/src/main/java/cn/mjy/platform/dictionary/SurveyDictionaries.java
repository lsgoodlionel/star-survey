package cn.mjy.platform.dictionary;

import cn.mjy.platform.survey.InvalidDefinitionException;
import cn.mjy.platform.survey.SurveyDictionarySource;
import java.util.ArrayList;
import java.util.List;
import org.springframework.stereotype.Component;

/**
 * 字典模块这一侧的 {@link SurveyDictionarySource}：把「这份定义引用了哪几本字典」
 * 变成「这几本字典发布这一刻的那一版 ＋ 整棵树」。
 *
 * <p>固化失败（字典不存在、一版都没发布过）一律抛 {@link InvalidDefinitionException}：
 * 这两种情况重试必然同样失败，属于**确定失败**，不该让问卷卡在待核对
 * （与 ADR 0017 对「认不出的 ref」的推理一致）。
 */
@Component
class SurveyDictionaries implements SurveyDictionarySource {

    private final DictionaryRepository dictionaries;

    SurveyDictionaries(DictionaryRepository dictionaries) {
        this.dictionaries = dictionaries;
    }

    /**
     * <b>刻意不开 TenantScope。</b>字典三张表没有行级安全，读它们不需要任何租户；而这个方法是在
     * 发布事务（问卷模块的租户作用域）<b>里面</b>被调用的，{@code TenantScope.call} 用的
     * {@code SET LOCAL app.tenant_id} 是<b>事务级</b>的——在里面再切一次租户，外层事务剩下的语句
     * 就都变成了那个租户，接着写 {@code survey_publish_attempt} 会被行级安全直接拒掉。
     * 这条在第一次跑测试时就红了，留档免得日后有人"顺手补上作用域"。
     */
    @Override
    public List<Pinned> pin(List<String> dictionaryCodes) {
        List<String> problems = new ArrayList<>();
        List<Pinned> pinned = new ArrayList<>(dictionaryCodes.size());
        for (String code : dictionaryCodes) {
            pin(code, problems).ifPresent(pinned::add);
        }
        if (!problems.isEmpty()) {
            throw new InvalidDefinitionException(problems);
        }
        return pinned;
    }

    private java.util.Optional<Pinned> pin(String code, List<String> problems) {
        DictionaryView dictionary = dictionaries.findDictionary(code).orElse(null);
        if (dictionary == null) {
            problems.add("dictionary not found: " + code);
            return java.util.Optional.empty();
        }
        if (dictionary.currentVersion() == null) {
            problems.add("dictionary " + code + " has no published version to pin");
            return java.util.Optional.empty();
        }
        DictionaryVersionView version = dictionaries.findVersion(code, dictionary.currentVersion())
                .filter(found -> found.status() == DictionaryVersionStatus.PUBLISHED)
                .orElse(null);
        if (version == null) {
            problems.add("dictionary " + code + " has no published version to pin");
            return java.util.Optional.empty();
        }
        List<Node> nodes = dictionaries.nodes(code, version.version()).stream()
                .map(node -> new Node(node.code(), node.parentCode(), node.label()))
                .toList();
        return java.util.Optional.of(new Pinned(code, version.version(), version.digest(), nodes));
    }
}
