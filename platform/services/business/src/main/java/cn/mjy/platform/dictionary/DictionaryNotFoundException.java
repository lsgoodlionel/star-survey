package cn.mjy.platform.dictionary;

/** 字典、版本或节点不存在。对租户来说「还没发布」与「不存在」是同一件事。 */
public class DictionaryNotFoundException extends RuntimeException {

    public DictionaryNotFoundException(String message) {
        super(message);
    }
}
