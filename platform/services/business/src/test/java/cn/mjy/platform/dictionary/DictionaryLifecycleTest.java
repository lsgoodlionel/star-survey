package cn.mjy.platform.dictionary;

import static cn.mjy.platform.dictionary.DictionaryFixture.BEIJING;
import static cn.mjy.platform.dictionary.DictionaryFixture.BEIJING_CITY;
import static cn.mjy.platform.dictionary.DictionaryFixture.DONGCHENG;
import static cn.mjy.platform.dictionary.DictionaryFixture.OPERATOR;
import static cn.mjy.platform.dictionary.DictionaryFixture.divisions;
import static cn.mjy.platform.dictionary.DictionaryFixture.trace;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * 字典版本的生命周期：草稿可改、发布即冻结、摘要由内容决定。
 *
 * <p>这一层要钉死的核心不变式：<b>已发布的版本永远不变</b>。已发布问卷引用的就是这一版，
 * 它一变，半年前那份答卷里的路径就会变成非法路径（ADR 0019 决定 2）。
 */
@SpringBootTest
class DictionaryLifecycleTest {

    @Autowired
    private DictionaryService dictionaries;

    @Autowired
    private DictionaryFixture fixture;

    private String code;

    @BeforeEach
    void aFreshDictionaryCode() {
        code = fixture.newDictionaryCode();
    }

    @Test
    void aDraftBecomesAPublishedVersionWithADigestAndANodeCount() {
        fixture.dictionary(code);
        DictionaryVersionView draft = dictionaries.createDraft(OPERATOR, trace(), code, "2024.1");
        assertThat(draft.status()).isEqualTo(DictionaryVersionStatus.DRAFT);
        assertThat(draft.digest()).isNull();

        dictionaries.loadNodes(OPERATOR, trace(), code, "2024.1", divisions());
        DictionaryVersionView published = dictionaries.publishVersion(OPERATOR, trace(), code, "2024.1");

        assertThat(published.status()).isEqualTo(DictionaryVersionStatus.PUBLISHED);
        assertThat(published.nodeCount()).isEqualTo(9);
        assertThat(published.digest()).matches("dg1:[0-9a-f]{16}");
        assertThat(published.publishedAt()).isNotNull();
        assertThat(dictionaries.get(code).currentVersion()).isEqualTo("2024.1");
    }

    @Test
    void aPublishedVersionCanNoLongerBeLoadedOrRepublished() {
        fixture.publishedDictionary(code, "2024.1");

        assertThatThrownBy(() -> dictionaries.loadNodes(OPERATOR, trace(), code, "2024.1", divisions()))
                .isInstanceOf(DictionaryConflictException.class)
                .hasMessageContaining("2024.1");
        assertThatThrownBy(() -> dictionaries.publishVersion(OPERATOR, trace(), code, "2024.1"))
                .isInstanceOf(DictionaryConflictException.class);
    }

    @Test
    void theDigestIsDecidedByTheContentNotByTheVersionName() {
        fixture.publishedDictionary(code, "2024.1");
        String other = fixture.newDictionaryCode();
        fixture.publishedDictionary(other, "2025.1");

        assertThat(dictionaries.version(code, "2024.1").digest())
                .isEqualTo(dictionaries.version(other, "2025.1").digest());
    }

    @Test
    void renamingOneNodeChangesTheDigest() {
        fixture.publishedDictionary(code, "2024.1");
        List<DictionaryNodeDraft> renamed = new ArrayList<>(divisions());
        renamed.set(2, new DictionaryNodeDraft(DONGCHENG, BEIJING_CITY, "东城区（改名）"));
        fixture.publishAnotherVersion(code, "2024.2", renamed);

        assertThat(dictionaries.version(code, "2024.2").digest())
                .isNotEqualTo(dictionaries.version(code, "2024.1").digest());
        // 新版发布之后，当前版本跟着走；旧版仍在，仍可读。
        assertThat(dictionaries.get(code).currentVersion()).isEqualTo("2024.2");
        assertThat(dictionaries.version(code, "2024.1").status()).isEqualTo(DictionaryVersionStatus.PUBLISHED);
    }

    @Test
    void loadingTheNodesTwiceReplacesTheDraftRatherThanAppending() {
        fixture.dictionary(code);
        dictionaries.createDraft(OPERATOR, trace(), code, "2024.1");
        dictionaries.loadNodes(OPERATOR, trace(), code, "2024.1", divisions());

        int loaded = dictionaries.loadNodes(OPERATOR, trace(), code, "2024.1",
                List.of(new DictionaryNodeDraft(BEIJING, null, "北京市")));

        assertThat(loaded).isEqualTo(1);
        assertThat(dictionaries.publishVersion(OPERATOR, trace(), code, "2024.1").nodeCount()).isEqualTo(1);
    }

