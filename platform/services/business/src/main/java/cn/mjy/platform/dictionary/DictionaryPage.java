package cn.mjy.platform.dictionary;

import java.util.List;

/**
 * 一层的一页。{@code total} 是这一层的节点总数，作答页据此知道还有没有下一页。
 */
public record DictionaryPage(List<DictionaryNodeView> nodes, int total) {

    public DictionaryPage {
        nodes = List.copyOf(nodes);
    }
}
