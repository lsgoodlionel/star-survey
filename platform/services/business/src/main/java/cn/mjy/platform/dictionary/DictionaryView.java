package cn.mjy.platform.dictionary;

import java.time.OffsetDateTime;

/**
 * 一本字典。{@code currentVersion} 是新发布的问卷会固化到的那一版；为空表示还没有任何一版发布过。
 */
public record DictionaryView(String code, String name, int maxDepth, String currentVersion,
        OffsetDateTime createdAt) {
}
