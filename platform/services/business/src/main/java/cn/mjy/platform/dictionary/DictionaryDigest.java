package cn.mjy.platform.dictionary;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.List;

/**
 * 一版字典的内容摘要。它回答的是「引擎上那份快照，和平台发布的这一版，是不是同一份数据」。
 *
 * <p>与副表的 {@code structureDigest} 刻意不同的一点：<b>标签进摘要</b>。
 * 结构摘要管的是「单元格还能不能按这一版列定义读回来」，改标签不影响读回，所以不算；
 * 字典摘要管的是「快照有没有被换过」，一个区改了名就是另一份数据，必须算。
 *
 * <p>摘要与版本名无关：同样的节点、不同的版本名，摘要相同。版本名是引用用的标识，
 * 摘要是内容的指纹，两者各管各的。
 */
final class DictionaryDigest {

    /** 算法版本。换算法时一并换掉它，免得新旧摘要看起来像同一种东西。 */
    static final String VERSION = "dg1";

    private DictionaryDigest() {
    }

    /** 按存储顺序（即下拉框里的先后）逐行算，所以调整顺序也会换摘要——顺序是作答者看得见的内容。 */
    static String of(List<DictionaryNodeView> nodes) {
        StringBuilder canonical = new StringBuilder();
        for (DictionaryNodeView node : nodes) {
            canonical.append(node.code()).append('|')
                    .append(node.parentCode() == null ? "" : node.parentCode()).append('|')
                    .append(node.depth()).append('|')
                    .append(node.label()).append('\n');
        }
        byte[] hash = sha256(canonical.toString().getBytes(StandardCharsets.UTF_8));
        return VERSION + ":" + HexFormat.of().formatHex(hash, 0, 8);
    }

    private static byte[] sha256(byte[] input) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(input);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 is required by the JRE", e);
        }
    }
}
