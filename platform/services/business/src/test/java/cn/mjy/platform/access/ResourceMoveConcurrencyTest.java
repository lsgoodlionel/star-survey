package cn.mjy.platform.access;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.junit.jupiter.api.RepeatedTest;
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
