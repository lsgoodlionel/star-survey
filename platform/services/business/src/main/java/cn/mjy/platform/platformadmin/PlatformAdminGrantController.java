package cn.mjy.platform.platformadmin;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import java.sql.SQLException;
import java.util.Map;
import java.util.UUID;
import cn.mjy.platform.tenant.PlatformOperatorGuard;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.dao.DataAccessException;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

@RestController
class PlatformAdminGrantController {
    private final PlatformAdminGrantService grants;
    private final PlatformOperatorGuard operator;
    PlatformAdminGrantController(PlatformAdminGrantService grants, PlatformOperatorGuard operator) { this.grants = grants; this.operator = operator; }

    @GetMapping("/v1/platform-admin/capabilities")
    Map<String, Boolean> capabilities() {
        try { operator.requireOperator(); return Map.of("managePlatform", true); }
        catch (AccessDeniedException rejected) { return Map.of("managePlatform", false); }
    }

    @PostMapping("/v1/platform-admin/grants/change")
    Map<String, Boolean> change(@Valid @RequestBody Change request) {
        return Map.of("changed", grants.change(request.targetTenant(), request.targetSubject(), request.grant(), request.reason()));
    }

    @ExceptionHandler(DataAccessException.class)
    ResponseEntity<Map<String, String>> databaseFailure(DataAccessException error) {
        Throwable cause = error.getMostSpecificCause();
        if (cause instanceof SQLException sql && "42501".equals(sql.getSQLState()))
            return ResponseEntity.status(403).body(Map.of("error", "access_denied"));
        if (cause instanceof SQLException sql && "P0001".equals(sql.getSQLState()))
            return ResponseEntity.status(409).body(Map.of("error", "grant_change_rejected"));
        return ResponseEntity.internalServerError().body(Map.of("error", "grant_change_failed"));
    }

    record Change(@NotNull UUID targetTenant, @NotNull UUID targetSubject, @NotNull Boolean grant,
                  @NotBlank @Size(max = 1000) String reason) { }
}
