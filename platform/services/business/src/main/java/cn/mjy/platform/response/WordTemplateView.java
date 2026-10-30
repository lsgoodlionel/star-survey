package cn.mjy.platform.response;

import java.time.Instant;
import java.util.UUID;

/** 自定义 Word 模板的只读元数据；内容只在创建导出任务时由服务端冻结，不经 API 回显。 */
public record WordTemplateView(UUID id, String name, int version, String sha256, long byteSize, Instant createdAt) {
}
