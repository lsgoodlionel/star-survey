package cn.mjy.platform.access;

import cn.mjy.platform.shared.security.CurrentTenant;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/v1/members")
public class MemberManagementController {
    private final CurrentTenant current;
    private final MemberService members;
    private final AccessDecisionService access;
    MemberManagementController(CurrentTenant current, MemberService members, AccessDecisionService access) {
        this.current = current;
        this.members = members;
        this.access = access;
    }
    @GetMapping("/capabilities")
    public Map<String, Boolean> capabilities() {
        return Map.of("canManageMembers", access.canInTenant(current.require(), Permission.MANAGE_MEMBERS).allowed());
    }
    @GetMapping
    public MemberService.MemberPage list(@RequestParam(defaultValue = "") String after,
            @RequestParam(defaultValue = "50") int limit) {
        return members.list(current.require(), after, limit);
    }
    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public void invite(@Valid @RequestBody Invite request) {
        members.invite(current.require(), request.actorId(), request.kind());
    }
    @PostMapping("/remove")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void remove(@Valid @RequestBody Remove request) {
        members.remove(current.require(), request.actorId());
    }
    @ExceptionHandler({SeatLimitExceededException.class, IllegalStateException.class})
    ResponseEntity<Map<String, String>> conflict(RuntimeException e) {
        return ResponseEntity.status(HttpStatus.CONFLICT).body(Map.of("error", e instanceof SeatLimitExceededException ? "seat_limit" : "member_conflict"));
    }
    @ExceptionHandler(IllegalArgumentException.class)
    ResponseEntity<Map<String, String>> invalid(IllegalArgumentException e) {
        return ResponseEntity.badRequest().body(Map.of("error", "invalid_request"));
    }
    public record Invite(@NotBlank @Size(max = 200) @Pattern(regexp = "[^\\s\\p{Cntrl}]+") String actorId, @NotNull MemberKind kind) { }
    public record Remove(@NotBlank @Size(max = 200) @Pattern(regexp = "[^\\s\\p{Cntrl}]+") String actorId) { }
}
