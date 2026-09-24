package cn.mjy.platform.dictionary;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Optional;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * 字典三张表的读写。它们是<b>控制平面表</b>（与 platform_survey_template 同一口径），没有行级安全，
 * 因此这里的方法不带 tenant 参数；可见性由「租户侧只查已发布的版本」承担，写入由运营端点的角色拦截器承担。
 */
@Repository
class DictionaryRepository {

    private static final String DICTIONARY_COLUMNS = "code, name, max_depth, current_version, created_at";
    private static final String VERSION_COLUMNS =
            "dictionary_code, version, status, digest, node_count, published_at";
    private static final String NODE_COLUMNS = "code, parent_code, depth, label, is_leaf";

    private final JdbcClient jdbc;

    DictionaryRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    // ---------------------------------------------------------------- 字典

    void insertDictionary(String code, String name, int maxDepth, String createdBy) {
        jdbc.sql("""
                        INSERT INTO platform_dictionary (code, name, max_depth, created_by)
                        VALUES (:code, :name, :depth, :by)""")
                .param("code", code).param("name", name).param("depth", maxDepth).param("by", createdBy)
                .update();
    }

    Optional<DictionaryView> findDictionary(String code) {
        return jdbc.sql("SELECT " + DICTIONARY_COLUMNS + " FROM platform_dictionary WHERE code = :code")
                .param("code", code).query(DictionaryRepository::toDictionary).optional();
    }

    /** 取行锁：发布一版与另一版并发发布时，current_version 只能有一个赢家。 */
    Optional<DictionaryView> lockDictionary(String code) {
        return jdbc.sql("SELECT " + DICTIONARY_COLUMNS
                        + " FROM platform_dictionary WHERE code = :code FOR UPDATE")
                .param("code", code).query(DictionaryRepository::toDictionary).optional();
    }

    List<DictionaryView> allDictionaries() {
        return jdbc.sql("SELECT " + DICTIONARY_COLUMNS + " FROM platform_dictionary ORDER BY code")
                .query(DictionaryRepository::toDictionary).list();
    }

    /** 租户视角：至少发布过一版的字典才存在。 */
    List<DictionaryView> publishedDictionaries() {
        return jdbc.sql("SELECT " + DICTIONARY_COLUMNS
                        + " FROM platform_dictionary WHERE current_version IS NOT NULL ORDER BY code")
                .query(DictionaryRepository::toDictionary).list();
    }

    void setCurrentVersion(String code, String version) {
        jdbc.sql("UPDATE platform_dictionary SET current_version = :version, updated_at = now() "
                        + "WHERE code = :code")
                .param("code", code).param("version", version).update();
    }

    // ---------------------------------------------------------------- 版本

    void insertVersion(String code, String version, String createdBy) {
        jdbc.sql("""
                        INSERT INTO platform_dictionary_version
                            (dictionary_code, version, status, created_by)
                        VALUES (:code, :version, 'draft', :by)""")
                .param("code", code).param("version", version).param("by", createdBy).update();
    }

    Optional<DictionaryVersionView> findVersion(String code, String version) {
        return jdbc.sql("SELECT " + VERSION_COLUMNS + " FROM platform_dictionary_version "
                        + "WHERE dictionary_code = :code AND version = :version")
                .param("code", code).param("version", version)
                .query(DictionaryRepository::toVersion).optional();
    }

    List<DictionaryVersionView> versionsOf(String code) {
        return jdbc.sql("SELECT " + VERSION_COLUMNS + " FROM platform_dictionary_version "
                        + "WHERE dictionary_code = :code ORDER BY created_at, version")
                .param("code", code).query(DictionaryRepository::toVersion).list();
    }

    void markPublished(String code, String version, String digest, int nodeCount) {
        jdbc.sql("""
                        UPDATE platform_dictionary_version
                           SET status = 'published', digest = :digest, node_count = :count,
                               published_at = now()
                         WHERE dictionary_code = :code AND version = :version AND status = 'draft'""")
                .param("code", code).param("version", version).param("digest", digest).param("count", nodeCount)
                .update();
    }

    // ---------------------------------------------------------------- 节点

    void deleteNodes(String code, String version) {
        jdbc.sql("DELETE FROM platform_dictionary_node WHERE dictionary_code = :code AND version = :version")
                .param("code", code).param("version", version).update();
    }

    /** 一次导入整棵树，所以逐条插入即可；节点数有上限（{@link DictionaryLimits#MAX_NODES}）。 */
    void insertNodes(String code, String version, List<DictionaryNodeView> nodes) {
        int sortKey = 0;
        for (DictionaryNodeView node : nodes) {
            jdbc.sql("""
                            INSERT INTO platform_dictionary_node
                                (dictionary_code, version, code, parent_code, depth, label, sort_key,
                                 is_leaf, search_text)
                            VALUES (:dict, :version, :code, :parent, :depth, :label, :sort, :leaf, :search)""")
                    .param("dict", code).param("version", version).param("code", node.code())
                    .param("parent", node.parentCode()).param("depth", node.depth())
                    .param("label", node.label()).param("sort", sortKey++).param("leaf", node.leaf())
                    .param("search", searchText(node))
                    .update();
        }
    }

    List<DictionaryNodeView> nodes(String code, String version) {
        return jdbc.sql("SELECT " + NODE_COLUMNS + " FROM platform_dictionary_node "
                        + "WHERE dictionary_code = :code AND version = :version ORDER BY sort_key, code")
                .param("code", code).param("version", version).query(DictionaryRepository::toNode).list();
    }

