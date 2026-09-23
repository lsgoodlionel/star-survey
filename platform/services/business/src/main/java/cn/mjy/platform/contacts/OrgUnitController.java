package cn.mjy.platform.contacts;

import cn.mjy.platform.shared.security.CurrentTenant;
import java.net.URI;
import java.util.List;
import java.util.UUID;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** 部门树接口。移动要求新旧位置都在调用者的数据范围内。 */
@RestController
@RequestMapping("/v1/org-units")
class OrgUnitController {

    record UnitBody(UUID parentId, String name, String externalRef) {
    }

    record MoveBody(UUID parentId) {
    }

    private final CurrentTenant currentTenant;
    private final OrgUnitService units;

    OrgUnitController(CurrentTenant currentTenant, OrgUnitService units) {
        this.currentTenant = currentTenant;
        this.units = units;
    }

    @PostMapping
    ResponseEntity<OrgUnitView> create(@RequestBody UnitBody body) {
        OrgUnitView created = units.create(currentTenant.require(), body.parentId(), body.name(),
                body.externalRef());
        return ResponseEntity.created(URI.create("/v1/org-units/" + created.id())).body(created);
    }

    @GetMapping
    List<OrgUnitView> list() {
        return units.list(currentTenant.require());
    }

    @GetMapping("/{id}")
    OrgUnitView get(@PathVariable UUID id) {
        return units.get(currentTenant.require(), id);
    }

    @PatchMapping("/{id}")
    OrgUnitView rename(@PathVariable UUID id, @RequestBody UnitBody body) {
        return units.rename(currentTenant.require(), id, body.name());
    }

    @PostMapping("/{id}/move")
    OrgUnitView move(@PathVariable UUID id, @RequestBody MoveBody body) {
        return units.move(currentTenant.require(), id, body.parentId());
    }
}
