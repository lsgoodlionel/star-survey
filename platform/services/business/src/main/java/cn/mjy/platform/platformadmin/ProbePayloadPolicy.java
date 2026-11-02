package cn.mjy.platform.platformadmin;

import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/** Isolated payload checks; caller must independently verify credential and resource ownership. */
final class ProbePayloadPolicy {
    private static final Set<String> CAPABILITIES = Set.of("survey.read", "survey.write", "response.collect", "response.export");
    private final String code;
    private final UUID boundPlan;
    private final String boundEngine;

    ProbePayloadPolicy(UUID probe, UUID boundPlan, String boundEngine) {
        this.code = "production-probe-" + probe.toString().replace("-", "").substring(0, 12);
        this.boundPlan = boundPlan;
        this.boundEngine = boundEngine;
    }

    boolean allows(String scope, Map<String, Object> body) {
        if (scope == null || body == null) return false;
        return switch (scope) {
            case "probe.tenant.create" -> body.equals(Map.of("code", code, "name", "Production probe"));
            case "probe.plan.create" -> plan(body);
            case "probe.onboard" -> boundPlan != null && body.equals(Map.of("planVersionId", boundPlan.toString(),
                    "kind", "TRIAL", "days", 3650, "ownerActorId", "production-probe-owner"));
            case "probe.tenant.activate" -> body.equals(Map.of("status", "active"));
            case "probe.engine.register" -> boundEngine != null && body.equals(Map.of("id", boundEngine, "baseUrl", "http://engine"));
            case "probe.engine.secret.read" -> body.isEmpty();
            default -> false;
        };
    }

    private boolean plan(Map<String, Object> body) {
        if (!body.keySet().equals(Set.of("planCode", "capabilities", "quotas", "exportWindowDays"))
                || !code.equals(body.get("planCode")) || !Integer.valueOf(1).equals(body.get("exportWindowDays"))
                || !Map.of("member.seats", 1, "response.valid_completed", 100).equals(body.get("quotas"))
                || !(body.get("capabilities") instanceof List<?> capabilities)) return false;
        return capabilities.size() == CAPABILITIES.size() && capabilities.containsAll(CAPABILITIES);
    }
}
