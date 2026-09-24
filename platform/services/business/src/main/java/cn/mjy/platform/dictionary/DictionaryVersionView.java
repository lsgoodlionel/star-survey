package cn.mjy.platform.dictionary;

import java.time.OffsetDateTime;

/**
 * 一版字典。{@code digest} 只在已发布时存在，由节点内容算出（{@link DictionaryDigest}），
 * 引擎侧的快照据它核对是不是同一份数据。
 */
public record DictionaryVersionView(String dictionaryCode, String version, DictionaryVersionStatus status,
        String digest, int nodeCount, OffsetDateTime publishedAt) {
}
