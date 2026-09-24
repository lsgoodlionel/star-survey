package cn.mjy.platform.survey;

import java.util.List;

/**
 * 「这份定义引用的字典，发布时该固化成哪一版」这条跨模块接缝（ADR 0019 决定 2）。
 *
 * <p>为什么需要它：问卷模块只管发布，不认识字典；字典模块管着版本与节点，却不参与发布。
 * 这个接口把两件事接起来——发布前把每一本被引用的字典<b>固化</b>成当时的当前版本，
 * 连同节点快照一起写进定义。实现在 {@code cn.mjy.platform.dictionary}，问卷模块只依赖本接口。
 *
 * <p>没有注册实现时（字典模块未装）等同「一本字典都没有」：引用了字典的问卷因此发不出去，
 * 而不是发出一份服务端校验不了路径的问卷。
 */
public interface SurveyDictionarySource {

    /** 快照里的一个节点。顺序即下拉框里的先后。 */
    record Node(String code, String parentCode, String label) {
    }

    /** 一本被固化下来的字典：版本、内容摘要、整棵树。 */
    record Pinned(String code, String version, String digest, List<Node> nodes) {

        public Pinned {
            nodes = List.copyOf(nodes);
        }
    }

    /**
     * 按当前已发布版本固化这几本字典，顺序与传入一致。
     *
     * @throws InvalidDefinitionException 有字典不存在、或一版都还没发布过——这两种情况重试必然同样失败，
     *     发布应当确定失败而不是待核对
     */
    List<Pinned> pin(List<String> dictionaryCodes);
}
