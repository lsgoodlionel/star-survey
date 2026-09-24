package cn.mjy.platform.dictionary;

import static cn.mjy.platform.dictionary.DictionaryFixture.BEIJING;
import static cn.mjy.platform.dictionary.DictionaryFixture.BEIJING_CITY;
import static cn.mjy.platform.dictionary.DictionaryFixture.DONGCHENG;
import static cn.mjy.platform.dictionary.DictionaryFixture.GUANGDONG;
import static cn.mjy.platform.dictionary.DictionaryFixture.GUANGZHOU;
import static cn.mjy.platform.dictionary.DictionaryFixture.LIWAN;
import static cn.mjy.platform.dictionary.DictionaryFixture.LUOHU;
import static cn.mjy.platform.dictionary.DictionaryFixture.SHENZHEN;
import static cn.mjy.platform.dictionary.DictionaryFixture.XICHENG;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.access.AccessFixture;
import cn.mjy.platform.shared.TenantContext;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * 字典的读取面：按父节点分页、按关键字搜索、把一个节点还原成整条路径，
 * 以及<b>服务端的路径判定</b>——多级下拉的「省 / 市 / 区」到底算不算数由这里说了算。
 *
 * <p>树有数千节点，作答页一次只能取一层的一页，所以分页不是优化而是前提（ADR 0019 决定 4）。
 */
@SpringBootTest
class DictionaryCatalogTest {

    @Autowired
    private DictionaryCatalog catalog;

    @Autowired
    private DictionaryFixture fixture;

    @Autowired
    private AccessFixture access;

    private TenantContext ctx;
    private String code;

    @BeforeEach
    void aPublishedDictionary() {
        ctx = access.newTenant();
        code = fixture.newDictionaryCode();
        fixture.publishedDictionary(code, "2024.1");
    }

    // ---------------------------------------------------------------- 分页

    @Test
    void theRootsAreTheFirstLevel() {
        DictionaryPage roots = catalog.children(ctx, code, "2024.1", null, 0, 20);

        assertThat(roots.nodes()).extracting(DictionaryNodeView::code).containsExactly(BEIJING, GUANGDONG);
        assertThat(roots.total()).isEqualTo(2);
        assertThat(roots.nodes()).extracting(DictionaryNodeView::leaf).containsOnly(false);
    }

    @Test
    void childrenComeBackInTheOrderTheyWereLoaded() {
        assertThat(catalog.children(ctx, code, "2024.1", GUANGDONG, 0, 20).nodes())
                .extracting(DictionaryNodeView::code).containsExactly(GUANGZHOU, SHENZHEN);
    }

    @Test
    void aPageIsAPageAndTheTotalSaysHowMuchIsLeft() {
        DictionaryPage first = catalog.children(ctx, code, "2024.1", BEIJING_CITY, 0, 1);
        DictionaryPage second = catalog.children(ctx, code, "2024.1", BEIJING_CITY, 1, 1);

        assertThat(first.nodes()).extracting(DictionaryNodeView::code).containsExactly(DONGCHENG);
        assertThat(second.nodes()).extracting(DictionaryNodeView::code).containsExactly(XICHENG);
        assertThat(first.total()).isEqualTo(2);
    }

    @Test
    void aPageSizeBeyondTheCapIsRefusedRatherThanSilentlyTrimmed() {
        assertThatThrownBy(() -> catalog.children(ctx, code, "2024.1", null, 0, DictionaryLimits.MAX_PAGE_SIZE + 1))
                .isInstanceOf(InvalidDictionaryRequestException.class);
    }

    @Test
    void aLeafHasNoChildren() {
        assertThat(catalog.children(ctx, code, "2024.1", DONGCHENG, 0, 20).nodes()).isEmpty();
    }

    // ---------------------------------------------------------------- 搜索

    @Test
    void searchMatchesTheLabelAnywhereAndSaysWhereEachHitSits() {
        List<DictionaryNodeView> hits = catalog.search(ctx, code, "2024.1", "区", 20);

        assertThat(hits).extracting(DictionaryNodeView::code)
                .contains(DONGCHENG, XICHENG, LIWAN, LUOHU, BEIJING_CITY);
        assertThat(hits).filteredOn(node -> node.code().equals(LIWAN)).singleElement()
                .extracting(DictionaryNodeView::parentCode).isEqualTo(GUANGZHOU);
    }

    @Test
    void searchAlsoMatchesTheNodeCode() {
        assertThat(catalog.search(ctx, code, "2024.1", LUOHU, 20))
                .extracting(DictionaryNodeView::code).containsExactly(LUOHU);
    }

