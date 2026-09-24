package cn.mjy.platform.dictionary;

import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import org.springframework.stereotype.Component;

/**
 * 字典测试夹具：一本小号行政区划字典，形状与真数据一致（省 / 市 / 区三层，代码是 GB/T 2260 风格）。
 *
 * <p>刻意做成跨省的两棵子树，好让「跨省的市」这类篡改用例有真东西可篡改。
 */
@Component
public class DictionaryFixture {

    public static final String OPERATOR = "ops-dictionary";

    /** 北京 → 市辖区 → 东城 / 西城。 */
    public static final String BEIJING = "110000";
    public static final String BEIJING_CITY = "110100";
    public static final String DONGCHENG = "110101";
    public static final String XICHENG = "110102";
    /** 广东 → 广州 / 深圳 → 荔湾 / 罗湖。 */
    public static final String GUANGDONG = "440000";
    public static final String GUANGZHOU = "440100";
    public static final String LIWAN = "440103";
    public static final String SHENZHEN = "440300";
    public static final String LUOHU = "440303";

    private final DictionaryService dictionaries;

    DictionaryFixture(DictionaryService dictionaries) {
        this.dictionaries = dictionaries;
    }

    /** 每个测试自己一本字典，互不干扰（表是控制平面表，没有租户隔离兜底）。 */
    public String newDictionaryCode() {
        return "t-" + UUID.randomUUID().toString().substring(0, 8);
    }

    public DictionaryView dictionary(String code) {
        return dictionaries.createDictionary(OPERATOR, trace(), code, "测试行政区划", 3);
    }

    /** 建字典 → 建草稿 → 灌节点 → 发布，返回已发布的那一版。 */
    public DictionaryVersionView publishedDictionary(String code, String version) {
        dictionary(code);
        return publishAnotherVersion(code, version, divisions());
    }

    /** 建字典 → 建草稿 → 灌节点，**不发布**：用来证明草稿对租户不可见。 */
    public void draftWithNodes(String code, String version) {
        dictionary(code);
        dictionaries.createDraft(OPERATOR, trace(), code, version);
        dictionaries.loadNodes(OPERATOR, trace(), code, version, divisions());
    }

    public DictionaryVersionView publishAnotherVersion(String code, String version, List<DictionaryNodeDraft> nodes) {
        dictionaries.createDraft(OPERATOR, trace(), code, version);
        dictionaries.loadNodes(OPERATOR, trace(), code, version, nodes);
        return dictionaries.publishVersion(OPERATOR, trace(), code, version);
    }

    /** 省 / 市 / 区三层，9 个节点。 */
    public static List<DictionaryNodeDraft> divisions() {
        List<DictionaryNodeDraft> nodes = new ArrayList<>();
        nodes.add(new DictionaryNodeDraft(BEIJING, null, "北京市"));
        nodes.add(new DictionaryNodeDraft(BEIJING_CITY, BEIJING, "市辖区"));
        nodes.add(new DictionaryNodeDraft(DONGCHENG, BEIJING_CITY, "东城区"));
        nodes.add(new DictionaryNodeDraft(XICHENG, BEIJING_CITY, "西城区"));
        nodes.add(new DictionaryNodeDraft(GUANGDONG, null, "广东省"));
        nodes.add(new DictionaryNodeDraft(GUANGZHOU, GUANGDONG, "广州市"));
        nodes.add(new DictionaryNodeDraft(LIWAN, GUANGZHOU, "荔湾区"));
        nodes.add(new DictionaryNodeDraft(SHENZHEN, GUANGDONG, "深圳市"));
        nodes.add(new DictionaryNodeDraft(LUOHU, SHENZHEN, "罗湖区"));
        return nodes;
    }

    public static String trace() {
        return UUID.randomUUID().toString();
    }
}
