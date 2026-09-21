package cn.mjy.platform.access;

import cn.mjy.platform.shared.TenantId;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

/**
 * 每租户一把事务级咨询锁，串行化会改变席位占用或所有者人数的操作（邀请、移除、授权、撤销）。
 * 锁随事务结束自动释放；不同租户互不阻塞。须在租户作用域（事务）内调用。
 */
@Component
class TenantAccessLock {

    private static final String LOCK_NAMESPACE = "access.members:";

    private final JdbcClient jdbc;

    TenantAccessLock(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    void lock(TenantId tenant) {
        jdbc.sql("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")
                .param("key", LOCK_NAMESPACE + tenant.value())
                .query((rs, n) -> Boolean.TRUE)
                .single();
    }
}
