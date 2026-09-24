package cn.mjy.platform.dictionary;

/**
 * 一个节点。{@code parentCode} 为空即根节点（{@code depth == 1}）；
 * {@code leaf} 由发布时的实际父子关系算出，不由导入方声明。
 */
public record DictionaryNodeView(String code, String parentCode, int depth, String label, boolean leaf) {
}
