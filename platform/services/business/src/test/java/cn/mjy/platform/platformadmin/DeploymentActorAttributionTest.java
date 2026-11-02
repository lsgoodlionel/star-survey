package cn.mjy.platform.platformadmin;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import org.junit.jupiter.api.Test;

class DeploymentActorAttributionTest {
    @Test void recordsOnlyTheExpectedDistinctDatabaseLoginAndAnExplicitReason() {
        var actor = DeploymentActorAttribution.fromDatabaseSession("deploy_alice", "deploy_alice", "approved recovery");
        assertThat(actor.databaseLogin()).isEqualTo("deploy_alice");
        assertThat(actor.reason()).isEqualTo("approved recovery");
        assertThatThrownBy(() -> DeploymentActorAttribution.fromDatabaseSession("deploy_alice", "deploy_bob", "approved"))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> DeploymentActorAttribution.fromDatabaseSession("platform_owner", "platform_owner", "approved"))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> DeploymentActorAttribution.fromDatabaseSession("deploy_alice", "deploy_alice", "  "))
                .isInstanceOf(IllegalArgumentException.class);
    }
}
