package cn.mjy.platform.dictionary;

import java.util.regex.Pattern;

/** 字典的硬上限与字符集。每个数字都要能说出为什么是这个数。 */
public final class DictionaryLimits {

    /**
     * 一版字典最多多少个节点。
     *
     * <p>定死在这里是因为快照要随定义下发到引擎（ADR 0019 决定 3），而平台的定义快照上限是 1 MiB。
     * 紧凑三元组下每个节点约 40 字节，8000 个节点约 320 KiB，给同一份问卷里别的题目留足余量。
     * 全国行政区划到区县约 3200 个节点，在这条线以内。
     */
    public static final int MAX_NODES = 8000;

    /** 一次读多少个节点。作答页一层一页，200 已经远超任何一层的实际宽度（广东 21 个地级市）。 */
    public static final int MAX_PAGE_SIZE = 200;

    /** 搜索一次最多返回多少条：搜索是给人用的，不是给人把整本字典导出来用的。 */
    public static final int MAX_SEARCH_HITS = 50;

    /** 关键字长度上限，免得把一整段文本当成模式去扫一版字典。 */
    public static final int MAX_KEYWORD = 64;

    /** 树最深几层，与迁移里的 CHECK 一致。 */
    public static final int MAX_DEPTH = 8;

    public static final int MAX_NAME = 200;
    public static final int MAX_LABEL = 200;

    /** 字典代码：小写、连字符，进 URL 路径。 */
    public static final Pattern CODE = Pattern.compile("^[a-z][a-z0-9-]{1,63}$");

    /**
     * 版本名：与副表契约的 {@code structureVersion} 同一字符集
     * （{@code platform/contracts/question-extension-tables-v1.md} 二），
     * 因为它会被原样写进题目属性带到引擎，不合这个形状的值插件一律压成「不知道是哪一版」。
     */
    public static final Pattern VERSION = Pattern.compile("^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$");

    /** 节点代码：与副表列的取值代码同一字符集（可以数字打头，GB/T 2260 就是数字）。 */
    public static final Pattern NODE_CODE = Pattern.compile("^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$");

    private DictionaryLimits() {
    }
}
