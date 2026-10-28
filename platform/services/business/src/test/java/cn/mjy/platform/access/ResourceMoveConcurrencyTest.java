package cn.mjy.platform.access;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.function.Supplier;
import org.junit.jupiter.api.RepeatedTest;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

/**
 * 两个互为交叉的移动（A 移到 B 下、B 移到 A 下）同时执行：各自单独看都合法，一起提交就成环。
 * 移动先锁住涉及的项目根行再检查祖先链，所以恰好一个成功，另一个看到新位置后被拒绝，树始终无环。
 */
@SpringBootTest
class ResourceMoveConcurrencyTest {

    private static final int MAX_TREE_DEPTH = 64;
    private static final long LOCK_WAIT_TIMEOUT_SECONDS = 10;
    private static final long LOCK_WAIT_POLL_MILLIS = 10;
    private static final String BLOCKED_ON_MY_RESOURCE_TRANSACTION = """
            SELECT COUNT(DISTINCT waiting.pid)
              FROM pg_locks waiting
              JOIN pg_stat_activity activity ON activity.pid = waiting.pid
             WHERE NOT waiting.granted
               AND waiting.pid IN (:workerPids)
               AND activity.query LIKE 'UPDATE access_resource SET archived_at%'
            """;

    @Autowired
    private AccessFixture fixture;

    @Autowired
    private ResourceTreeService tree;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    @RepeatedTest(5)
    void crossedConcurrentMovesNeverCreateACycle() throws Exception {
        TenantContext owner = fixture.newTenant();
        UUID p1 = tree.createProject(owner, "P1").id();
        UUID p2 = tree.createProject(owner, "P2").id();
        UUID a = tree.createFolder(owner, p1, "A").id();
        UUID b = tree.createFolder(owner, p2, "B").id();
        CountDownLatch start = new CountDownLatch(1);

        List<CompletableFuture<String>> results = new ArrayList<>();
        try (ExecutorService pool = Executors.newFixedThreadPool(2)) {
            results.add(CompletableFuture.supplyAsync(() -> attempt(start, owner, a, b), pool));
            results.add(CompletableFuture.supplyAsync(() -> attempt(start, owner, b, a), pool));
            start.countDown();
        }

        List<String> outcomes = results.stream().map(CompletableFuture::join).toList();
        assertThat(outcomes).containsExactlyInAnyOrder("moved", "cycle");
        assertThat(everyNodeReachesAProject(owner, List.of(a, b))).isTrue();
    }

    @Test
    void concurrentArchiveAndRestoreReturnTheCommittedStateAndAuditOnce() throws Exception {
        TenantContext owner = fixture.newTenant();
        UUID project = tree.createProject(owner, "项目").id();
        Instant originalUpdatedAt = tree.get(owner, project).updatedAt();

        List<ResourceView> archived = runTwiceBehindResourceLock(owner, project,
                () -> tree.archive(owner, project));
        ResourceView archivedCurrent = tree.get(owner, project);
        Instant archivedUpdatedAt = archivedCurrent.updatedAt();
        List<ResourceView> restored = runTwiceBehindResourceLock(owner, project,
                () -> tree.restore(owner, project));
        Instant restoredUpdatedAt = tree.get(owner, project).updatedAt();

        assertThat(archived).extracting(ResourceView::archivedAt)
                .containsOnly(archivedCurrent.archivedAt());
        assertThat(archived).extracting(ResourceView::updatedAt)
                .containsOnly(archivedUpdatedAt);
        assertThat(archivedUpdatedAt).isAfter(originalUpdatedAt);
        assertThat(restored).extracting(ResourceView::archivedAt).containsOnlyNulls();
        assertThat(restored).extracting(ResourceView::updatedAt)
                .containsOnly(restoredUpdatedAt);
        assertThat(restoredUpdatedAt).isAfter(archivedUpdatedAt);
        assertThat(auditCount(owner, AccessAudit.RESOURCE_ARCHIVE, project)).isEqualTo(1);
        assertThat(auditCount(owner, AccessAudit.RESOURCE_RESTORE, project)).isEqualTo(1);
    }

    private String attempt(CountDownLatch start, TenantContext ctx, UUID node, UUID target) {
        try {
            start.await();
            tree.move(ctx, node, target);
            return "moved";
        } catch (ResourceConflictException e) {
            return "cycle";
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return "interrupted";
        }
    }

