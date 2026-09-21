package cn.mjy.platform.entitlement;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.TreeSet;
import java.util.UUID;
import java.util.stream.Collectors;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;

/**
 * 套餐目录：能力与计量的登记，以及套餐版本的发布与查询。
 * 这些是控制平面数据（不按租户隔离）；已发布版本由数据库权限与触发器保证不可修改。
 * 发布套餐属于平台运营操作，调用方（运营后台）须先做好权限判定。
 */
@Service
public class PlanCatalog {

    private static final String PLAN_COLUMNS = """
            id, plan_code, version, array_to_string(capabilities, ',') AS capabilities,
            export_window_days, published_at
            """;

    private final JdbcClient jdbc;

    public PlanCatalog(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /** 发布一个新版本：同一套餐代码的版本号递增，旧版本原样保留。 */
    public PlanVersion publish(PlanDefinition definition) {
        requireRegistered("capability_definition", definition.capabilities());
        requireRegistered("meter_definition", definition.quotas().keySet());
        UUID id = UUID.randomUUID();
        jdbc.sql("""
                INSERT INTO plan_version (id, plan_code, version, capabilities, quotas, export_window_days)
                SELECT :id, :code, COALESCE(MAX(version), 0) + 1, string_to_array(:capabilities, ','),
                       CAST(:quotas AS jsonb), :exportWindowDays
                FROM plan_version WHERE plan_code = :code
                """)
                .param("id", id)
                .param("code", definition.planCode())
                .param("capabilities", String.join(",", new TreeSet<>(definition.capabilities())))
                .param("quotas", quotasAsJson(definition.quotas()))
                .param("exportWindowDays", definition.exportWindowDays())
                .update();
        return find(id).orElseThrow();
    }

    public Optional<PlanVersion> find(UUID id) {
        return jdbc.sql("SELECT " + PLAN_COLUMNS + " FROM plan_version WHERE id = :id")
                .param("id", id)
                .query(this::mapPlan)
                .optional();
    }

    /** 某套餐的全部已发布版本，按版本号升序。 */
    public List<PlanVersion> versionsOf(String planCode) {
        return jdbc.sql("SELECT " + PLAN_COLUMNS + " FROM plan_version WHERE plan_code = :code ORDER BY version")
                .param("code", planCode)
                .query(this::mapPlan)
                .list();
    }

    public Optional<CapabilityDefinition> capability(String code) {
        return jdbc.sql("SELECT code, access, meter_code FROM capability_definition WHERE code = :code")
                .param("code", code)
                .query((rs, row) -> new CapabilityDefinition(rs.getString("code"),
                        CatalogEnums.fromDatabase(AccessKind.class, rs.getString("access")),
                        rs.getString("meter_code")))
                .optional();
    }

    public Optional<MeterDefinition> meter(String code) {
        return jdbc.sql("SELECT code, unit, scope, reset_policy FROM meter_definition WHERE code = :code")
                .param("code", code)
                .query((rs, row) -> new MeterDefinition(rs.getString("code"), rs.getString("unit"),
                        CatalogEnums.fromDatabase(MeterScope.class, rs.getString("scope")),
                        CatalogEnums.fromDatabase(ResetPolicy.class, rs.getString("reset_policy"))))
                .optional();
    }

    private void requireRegistered(String table, Set<String> codes) {
        if (codes.isEmpty()) {
            return;
        }
        Set<String> known = new HashSet<>(jdbc.sql(
                        "SELECT code FROM " + table + " WHERE code = ANY (string_to_array(:codes, ','))")
                .param("codes", String.join(",", codes))
                .query(String.class)
                .list());
        Set<String> unknown = codes.stream().filter(code -> !known.contains(code)).collect(Collectors.toSet());
        if (!unknown.isEmpty()) {
            throw new IllegalArgumentException("unregistered codes in " + table + ": " + unknown);
        }
    }

    /** 代码已由 {@link PlanDefinition#CODE} 限定为安全字符，直接拼 JSON 不会注入。 */
    private static String quotasAsJson(Map<String, Long> quotas) {
        return quotas.entrySet().stream()
                .sorted(Map.Entry.comparingByKey())
                .map(entry -> "\"" + entry.getKey() + "\":" + entry.getValue())
                .collect(Collectors.joining(",", "{", "}"));
    }

    private PlanVersion mapPlan(ResultSet rs, int row) throws SQLException {
        UUID id = rs.getObject("id", UUID.class);
        String capabilities = rs.getString("capabilities");
        return new PlanVersion(id, rs.getString("plan_code"), rs.getInt("version"),
                capabilities.isEmpty() ? Set.of() : Set.copyOf(Arrays.asList(capabilities.split(","))),
                quotasOf(id), rs.getInt("export_window_days"),
                rs.getObject("published_at", OffsetDateTime.class).toInstant());
    }

    private Map<String, Long> quotasOf(UUID planVersionId) {
        Map<String, Long> quotas = new HashMap<>();
        jdbc.sql("""
                SELECT q.key, q.value::bigint AS quota
                FROM plan_version p, jsonb_each_text(p.quotas) AS q
                WHERE p.id = :id
                """)
                .param("id", planVersionId)
                .query(rs -> {
                    quotas.put(rs.getString("key"), rs.getLong("quota"));
                });
        return quotas;
    }
}
