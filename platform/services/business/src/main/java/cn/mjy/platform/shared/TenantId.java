package cn.mjy.platform.shared;

import java.util.Objects;
import java.util.UUID;

/** 租户标识。只能来自可信身份或平台内部，不接受客户端参数直接构造后提升权限。 */
public record TenantId(UUID value) {

    public TenantId {
        Objects.requireNonNull(value, "tenant id");
    }

    public static TenantId of(String value) {
        return new TenantId(UUID.fromString(value));
    }

    public static TenantId random() {
        return new TenantId(UUID.randomUUID());
    }

    @Override
    public String toString() {
        return value.toString();
    }
}
