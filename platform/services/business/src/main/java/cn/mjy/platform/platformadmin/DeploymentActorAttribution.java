package cn.mjy.platform.platformadmin;

import java.util.Set;

/** Draft for a controlled deployment tool; a distinct database login still needs external ownership verification. */
record DeploymentActorAttribution(String databaseLogin, String reason) {
    private static final Set<String> SHARED = Set.of("platform_owner", "platform_app", "postgres");
    static DeploymentActorAttribution fromDatabaseSession(String expected, String sessionUser, String reason) {
        if (expected == null || sessionUser == null || !sessionUser.matches("[a-z_][a-z0-9_]{0,62}")
                || !expected.equals(sessionUser) || SHARED.contains(sessionUser)
                || reason == null || reason.isBlank() || reason.length() > 1000) {
            throw new IllegalArgumentException("distinct database login and reason required");
        }
        return new DeploymentActorAttribution(sessionUser, reason);
    }
}