    int countNodes(String code, String version) {
        return jdbc.sql("SELECT COUNT(*) FROM platform_dictionary_node "
                        + "WHERE dictionary_code = :code AND version = :version")
                .param("code", code).param("version", version).query(Integer.class).single();
    }

    int countChildren(String code, String version, String parentCode) {
        var statement = jdbc.sql("SELECT COUNT(*) FROM platform_dictionary_node "
                        + "WHERE dictionary_code = :code AND version = :version AND "
                        + parentPredicate(parentCode))
                .param("code", code).param("version", version);
        return withParent(statement, parentCode).query(Integer.class).single();
    }

    List<DictionaryNodeView> children(String code, String version, String parentCode, int offset, int limit) {
        var statement = jdbc.sql("SELECT " + NODE_COLUMNS + " FROM platform_dictionary_node "
                        + "WHERE dictionary_code = :code AND version = :version AND " + parentPredicate(parentCode)
                        + " ORDER BY sort_key, code LIMIT :limit OFFSET :offset")
                .param("code", code).param("version", version)
                .param("limit", limit).param("offset", offset);
        return withParent(statement, parentCode).query(DictionaryRepository::toNode).list();
    }

    Optional<DictionaryNodeView> node(String code, String version, String nodeCode) {
        return jdbc.sql("SELECT " + NODE_COLUMNS + " FROM platform_dictionary_node "
                        + "WHERE dictionary_code = :code AND version = :version AND code = :node")
                .param("code", code).param("version", version).param("node", nodeCode)
                .query(DictionaryRepository::toNode).optional();
    }

    /** 一次取回整条路径上的全部节点（无序），由调用方按父链串起来——比逐级往上查少 N 次往返。 */
    List<DictionaryNodeView> nodesIn(String code, String version, List<String> nodeCodes) {
        if (nodeCodes.isEmpty()) {
            return List.of();
        }
        List<String> placeholders = new ArrayList<>(nodeCodes.size());
        var statement = jdbc.sql("SELECT " + NODE_COLUMNS + " FROM platform_dictionary_node "
                + "WHERE dictionary_code = :code AND version = :version AND code IN ("
                + names(nodeCodes, placeholders) + ")")
                .param("code", code).param("version", version);
        for (int i = 0; i < nodeCodes.size(); i++) {
            statement = statement.param("n" + i, nodeCodes.get(i));
        }
        return statement.query(DictionaryRepository::toNode).list();
    }

    /**
     * 关键字搜索：代码前缀或标签包含。标签用 {@code LIKE '%…%'} 走不了索引，
     * 但一版字典的节点数有硬上限，最坏情况是扫一版，仍在可接受范围（ADR 0019 已知限制 3）。
     */
    List<DictionaryNodeView> search(String code, String version, String keyword, int limit) {
        return jdbc.sql("SELECT " + NODE_COLUMNS + " FROM platform_dictionary_node "
                        + "WHERE dictionary_code = :code AND version = :version "
                        + "  AND (search_text LIKE :contains ESCAPE '\\' OR code LIKE :prefix ESCAPE '\\') "
                        + "ORDER BY depth, sort_key, code LIMIT :limit")
                .param("code", code).param("version", version)
                .param("contains", "%" + escapeLike(keyword.toLowerCase(Locale.ROOT)) + "%")
                .param("prefix", escapeLike(keyword) + "%")
                .param("limit", limit)
                .query(DictionaryRepository::toNode).list();
    }

    // ---------------------------------------------------------------- 工具

    private static String names(List<String> values, List<String> placeholders) {
        for (int i = 0; i < values.size(); i++) {
            placeholders.add(":n" + i);
        }
        return String.join(", ", placeholders);
    }

    /** 根节点用 {@code IS NULL}：SQL 里 {@code = NULL} 永远不成立，写成参数会静默返回空页。 */
    private static String parentPredicate(String parentCode) {
        return parentCode == null ? "parent_code IS NULL" : "parent_code = :parent";
    }

    /** 与 {@link #parentPredicate} 配套：语句里没有 {@code :parent} 时就不能绑这个参数。 */
    private static JdbcClient.StatementSpec withParent(JdbcClient.StatementSpec statement, String parentCode) {
        return parentCode == null ? statement : statement.param("parent", parentCode);
    }

    private static String searchText(DictionaryNodeView node) {
        return node.label().trim().toLowerCase(Locale.ROOT);
    }

    private static String escapeLike(String raw) {
        return raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_");
    }

    private static DictionaryView toDictionary(ResultSet rs, int row) throws SQLException {
        return new DictionaryView(rs.getString("code"), rs.getString("name"), rs.getInt("max_depth"),
                rs.getString("current_version"), rs.getObject("created_at", OffsetDateTime.class));
    }

    private static DictionaryVersionView toVersion(ResultSet rs, int row) throws SQLException {
        return new DictionaryVersionView(rs.getString("dictionary_code"), rs.getString("version"),
                DictionaryVersionStatus.fromDb(rs.getString("status")), rs.getString("digest"),
                rs.getInt("node_count"), rs.getObject("published_at", OffsetDateTime.class));
    }

    private static DictionaryNodeView toNode(ResultSet rs, int row) throws SQLException {
        return new DictionaryNodeView(rs.getString("code"), rs.getString("parent_code"), rs.getInt("depth"),
                rs.getString("label"), rs.getBoolean("is_leaf"));
    }
}
