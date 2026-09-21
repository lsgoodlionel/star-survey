package cn.mjy.platform.access;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.support.FakeSeatAllowance;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.RepeatedTest;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.security.access.AccessDeniedException;

/** 席位：占席位的成员（含邀请中）不超过额度模块给出的上限，并发下也不超。 */
@SpringBootTest
class SeatLimitTest {

    private static final int PARALLEL_INVITES = 2;

    @Autowired
    private AccessFixture fixture;

    @Autowired
    private MemberService members;

    @Autowired
    private FakeSeatAllowance seats;

    private TenantContext owner;

    @BeforeEach
    void tenantWithTwoSeats() {
        owner = fixture.newTenant();
        seats.setLimit(owner.tenantId(), 2);
    }

    @Test
    void invitationsBeyondTheSeatLimitAreRejected() {
        members.invite(owner, "second", MemberKind.STAFF);

        assertThatThrownBy(() -> members.invite(owner, "third", MemberKind.STAFF))
                .isInstanceOf(SeatLimitExceededException.class);
        assertThat(members.seatsInUse(owner)).isEqualTo(2);
    }

    @Test
    void removingAMemberFreesTheirSeat() {
        members.invite(owner, "second", MemberKind.STAFF);
        members.remove(owner, "second");

        members.invite(owner, "third", MemberKind.STAFF);

        assertThat(members.seatsInUse(owner)).isEqualTo(2);
    }

    @Test
    void participantsDoNotUseSeatsButCannotHoldStaffRoles() {
        members.invite(owner, "second", MemberKind.STAFF);

        members.invite(owner, "respondent", MemberKind.PARTICIPANT);

        assertThat(members.seatsInUse(owner)).isEqualTo(2);
        assertThatThrownBy(() -> fixture.grants().grant(owner, new GrantRequest("respondent", "editor", null, null)))
                .isInstanceOf(IllegalArgumentException.class);
        fixture.grants().grant(owner, new GrantRequest("respondent", "participant", null, null));
    }

    @Test
    void invitingTheSameMemberTwiceDoesNotUseASecondSeat() {
        members.invite(owner, "second", MemberKind.STAFF);
        members.invite(owner, "second", MemberKind.STAFF);

        assertThat(members.seatsInUse(owner)).isEqualTo(2);
    }

    @Test
    void invitingRequiresTenantLevelManageMembers() {
        TenantContext manager = fixture.member(owner, "pm", "project_manager", fixture.project(owner.tenantId()));
        seats.setLimit(owner.tenantId(), 10);

        assertThatThrownBy(() -> members.invite(manager, "someone", MemberKind.STAFF))
                .isInstanceOf(AccessDeniedException.class);
    }

    @RepeatedTest(10)
    void twoParallelInvitationsForTheLastSeatLetExactlyOneThrough() throws Exception {
        CountDownLatch start = new CountDownLatch(1);
        ExecutorService pool = Executors.newFixedThreadPool(PARALLEL_INVITES);
        try {
            List<Future<Boolean>> results = new ArrayList<>();
            for (int i = 0; i < PARALLEL_INVITES; i++) {
                String invitee = "racer-" + i;
                results.add(pool.submit(inviteWhenReleased(start, invitee)));
            }
            start.countDown();

            long succeeded = countSucceeded(results);
            assertThat(succeeded).isEqualTo(1);
            assertThat(members.seatsInUse(owner)).isEqualTo(2);
        } finally {
            pool.shutdownNow();
        }
    }

    private Callable<Boolean> inviteWhenReleased(CountDownLatch start, String invitee) {
        return () -> {
            start.await();
            try {
                members.invite(owner, invitee, MemberKind.STAFF);
                return true;
            } catch (SeatLimitExceededException rejected) {
                return false;
            }
        };
    }

    private static long countSucceeded(List<Future<Boolean>> results)
            throws InterruptedException, ExecutionException, TimeoutException {
        long succeeded = 0;
        for (Future<Boolean> result : results) {
            if (result.get(30, TimeUnit.SECONDS)) {
                succeeded++;
            }
        }
        return succeeded;
    }
}
