package cn.mjy.platform.platformadmin;

/** Route check only; creation payloads and resource ownership need separate server-side validation. */
final class ProbeRequestScope {
    private ProbeRequestScope() { }
    static boolean allows(ProbeCredentialVerifier.Credential credential, String method, String path) {
        if (credential == null || !"POST".equals(method) || path == null) return false;
        var scopes = credential.scopes();
        var boundTenant = credential.tenant();
        var boundEngine = credential.engine();
        if (path.equals("/v1/platform/tenants")) return scopes.contains("probe.tenant.create");
        if (path.equals("/v1/platform/plans")) return scopes.contains("probe.plan.create");
        if (boundTenant != null) {
            String prefix = "/v1/platform/tenants/" + boundTenant;
            if (path.equals(prefix + "/onboarding")) return scopes.contains("probe.onboard");
            if (path.equals(prefix + "/status")) return scopes.contains("probe.tenant.activate");
            if (path.equals(prefix + "/engine-instances")) return scopes.contains("probe.engine.register");
        }
        return boundEngine != null && boundEngine.matches("[A-Za-z0-9_-]{1,64}")
                && path.equals("/v1/platform/engine-instances/" + boundEngine + "/event-secret")
                && scopes.contains("probe.engine.secret.read");
    }
}
