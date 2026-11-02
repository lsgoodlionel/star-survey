package cn.mjy.platform.platformadmin;

import jakarta.servlet.http.HttpServletRequest;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Map;
import java.util.UUID;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.dao.DataAccessException;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
class ProbeSetupController {
    private final ProbeSetupService setup;
    private final ProbeCredentialVerifier verifier;
    private final UUID installation;
    ProbeSetupController(ProbeSetupService setup, @Value("${PLATFORM_PROBE_HMAC_SECRET:}") String secret,
                         @Value("${PLATFORM_PROBE_INSTALLATION_ID:}") String installation,
                         @Value("${platform.security.jwt-hmac-secret:}") String personnelSecret,
                         @Value("${PLATFORM_ENGINE_EVENTS_SECRET:}") String engineMasterSecret) {
        this.setup = setup;
        UUID configured = null;
        try { configured = UUID.fromString(installation); if (!configured.toString().equals(installation)) configured = null; }
        catch (IllegalArgumentException ignored) { }
        byte[] machine = secret.getBytes(StandardCharsets.UTF_8);
        byte[] personnel = personnelSecret.getBytes(StandardCharsets.UTF_8);
        byte[] engineMaster = engineMasterSecret.getBytes(StandardCharsets.UTF_8);
        this.verifier = machine.length >= 32 && !MessageDigest.isEqual(machine, personnel)
                && !MessageDigest.isEqual(machine, engineMaster) && configured != null
                ? new ProbeCredentialVerifier(machine) : null;
        this.installation = configured;
    }
    @PostMapping("/v1/setup-probe")
    ResponseEntity<?> setup(HttpServletRequest request) throws IOException {
        if (verifier == null) return ResponseEntity.status(503).body(Map.of("error", "probe_not_configured"));
        String header = request.getHeader("Authorization");
        if (header == null || !header.startsWith("Bearer ")) return ResponseEntity.status(401).body(Map.of("error", "probe_credential_required"));
        var verified = verifier.verify(header.substring(7));
        if (verified.isEmpty() || !installation.equals(verified.get().installation()) || !verified.get().scopes().equals(java.util.Set.of("probe.setup")))
            return ResponseEntity.status(403).body(Map.of("error", "probe_credential_rejected"));
        if (request.getInputStream().read() != -1) return ResponseEntity.badRequest().body(Map.of("error", "probe_body_not_allowed"));
        try {
            return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(setup.setup(verified.get()));
        } catch (DataAccessException | IllegalStateException rejected) {
            return ResponseEntity.status(409).body(Map.of("error", "probe_setup_rejected"));
        }
    }
}