    private List<ResourceView> runTwiceBehindResourceLock(
            TenantContext ctx, UUID resource, Supplier<ResourceView> operation) throws Exception {
        CompletableFuture<ResourceView> first = new CompletableFuture<>();
        CompletableFuture<ResourceView> second = new CompletableFuture<>();
        CompletableFuture<Integer> firstPid = new CompletableFuture<>();
        CompletableFuture<Integer> secondPid = new CompletableFuture<>();
        tenantScope.run(ctx.tenantId(), () -> {
            jdbc.sql("SELECT id FROM access_resource WHERE id = :id FOR UPDATE")
                    .param("id", resource).query(UUID.class).single();
            completeAsync(ctx, first, firstPid, operation);
            completeAsync(ctx, second, secondPid, operation);
            List<Integer> workerPids = List.of(awaitPid(firstPid), awaitPid(secondPid));
            assertThat(workerPids).doesNotHaveDuplicates();
            awaitBothBlockedOnThisTransaction(first, second, workerPids);
        });
        return List.of(first.get(20, TimeUnit.SECONDS), second.get(20, TimeUnit.SECONDS));
    }

    private void completeAsync(TenantContext ctx, CompletableFuture<ResourceView> result,
            CompletableFuture<Integer> backendPid, Supplier<ResourceView> operation) {
        Thread.ofVirtual().start(() -> {
            try {
                result.complete(tenantScope.call(ctx.tenantId(), () -> {
                    backendPid.complete(jdbc.sql("SELECT pg_backend_pid()").query(Integer.class).single());
                    return operation.get();
                }));
            } catch (RuntimeException | Error e) {
                backendPid.completeExceptionally(e);
                result.completeExceptionally(e);
            }
        });
    }

    private static int awaitPid(CompletableFuture<Integer> backendPid) {
        try {
            return backendPid.get(5, TimeUnit.SECONDS);
        } catch (Exception e) {
            throw new IllegalStateException("worker did not publish its database backend pid", e);
        }
    }

    private void awaitBothBlockedOnThisTransaction(
            CompletableFuture<?> first, CompletableFuture<?> second, List<Integer> workerPids) {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(LOCK_WAIT_TIMEOUT_SECONDS);
        try {
            while (blockedWorkerCount(workerPids) < 2) {
                assertThat(first).as("first operation must wait for the held resource row lock").isNotDone();
                assertThat(second).as("second operation must wait for the held resource row lock").isNotDone();
                assertThat(System.nanoTime()).as("both operations should reach the resource row lock")
                        .isLessThan(deadline);
                TimeUnit.MILLISECONDS.sleep(LOCK_WAIT_POLL_MILLIS);
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException(e);
        }
    }

    private long blockedWorkerCount(List<Integer> workerPids) {
        jdbc.sql("SELECT pg_stat_clear_snapshot()")
                .query((rs, row) -> rs.getObject(1)).list();
        return jdbc.sql(BLOCKED_ON_MY_RESOURCE_TRANSACTION)
                .param("workerPids", workerPids)
                .query(Long.class)
                .single();
    }

    private long auditCount(TenantContext ctx, String action, UUID resource) {
        return tenantScope.call(ctx.tenantId(), () -> jdbc.sql("""
                        SELECT COUNT(*) FROM audit_log WHERE action = :action AND resource LIKE :pattern
                        """)
                .param("action", action)
                .param("pattern", "%" + resource + "%")
                .query(Long.class)
                .single());
    }

    /** 从每个节点沿 parent_id 向上走，必须在有限步内到达项目（parent_id 为空）。 */
    private boolean everyNodeReachesAProject(TenantContext ctx, List<UUID> nodes) {
        return tenantScope.call(ctx.tenantId(), () -> nodes.stream().allMatch(node -> {
            UUID current = node;
            for (int step = 0; step < MAX_TREE_DEPTH; step++) {
                UUID parent = jdbc.sql("SELECT parent_id FROM access_resource WHERE id = :id")
                        .param("id", current).query(UUID.class).optional().orElse(null);
                if (parent == null) {
                    return true;
                }
                current = parent;
            }
            return false;
        }));
    }
}