    /**
     * 树的形状在<b>导入</b>时就判，不拖到发布：一次导入就是一整棵树，判得出来就该当场判，
     * 让人灌完几千个节点再说「其中一个是孤儿」是白费一次。发布时会再推一遍作为最后一道闸门。
     */
    @Test
    void anOrphanNodeIsRefusedWhenItIsLoaded() {
        fixture.dictionary(code);
        dictionaries.createDraft(OPERATOR, trace(), code, "2024.1");

        assertThatThrownBy(() -> dictionaries.loadNodes(OPERATOR, trace(), code, "2024.1",
                List.of(new DictionaryNodeDraft(DONGCHENG, "999999", "\u4e1c\u57ce\u533a"))))
                .isInstanceOf(InvalidDictionaryRequestException.class)
                .hasMessageContaining("999999");
    }

    @Test
    void aTreeDeeperThanTheDictionaryAllowsIsRefused() {
        fixture.dictionary(code);
        dictionaries.createDraft(OPERATOR, trace(), code, "2024.1");
        List<DictionaryNodeDraft> tooDeep = new ArrayList<>(divisions());
        tooDeep.add(new DictionaryNodeDraft("11010101", DONGCHENG, "\u67d0\u8857\u9053"));

        assertThatThrownBy(() -> dictionaries.loadNodes(OPERATOR, trace(), code, "2024.1", tooDeep))
                .isInstanceOf(InvalidDictionaryRequestException.class)
                .hasMessageContaining("11010101");
    }

    @Test
    void aCycleIsRefusedBecauseNeitherNodeCanEverGetADepth() {
        fixture.dictionary(code);
        dictionaries.createDraft(OPERATOR, trace(), code, "2024.1");

        assertThatThrownBy(() -> dictionaries.loadNodes(OPERATOR, trace(), code, "2024.1", List.of(
                new DictionaryNodeDraft("A1", "A2", "\u7532"),
                new DictionaryNodeDraft("A2", "A1", "\u4e59"))))
                .isInstanceOf(InvalidDictionaryRequestException.class);
    }

    @Test
    void anEmptyVersionCannotBePublished() {
        fixture.dictionary(code);
        dictionaries.createDraft(OPERATOR, trace(), code, "2024.1");

        assertThatThrownBy(() -> dictionaries.publishVersion(OPERATOR, trace(), code, "2024.1"))
                .isInstanceOf(InvalidDictionaryRequestException.class);
    }

    @Test
    void duplicateNodeCodesAreRefusedWhenTheyAreLoaded() {
        fixture.dictionary(code);
        dictionaries.createDraft(OPERATOR, trace(), code, "2024.1");

        assertThatThrownBy(() -> dictionaries.loadNodes(OPERATOR, trace(), code, "2024.1", List.of(
                new DictionaryNodeDraft(BEIJING, null, "北京市"),
                new DictionaryNodeDraft(BEIJING, null, "又一个北京市"))))
                .isInstanceOf(InvalidDictionaryRequestException.class)
                .hasMessageContaining(BEIJING);
    }

    @Test
    void aVersionNameOutsideTheStructureVersionCharsetIsRefused() {
        fixture.dictionary(code);

        assertThatThrownBy(() -> dictionaries.createDraft(OPERATOR, trace(), code, "2024 年版"))
                .isInstanceOf(InvalidDictionaryRequestException.class);
    }

    @Test
    void aSecondDraftOfTheSameVersionNameIsAConflict() {
        fixture.dictionary(code);
        dictionaries.createDraft(OPERATOR, trace(), code, "2024.1");

        assertThatThrownBy(() -> dictionaries.createDraft(OPERATOR, trace(), code, "2024.1"))
                .isInstanceOf(DictionaryConflictException.class);
    }

    @Test
    void publishingComputesTheLeafFlagFromTheTreeNotFromTheImport() {
        fixture.publishedDictionary(code, "2024.1");

        assertThat(dictionaries.nodes(code, "2024.1")).filteredOn(DictionaryNodeView::leaf)
                .extracting(DictionaryNodeView::code)
                .containsExactlyInAnyOrder(DictionaryFixture.DONGCHENG, DictionaryFixture.XICHENG,
                        DictionaryFixture.LIWAN, DictionaryFixture.LUOHU);
    }
}
