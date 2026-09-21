package cn.mjy.platform.tenant.api;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;

/**
 * 幂等创建的结果：{@code created} 为 false 表示同一业务键已存在，返回的是既有对象。
 * 对外分别映射为 201 与 200，客户端重试不会产生重复数据。
 */
public record CreateResult<T>(T value, boolean created) {

    public <R> ResponseEntity<R> toResponse(java.util.function.Function<T, R> view) {
        return ResponseEntity.status(created ? HttpStatus.CREATED : HttpStatus.OK).body(view.apply(value));
    }
}
