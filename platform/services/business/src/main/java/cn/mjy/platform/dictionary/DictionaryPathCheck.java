package cn.mjy.platform.dictionary;

/**
 * 一条层级路径在某一版字典里的判定结果。
 *
 * <p>{@code problem} 里只出现<b>调用方自己提交的代码</b>，不透露这一版字典还有哪些别的节点——
 * 拒绝理由不该变成枚举字典的入口。
 */
public record DictionaryPathCheck(boolean ok, String problem) {

    private static final DictionaryPathCheck PASSED = new DictionaryPathCheck(true, "");

    public static DictionaryPathCheck passed() {
        return PASSED;
    }

    public static DictionaryPathCheck failed(String problem) {
        return new DictionaryPathCheck(false, problem);
    }
}
