package cn.mjy.platform.dictionary;

/**
 * 导入一个节点时要提供的三样东西。深度、是否叶子、排序都由平台算，导入方说了不算——
 * 那三样要是可以声明，一份自相矛盾的导入就会变成一棵自相矛盾的树。
 */
public record DictionaryNodeDraft(String code, String parentCode, String label) {
}
