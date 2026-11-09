package cn.mjy.platform.delivery;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertAll;

import cn.mjy.platform.engine.EngineEventType;
import cn.mjy.platform.engine.ResponseProjectionQuery.Position;
import cn.mjy.platform.response.RespondentSource;
import cn.mjy.platform.response.ResponseAnswersUnavailableException;
import cn.mjy.platform.response.ResponseFixture;
import cn.mjy.platform.response.ResponseFixture.Published;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.shared.tenant.TenantDirectory;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.context.annotation.Primary;
import org.springframework.test.context.TestPropertySource;

/**
 * Database integration: actual resolver, reconciler and worker, with synthetic engine events,
 * a respondent source that fails once, and an in-memory email provider. No real engine or mail server.
 */
@DeliveryIntegrationTest
@Import(DeliveryCompletionRecoveryTest.RecoveryConfiguration.class)
@TestPropertySource(properties = {
        "platform.delivery.enabled=false",
        "platform.delivery.email.enabled=false",
        "platform.pubgw.url=",
        "platform.pubgw.secret="
})
class DeliveryCompletionRecoveryTest {

    private static final long RESPONSE_ID = 77L;
    private static final Duration DELAY = Duration.ofHours(24);

    @Autowired private DeliveryFixture fixture;
    @Autowired private DeliveryWorker worker;
    @Autowired private ReminderService reminders;
    @Autowired private CompletionService completions;
    @Autowired private CompletionCursorRepository cursors;
    @Autowired private TenantScope tenantScope;
    @Autowired private TenantDirectory tenants;
    @Autowired private FakeEmailProvider email;
    @Autowired private DeliveryProviders providers;
    @Autowired private RecoveringRespondentSource source;

    @BeforeEach
    void resetSyntheticSource() {
        source.reset();
    }

    @Test
    void aTransientIdentityReadFailureCannotSkipACompletionOrSendAReminder() {
        // Fail closed if this test context ever registers a real email adapter.
        assertThat(providers.require(DeliveryChannel.EMAIL)).isSameAs(email);
        email.reset();
        Published p = fixture.publishedSurvey();
        TaskView invite = fixture.emailTask(p, 1);
        fixture.run(p.tenant(), invite.id());
        assertThat(email.delivered()).hasSize(1);
        email.reset();
        String address = fixture.addressOf(p.tenant(), invite.id(), 1);
        String key = fixture.respondentKeyOf(p.tenant(), invite.id(), 1);
        reminders.create(p.owner(), p.surveyId(), new ReminderService.NewRule(invite.id(),
                DELAY, DELAY, 3, "synthetic reminder", DeliveryFixture.REMINDER_BODY));
        fixture.ageSentAt(p.tenant(), invite.id(), DELAY.plusHours(1));
        fixture.responses().response(p.tenant(), p.instance(), p.sid(), ResponseFixture.GENERATION,
                RESPONSE_ID, EngineEventType.COMPLETED);
        source.failOnceThenResolve(p, key);

        worker.runOnce();

        Optional<Position> cursorAfterFailure = tenantScope.call(p.tenant(), () -> cursors.find(p.surveyId()));
        List<UUID> remindersAfterFailure = fixture.reminderTasks(p.tenant(), invite.id());
        Optional<FakeDeliveryProvider.Delivered> emailAfterFailure = email.deliveredTo(address);
        boolean completedAfterFailure = completions.isCompleted(p.owner(), p.surveyId(), key);

        // The same completion must be read again after recovery, rather than lost behind the cursor.
        worker.runOnce();

        assertAll("temporary respondent-read failure must be retryable and must stop reminders",
                () -> assertThat(cursorAfterFailure).as("cursor after failed identity lookup").isEmpty(),
                () -> assertThat(remindersAfterFailure).as("reminders generated during outage").isEmpty(),
                () -> assertThat(emailAfterFailure).as("reminder delivered during outage").isEmpty(),
                () -> assertThat(completedAfterFailure).as("no fabricated completion during outage").isFalse(),
                () -> assertThat(source.requests).as("same completed response retried after recovery")
                        .containsExactly(List.of(RESPONSE_ID), List.of(RESPONSE_ID)),
                () -> assertThat(completions.isCompleted(p.owner(), p.surveyId(), key))
                        .as("completion recorded after gateway recovery").isTrue(),
                () -> assertThat(tenantScope.call(p.tenant(), () -> cursors.find(p.surveyId())))
                        .as("cursor advances only after successful reconciliation")
                        .contains(new Position(ResponseFixture.GENERATION, RESPONSE_ID)),
                // ReminderService may keep an already-completed, zero-recipient task for its audit trail.
                () -> assertThat(fixture.reminderTasks(p.tenant(), invite.id()))
                        .as("completed respondent is never queued for a reminder")
                        .allSatisfy(taskId -> {
                            assertThat(fixture.recipientViews(p.owner(), taskId)).isEmpty();
                            TaskView task = fixture.status(p.owner(), taskId);
                            assertThat(task.recipientCount()).isZero();
                            assertThat(task.sentCount()).isZero();
                            assertThat(task.status()).isEqualTo("completed");
                        }),
                () -> assertThat(email.deliveredTo(address))
                        .as("completed respondent never receives a reminder").isEmpty());
    }

