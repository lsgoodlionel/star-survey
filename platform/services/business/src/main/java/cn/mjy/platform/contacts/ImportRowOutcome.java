package cn.mjy.platform.contacts;

import java.util.UUID;

/**
 * 某一行的判定结果。outcome ∈ {created, updated, duplicate, invalid}；
 * reason 是错误码或去重原因码；<b>不含任何行内容</b>，避免个人信息外泄。
 */
public record ImportRowOutcome(int rowNo, String outcome, String reason, UUID contactId) {
}
