package cn.mjy.platform.entitlement;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.time.temporal.ChronoUnit;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;

/**
 * 订阅期管理。所有写入都是追加：换套餐、续费、取消各追加一期，历史期不被改写。
 * 读写都在 {@link TenantScope} 内进行，由行级安全保证只能碰到本租户的订阅。
 * 开通与取消属于计费/运营操作，调用方须先做好权限判定。
 */
@Service
public class SubscriptionService {

    private static final String COLUMNS = "id, tenant_id, plan_version_id, kind, starts_at, ends_at";

    private final TenantScope tenantScope;
    private final JdbcClient jdbc;

    public SubscriptionService(TenantScope tenantScope, JdbcClient jdbc) {
        this.tenantScope = tenantScope;
        this.jdbc = jdbc;
    }

    /** 开启一个试用或正式订阅期。换套餐就是以新套餐版本再开一期。 */
    public Subscription start(TenantId tenant, UUID planVersionId, SubscriptionKind kind,
                              Instant startsAt, Instant endsAt) {
        if (kind == SubscriptionKind.CANCELLED) {
            throw new IllegalArgumentException("use cancel() to end a subscription");
        }
        Subscription period = new Subscription(UUID.randomUUID(), tenant, planVersionId, kind,
                micros(startsAt), micros(endsAt));
        tenantScope.run(tenant, () -> insert(period));
        return period;
    }

    /** 取消：以当前套餐版本追加一个从此刻起的取消期，租户随即进入只读。 */
    public Subscription cancel(TenantId tenant) {
        return tenantScope.call(tenant, () -> {
            Instant now = micros(Instant.now());
            Subscription current = currentInScope(now)
                    .orElseThrow(() -> new IllegalStateException("tenant has no subscription to cancel"));
            Subscription cancelled = new Subscription(UUID.randomUUID(), tenant, current.planVersionId(),
                    SubscriptionKind.CANCELLED, now, now);
            insert(cancelled);
            return cancelled;
        });
    }

    public Optional<Subscription> current(TenantId tenant) {
        return tenantScope.call(tenant, () -> currentInScope(Instant.now()));
    }

    /** 全部订阅期，按开始时间与写入顺序升序。 */
    public List<Subscription> history(TenantId tenant) {
        return tenantScope.call(tenant, () -> jdbc.sql(
                        "SELECT " + COLUMNS + " FROM subscription ORDER BY starts_at, seq")
                .query(SubscriptionService::map)
                .list());
    }

    /** 当前租户作用域内、在 asOf 时刻已开始的最新一期。调用方必须已处于 {@link TenantScope} 内。 */
    Optional<Subscription> currentInScope(Instant asOf) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM subscription WHERE starts_at <= :asOf"
                        + " ORDER BY starts_at DESC, seq DESC LIMIT 1")
                .param("asOf", OffsetDateTime.ofInstant(asOf, ZoneOffset.UTC))
                .query(SubscriptionService::map)
                .optional();
    }

    private void insert(Subscription period) {
        jdbc.sql("""
                INSERT INTO subscription (id, tenant_id, plan_version_id, kind, starts_at, ends_at)
                VALUES (:id, :tenant, :plan, :kind, :startsAt, :endsAt)
                """)
                .param("id", period.id())
                .param("tenant", period.tenantId().value())
                .param("plan", period.planVersionId())
                .param("kind", CatalogEnums.toDatabase(period.kind()))
                .param("startsAt", OffsetDateTime.ofInstant(period.startsAt(), ZoneOffset.UTC))
                .param("endsAt", OffsetDateTime.ofInstant(period.endsAt(), ZoneOffset.UTC))
                .update();
    }

    private static Subscription map(ResultSet rs, int row) throws SQLException {
        return new Subscription(rs.getObject("id", UUID.class),
                new TenantId(rs.getObject("tenant_id", UUID.class)),
                rs.getObject("plan_version_id", UUID.class),
                CatalogEnums.fromDatabase(SubscriptionKind.class, rs.getString("kind")),
                rs.getObject("starts_at", OffsetDateTime.class).toInstant(),
                rs.getObject("ends_at", OffsetDateTime.class).toInstant());
    }

    /** 数据库时间精度为微秒；先截断，保证写入值与读回值相等。 */
    private static Instant micros(Instant instant) {
        return instant.truncatedTo(ChronoUnit.MICROS);
    }
}