    @Test
    void anEmptyKeywordIsRefusedRatherThanReturningTheWholeTree() {
        assertThatThrownBy(() -> catalog.search(ctx, code, "2024.1", "   ", 20))
                .isInstanceOf(InvalidDictionaryRequestException.class);
    }

    @Test
    void searchNeverReturnsMoreThanItWasAskedFor() {
        assertThat(catalog.search(ctx, code, "2024.1", "区", 2)).hasSize(2);
    }

    // ---------------------------------------------------------------- 整条路径

    @Test
    void aNodeIsResolvedIntoItsWholePathFromTheRoot() {
        assertThat(catalog.path(ctx, code, "2024.1", LIWAN))
                .extracting(DictionaryNodeView::code).containsExactly(GUANGDONG, GUANGZHOU, LIWAN);
    }

    @Test
    void aNodeThatIsNotInThisVersionHasNoPath() {
        assertThatThrownBy(() -> catalog.path(ctx, code, "2024.1", "999999"))
                .isInstanceOf(DictionaryNotFoundException.class);
    }

    // ---------------------------------------------------------------- 服务端路径判定（篡改）

    @Test
    void aWholeGenuinePathIsAccepted() {
        assertThat(catalog.checkPath(code, "2024.1", List.of(GUANGDONG, GUANGZHOU, LIWAN)).ok()).isTrue();
    }

    @Test
    void aCityFromAnotherProvinceIsRejected() {
        DictionaryPathCheck check = catalog.checkPath(code, "2024.1", List.of(BEIJING, GUANGZHOU, LIWAN));

        assertThat(check.ok()).isFalse();
        assertThat(check.problem()).contains(GUANGZHOU);
    }

    @Test
    void aDistrictThatDoesNotExistIsRejected() {
        DictionaryPathCheck check = catalog.checkPath(code, "2024.1", List.of(GUANGDONG, GUANGZHOU, "440199"));

        assertThat(check.ok()).isFalse();
        assertThat(check.problem()).contains("440199");
    }

    @Test
    void aNodeFromAnotherVersionIsRejected() {
        List<DictionaryNodeDraft> withNewDistrict = new ArrayList<>(DictionaryFixture.divisions());
        withNewDistrict.add(new DictionaryNodeDraft("440104", GUANGZHOU, "越秀区"));
        fixture.publishAnotherVersion(code, "2025.1", withNewDistrict);

        assertThat(catalog.checkPath(code, "2025.1", List.of(GUANGDONG, GUANGZHOU, "440104")).ok()).isTrue();
        DictionaryPathCheck old = catalog.checkPath(code, "2024.1", List.of(GUANGDONG, GUANGZHOU, "440104"));
        assertThat(old.ok()).isFalse();
        assertThat(old.problem()).contains("440104");
    }

    @Test
    void aPathThatSkipsALevelIsRejected() {
        DictionaryPathCheck check = catalog.checkPath(code, "2024.1", List.of(GUANGDONG, LIWAN));

        assertThat(check.ok()).isFalse();
        assertThat(check.problem()).contains(LIWAN);
    }

    @Test
    void aPathThatDoesNotStartAtTheRootIsRejected() {
        assertThat(catalog.checkPath(code, "2024.1", List.of(GUANGZHOU, LIWAN)).ok()).isFalse();
    }

    @Test
    void anEmptyPathIsRejected() {
        assertThat(catalog.checkPath(code, "2024.1", List.of()).ok()).isFalse();
    }

    @Test
    void aPathAgainstAVersionThatWasNeverPublishedIsRejectedRatherThanThrowing() {
        assertThat(catalog.checkPath(code, "1999.1", List.of(BEIJING)).ok()).isFalse();
    }

    // ---------------------------------------------------------------- 版本可见性

    @Test
    void aDraftVersionIsInvisibleToTenantsAndCannotBeUsedForPaths() {
        String draftOnly = fixture.newDictionaryCode();
        fixture.draftWithNodes(draftOnly, "2024.1");

        assertThatThrownBy(() -> catalog.children(ctx, draftOnly, "2024.1", null, 0, 20))
                .isInstanceOf(DictionaryNotFoundException.class);
        assertThat(catalog.checkPath(draftOnly, "2024.1", List.of(BEIJING)).ok()).isFalse();
    }

    @Test
    void onlyPublishedDictionariesAreListed() {
        String draftOnly = fixture.newDictionaryCode();
        fixture.draftWithNodes(draftOnly, "2024.1");

        assertThat(catalog.list(ctx)).extracting(DictionaryView::code).contains(code).doesNotContain(draftOnly);
    }
}
