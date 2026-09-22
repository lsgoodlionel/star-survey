package cn.mjy.platform.access;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.security.CurrentTenant;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotNull;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 租户端资源树接口：项目、文件夹的创建，节点查询、改名与移动（问卷同样经 move 移动）。
 * 租户与操作者只取自已验签令牌；别的租户的 id 一律 404。
 */
@RestController
@RequestMapping("/v1")
public class ResourceTreeController {

    private final ResourceTreeService tree;
    private final CurrentTenant currentTenant;

    public ResourceTreeController(ResourceTreeService tree, CurrentTenant currentTenant) {
        this.tree = tree;
        this.currentTenant = currentTenant;
    }

    public record CreateProject(@NotNull String name) {
    }

    public record CreateFolder(@NotNull UUID parentId, @NotNull String name) {
    }

    public record Rename(@NotNull String name) {
    }

    public record Move(@NotNull UUID parentId) {
    }

    @PostMapping("/projects")
    public ResponseEntity<ResourceView> createProject(@Valid @RequestBody CreateProject request) {
        TenantContext ctx = currentTenant.require();
        return ResponseEntity.status(HttpStatus.CREATED).body(tree.createProject(ctx, request.name()));
    }

    @PostMapping("/folders")
    public ResponseEntity<ResourceView> createFolder(@Valid @RequestBody CreateFolder request) {
        TenantContext ctx = currentTenant.require();
        return ResponseEntity.status(HttpStatus.CREATED)
                .body(tree.createFolder(ctx, request.parentId(), request.name()));
    }

    @GetMapping("/resources")
    public ResourcePage list(@RequestParam(required = false) UUID parentId,
            @RequestParam(required = false) String cursor, @RequestParam(required = false) Integer limit) {
        return tree.list(currentTenant.require(), parentId, cursor, limit);
    }

    @GetMapping("/resources/{id}")
    public ResourceView get(@PathVariable UUID id) {
        return tree.get(currentTenant.require(), id);
    }

    @PatchMapping("/resources/{id}")
    public ResourceView rename(@PathVariable UUID id, @Valid @RequestBody Rename request) {
        return tree.rename(currentTenant.require(), id, request.name());
    }

    @PostMapping("/resources/{id}/move")
    public ResourceView move(@PathVariable UUID id, @Valid @RequestBody Move request) {
        return tree.move(currentTenant.require(), id, request.parentId());
    }
}
