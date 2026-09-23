package cn.mjy.platform.contacts;

/**
 * 一次导入：format 为 csv 或 json，content 是原文。
 * CSV 必须带表头，列名取自 {@code name, email, phone, employee_id, provider, app_id, external_id, org_unit}；
 * JSON 是同名键的对象数组。
 */
public record ImportRequest(String format, String content) {
}
