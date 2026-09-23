package cn.mjy.platform.contacts;

import cn.mjy.platform.shared.security.CurrentTenant;
import java.net.URI;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** 部门级数据范围接口。只有租户管理员能改，部门管理员无法自行扩大范围。 */
@RestController
@RequestMapping("/v1/contact-scopes")
class ContactScopeController {

    record ScopeBody(String actorId, UUID orgUnitId, Instant expiresAt) {
    }

    private final CurrentTenant currentTenant;
    private final ContactScopeService scopes;

    ContactScopeController(CurrentTenant currentTenant, ContactScopeService scopes) {
        this.currentTenant = currentTenant;
        this.scopes = scopes;
    }

    @PostMapping
    ResponseEntity<ContactScopeView> grant(@RequestBody ScopeBody body) {
        ContactScopeView granted = scopes.grant(currentTenant.require(), body.actorId(), body.orgUnitId(),
                body.expiresAt());
        return ResponseEntity.created(URI.create("/v1/contact-scopes/" + granted.id())).body(granted);
    }

    @GetMapping
    List<ContactScopeView> list() {
        return scopes.list(currentTenant.require());
    }

    @DeleteMapping("/{id}")
    ResponseEntity<Void> revoke(@PathVariable UUID id) {
        scopes.revoke(currentTenant.require(), id);
        return ResponseEntity.noContent().build();
    }
}
