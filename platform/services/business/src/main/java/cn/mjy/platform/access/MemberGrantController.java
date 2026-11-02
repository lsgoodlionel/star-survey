package cn.mjy.platform.access;

import cn.mjy.platform.shared.security.CurrentTenant;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/v1/member-grants")
public class MemberGrantController {
    private final CurrentTenant current;
    private final GrantService grants;
    private final RoleCatalogue roles;
    private final AccessDecisionService access;
    MemberGrantController(CurrentTenant current, GrantService grants, RoleCatalogue roles, AccessDecisionService access) {
        this.current = current;
        this.grants = grants;
        this.roles = roles;
        this.access = access;
    }
    @GetMapping
    public GrantService.GrantPage list(@RequestParam(required = false) UUID after,
            @RequestParam(defaultValue = "50") int limit) { return grants.page(current.require(), after, limit); }
    @GetMapping("/roles")
    public List<RoleDefinition> roles() {
        access.require(current.require(), Permission.MANAGE_MEMBERS, null);
        return roles.all();
    }
    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public Map<String, UUID> grant(@RequestBody GrantRequest request) {
        return Map.of("id", grants.grant(current.require(), request));
    }
    @PostMapping("/{id}/revoke")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void revoke(@PathVariable UUID id) { grants.revoke(current.require(), id); }
    @ExceptionHandler(IllegalStateException.class)
    ResponseEntity<Map<String, String>> conflict(IllegalStateException e) {
        return ResponseEntity.status(HttpStatus.CONFLICT).body(Map.of("error", "grant_conflict"));
    }
    @ExceptionHandler(IllegalArgumentException.class)
    ResponseEntity<Map<String, String>> invalid(IllegalArgumentException e) {
        return ResponseEntity.badRequest().body(Map.of("error", "invalid_request"));
    }
}