    @Test
    void aSuccessfullyReadAnonymousResponseAdvancesTheCursorWithoutInventingACompletion() {
        Published p = fixture.publishedSurvey();
        TaskView invite = dueInvitation(p);
        String key = fixture.respondentKeyOf(p.tenant(), invite.id(), 1);
        String address = fixture.addressOf(p.tenant(), invite.id(), 1);
        fixture.responses().response(p.tenant(), p.instance(), p.sid(), ResponseFixture.GENERATION,
                RESPONSE_ID, EngineEventType.COMPLETED);
        source.resolveAnonymously(p);

        worker.runOnce();
        worker.runOnce();

        assertThat(source.requests).containsExactly(List.of(RESPONSE_ID));
        assertThat(tenantScope.call(p.tenant(), () -> cursors.find(p.surveyId())))
                .contains(new Position(ResponseFixture.GENERATION, RESPONSE_ID));
        assertThat(completions.isCompleted(p.owner(), p.surveyId(), key)).isFalse();
        assertThat(fixture.reminderTasks(p.tenant(), invite.id())).hasSize(1);
        assertThat(email.deliveredTo(address)).isPresent();
    }

    @Test
    void noCompletedResponsesNeedsNoIdentityReadAndStillAllowsReminders() {
        Published p = fixture.publishedSurvey();
        TaskView invite = dueInvitation(p);
        String address = fixture.addressOf(p.tenant(), invite.id(), 1);
        source.failOnceThenResolve(p, fixture.respondentKeyOf(p.tenant(), invite.id(), 1));

        worker.runOnce();

        assertThat(source.requests).isEmpty();
        assertThat(tenantScope.call(p.tenant(), () -> cursors.find(p.surveyId()))).isEmpty();
        assertThat(fixture.reminderTasks(p.tenant(), invite.id())).hasSize(1);
        assertThat(email.deliveredTo(address)).isPresent();
    }

    @Test
    void anIdentityFailureDoesNotBlockTheNextTenant() {
        Published unavailable = fixture.publishedSurvey();
        TaskView blockedInvite = dueInvitation(unavailable);
        String blockedAddress = fixture.addressOf(unavailable.tenant(), blockedInvite.id(), 1);
        fixture.responses().response(unavailable.tenant(), unavailable.instance(), unavailable.sid(),
                ResponseFixture.GENERATION, RESPONSE_ID, EngineEventType.COMPLETED);
        source.failOnceThenResolve(unavailable,
                fixture.respondentKeyOf(unavailable.tenant(), blockedInvite.id(), 1));
        Published healthy = fixture.publishedSurvey();
        TaskView healthyInvite = dueInvitation(healthy);
        String healthyAddress = fixture.addressOf(healthy.tenant(), healthyInvite.id(), 1);
        // Ensure the successful tenant is visited after the failure, not before it.
        assertThat(tenants.openTenants()).containsSubsequence(unavailable.tenant(), healthy.tenant());

        worker.runOnce();

        assertThat(source.requests).containsExactly(List.of(RESPONSE_ID));
        assertThat(tenantScope.call(unavailable.tenant(), () -> cursors.find(unavailable.surveyId()))).isEmpty();
        assertThat(fixture.reminderTasks(unavailable.tenant(), blockedInvite.id())).isEmpty();
        assertThat(email.deliveredTo(blockedAddress)).isEmpty();
        assertThat(fixture.reminderTasks(healthy.tenant(), healthyInvite.id())).hasSize(1);
        assertThat(email.deliveredTo(healthyAddress)).isPresent();
    }

