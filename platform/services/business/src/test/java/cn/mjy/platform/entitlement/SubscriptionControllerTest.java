package cn.mjy.platform.entitlement;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import cn.mjy.platform.access.AccessDecisionService;
import cn.mjy.platform.access.Permission;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.security.CurrentTenant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;

class SubscriptionControllerTest {
    @Test void missingSubscriptionReturnsNotFoundAfterTenantPermissionCheck() {
        var current = mock(CurrentTenant.class);
        var access = mock(AccessDecisionService.class);
        var subscriptions = mock(SubscriptionService.class);
        var context = new TenantContext(new TenantId(UUID.randomUUID()), "owner", List.of(), "trace");
        when(current.require()).thenReturn(context);
        when(subscriptions.current(context.tenantId())).thenReturn(Optional.empty());
        var controller = new SubscriptionController(current, access, subscriptions, mock(PlanCatalog.class));
        assertThat(controller.current().getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        verify(access).require(context, Permission.MANAGE_SETTINGS, null);
    }
}