    @Test
    void anAlreadyQueuedReminderWaitsForRecoveryAndIsThenSkippedForTheCompletedRespondent() {
        Published p = fixture.publishedSurvey();
        TaskView invite = dueInvitation(p);
        String key = fixture.respondentKeyOf(p.tenant(), invite.id(), 1);
        String address = fixture.addressOf(p.tenant(), invite.id(), 1);
        assertThat(reminders.runOnce(p.tenant())).isEqualTo(1);
        UUID queued = fixture.reminderTasks(p.tenant(), invite.id()).getFirst();
        fixture.responses().response(p.tenant(), p.instance(), p.sid(), ResponseFixture.GENERATION,
                RESPONSE_ID, EngineEventType.COMPLETED);
        source.failOnceThenResolve(p, key);

        worker.runOnce();

        assertThat(tenantScope.call(p.tenant(), () -> cursors.find(p.surveyId()))).isEmpty();
        assertThat(email.deliveredTo(address)).isEmpty();
        assertThat(fixture.recipientViews(p.owner(), queued)).singleElement()
                .satisfies(recipient -> assertThat(recipient.state()).isEqualTo("queued"));

        worker.runOnce();

        assertThat(source.requests).containsExactly(List.of(RESPONSE_ID), List.of(RESPONSE_ID));
        assertThat(completions.isCompleted(p.owner(), p.surveyId(), key)).isTrue();
        assertThat(fixture.status(p.owner(), queued).skippedCount()).isEqualTo(1);
        assertThat(email.deliveredTo(address)).isEmpty();
    }

    private TaskView dueInvitation(Published p) {
        assertThat(providers.require(DeliveryChannel.EMAIL)).isSameAs(email);
        email.reset();
        TaskView invite = fixture.emailTask(p, 1);
        fixture.run(p.tenant(), invite.id());
        assertThat(email.delivered()).hasSize(1);
        email.reset();
        reminders.create(p.owner(), p.surveyId(), new ReminderService.NewRule(invite.id(),
                DELAY, DELAY, 3, "synthetic reminder", DeliveryFixture.REMINDER_BODY));
        fixture.ageSentAt(p.tenant(), invite.id(), DELAY.plusHours(1));
        return invite;
    }

    @TestConfiguration(proxyBeanMethods = false)
    static class RecoveryConfiguration {
        @Bean
        @Primary
        RecoveringRespondentSource recoveringRespondentSource() {
            return new RecoveringRespondentSource();
        }

        @Bean
        @Primary
        RespondentIdentityResolver recoveringIdentityResolver(RecoveringRespondentSource source) {
            return new GatewayRespondentIdentityResolver(source);
        }
    }

    static final class RecoveringRespondentSource implements RespondentSource {
        private final List<List<Long>> requests = new ArrayList<>();
        private Published published;
        private String respondentKey;
        private int failuresRemaining;

        void reset() {
            published = null;
            respondentKey = null;
            failuresRemaining = 0;
            requests.clear();
        }

        void resolveAnonymously(Published published) {
            reset();
            this.published = published;
        }

        void failOnceThenResolve(Published published, String respondentKey) {
            this.published = published;
            this.respondentKey = respondentKey;
            failuresRemaining = 1;
            requests.clear();
        }

        @Override
        public Map<Long, String> readRespondents(String instance, long sid, List<Long> ids) {
            if (published == null || !published.instance().equals(instance) || published.sid() != sid) {
                return Map.of();
            }
            requests.add(List.copyOf(ids));
            if (failuresRemaining > 0) {
                failuresRemaining--;
                throw new ResponseAnswersUnavailableException("synthetic gateway HTTP 503");
            }
            return respondentKey != null && ids.contains(RESPONSE_ID)
                    ? Map.of(RESPONSE_ID, respondentKey) : Map.of();
        }
    }
}
