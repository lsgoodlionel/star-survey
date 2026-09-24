<?php

/**
 * 引擎侧的层级字典快照（R02-03，ADR 0019）。
 *
 * 平台发布时把字典的某一版物化进定义，网关写进 `lime_plugin_settings`（model=Survey，
 * key=dictionaries）。那是**运输方式**，不是查询方式：一本行政区划有三千多个节点，
 * 每次判定路径、每次翻页都 json_decode 一遍几十万字符是不可接受的。所以首次用到时
 * 把它物化进这两张带索引的表，之后全是索引查询。
 *
 *   {prefix}mjyquestionextensions_dict_version   一本字典的一版一行（摘要、节点数）
 *   {prefix}mjyquestionextensions_dict_node      一个节点一行
 *
 * **按摘要幂等**：装过的那一版摘要一致就什么都不做；摘要不一致说明是另一份数据，整版换掉，
 * 绝不与旧节点混在一起——混在一起会让「跨版本的节点」这类篡改变成合法路径。
 *
 * 节点代码在一版之内全局唯一（GB/T 2260 本来就是这样），所以路径判定只要一次
 * `IN (...)` 就能拿齐全部层级，不必逐级往返。
 */
class MjyDictionaryStore
{
    /**
     * 快照在 lime_plugin_settings 里的键（model=Survey）。网关写它，插件读它；
     * 两端约定在契约 question-extension-tables-v1 里。
     */
    public const SETTING_KEY = 'dictionaries';

    public const VERSION_TABLE = 'mjyquestionextensions_dict_version';
    public const NODE_TABLE = 'mjyquestionextensions_dict_node';

    /** 与平台 DictionaryLimits.MAX_NODES、网关 MAX_DICTIONARY_NODES 一致。 */
    public const MAX_NODES = 8000;
    /** 与平台 DictionaryLimits.MAX_DEPTH 一致。 */
    public const MAX_DEPTH = 8;

    private const CODE_PATTERN = '/^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$/';
    private const DICTIONARY_PATTERN = '/^[a-z][a-z0-9-]{1,63}$/';
    private const VERSION_PATTERN = '/^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$/';
    private const DIGEST_PATTERN = '/^dg1:[0-9a-f]{16}$/';
    private const LABEL_MAX_LENGTH = 200;
    /** 一次插入多少行。行数上限 8000，分批既省内存也避开占位符上限。 */
    private const INSERT_BATCH = 200;

    /** @var CDbConnection */
    private $db;

    /** @var bool */
    private $isSchemaReady = false;

    public function __construct(CDbConnection $db)
    {
        $this->db = $db;
    }

    public function versionTableName(): string
    {
        return $this->db->tablePrefix . self::VERSION_TABLE;
    }

    public function nodeTableName(): string
    {
        return $this->db->tablePrefix . self::NODE_TABLE;
    }

    // ---------------------------------------------------------------- 建表

    /** 幂等；与 MjyStructuredAnswerStore::createIfMissing 同一套并发处理。 */
    public function ensureSchema(): void
    {
        $this->createIfMissing($this->versionTableName(), function (string $table): void {
            $this->db->createCommand()->createTable($table, [
                'id' => 'pk',
                'dictionary_code' => 'string(64) NOT NULL',
                'dictionary_version' => 'string(32) NOT NULL',
                'digest' => 'string(32) NOT NULL',
                'node_count' => 'integer NOT NULL',
                'updated_at' => 'datetime NOT NULL',
            ]);
            $this->db->createCommand()->createIndex(
                $table . '_version',
                $table,
                'dictionary_code, dictionary_version',
                true
            );
        });
        $this->createIfMissing($this->nodeTableName(), function (string $table): void {
            $this->db->createCommand()->createTable($table, [
                'id' => 'pk',
                'dictionary_code' => 'string(64) NOT NULL',
                'dictionary_version' => 'string(32) NOT NULL',
                'node_code' => 'string(32) NOT NULL',
                // 根节点存空串而不是 NULL：SQL 里 `= NULL` 永远不成立，
                // 用空串可以让「取根那一层」和「取某个节点的子级」走同一条语句。
                'parent_code' => "string(32) NOT NULL DEFAULT ''",
                'depth' => 'integer NOT NULL',
                'label' => 'string(200) NOT NULL',
                'sort_key' => 'integer NOT NULL',
                'search_text' => 'string(200) NOT NULL',
            ]);
            $this->db->createCommand()->createIndex(
                $table . '_node',
                $table,
                'dictionary_code, dictionary_version, node_code',
                true
            );
            $this->db->createCommand()->createIndex(
                $table . '_children',
                $table,
                'dictionary_code, dictionary_version, parent_code, sort_key'
            );
        });
        $this->isSchemaReady = true;
    }

    /** 惰性建表：调用方不必每次显式 ensureSchema()。 */
    private function ensureSchemaOnce(): void
    {
        if (!$this->isSchemaReady) {
            $this->ensureSchema();
        }
    }

    private function createIfMissing(string $table, callable $create): void
    {
        if ($this->db->getSchema()->getTable($table, true) !== null) {
            return;
        }
        try {
            $create($table);
        } catch (CDbException $e) {
            // 并发首用时可能输掉建表竞争；表已经在了就当成功。
            if ($this->db->getSchema()->getTable($table, true) === null) {
                throw $e;
            }
        }
        $this->db->getSchema()->refresh();
    }

    // ---------------------------------------------------------------- 安装

    public function isInstalled(string $code, string $version, string $digest): bool
    {
        $this->ensureSchemaOnce();
        $row = $this->db->createCommand()
            ->select('digest')
            ->from($this->versionTableName())
            ->where(
                'dictionary_code = :code AND dictionary_version = :version',
                [':code' => $code, ':version' => $version]
            )
            ->queryRow();
        return is_array($row) && (string) $row['digest'] === $digest;
    }

    /**
     * 把一份快照物化进来。已装过同一摘要时不做任何事并返回 0。
     *
     * @param array<int, array<int, string>> $nodes [代码, 父代码, 标签] 三元组，顺序即下拉框里的先后
     * @return int 本次写入的节点数
     * @throws InvalidArgumentException 快照自己就不成立时（宁可整版不装，也不装半棵树）
     */
    public function install(string $code, string $version, string $digest, array $nodes): int
    {
        $this->ensureSchemaOnce();
        $this->requireReference($code, $version, $digest);
        if ($this->isInstalled($code, $version, $digest)) {
            return 0;
        }
        $rows = $this->resolve($nodes);
        $transaction = $this->db->getCurrentTransaction() === null ? $this->db->beginTransaction() : null;
        try {
            $this->forget($code, $version);
            $this->insertNodes($code, $version, $rows);
            $this->db->createCommand()->insert($this->versionTableName(), [
                'dictionary_code' => $code,
                'dictionary_version' => $version,
                'digest' => $digest,
                'node_count' => count($rows),
                'updated_at' => gmdate('Y-m-d H:i:s'),
            ]);
            if ($transaction !== null) {
                $transaction->commit();
            }
        } catch (Exception $e) {
            if ($transaction !== null) {
                $transaction->rollback();
            }
            throw $e;
        }
        return count($rows);
    }

    /** 删掉这一版的全部痕迹。装新的一版与测试清场都用它。 */
    public function forget(string $code, string $version): void
    {
        $this->ensureSchemaOnce();
        $condition = 'dictionary_code = :code AND dictionary_version = :version';
        $params = [':code' => $code, ':version' => $version];
        $this->db->createCommand()->delete($this->nodeTableName(), $condition, $params);
        $this->db->createCommand()->delete($this->versionTableName(), $condition, $params);
    }

    public function countNodes(string $code, string $version): int
    {
        $this->ensureSchemaOnce();
        return (int) $this->db->createCommand()
            ->select('COUNT(*)')
            ->from($this->nodeTableName())
            ->where(
                'dictionary_code = :code AND dictionary_version = :version',
                [':code' => $code, ':version' => $version]
            )
            ->queryScalar();
    }

    // ---------------------------------------------------------------- 查询

    /**
     * 某一层的一页。$parent 为 null 取根那一层。
     *
     * @return array<int, array<string, mixed>>
     */
    public function children(string $code, string $version, ?string $parent, int $offset, int $limit): array
    {
        $this->ensureSchemaOnce();
        return $this->db->createCommand()
            ->select('node_code AS code, parent_code, depth, label')
            ->from($this->nodeTableName())
            ->where(
                'dictionary_code = :code AND dictionary_version = :version AND parent_code = :parent',
                [':code' => $code, ':version' => $version, ':parent' => (string) $parent]
            )
            ->order('sort_key, node_code')
            ->limit(max(1, $limit), max(0, $offset))
            ->queryAll();
    }

    public function countChildren(string $code, string $version, ?string $parent): int
    {
        $this->ensureSchemaOnce();
        return (int) $this->db->createCommand()
            ->select('COUNT(*)')
            ->from($this->nodeTableName())
            ->where(
                'dictionary_code = :code AND dictionary_version = :version AND parent_code = :parent',
                [':code' => $code, ':version' => $version, ':parent' => (string) $parent]
            )
            ->queryScalar();
    }

    /**
     * 按标签包含或代码前缀搜索。
     *
     * @return array<int, array<string, mixed>>
     */
    public function search(string $code, string $version, string $keyword, int $limit): array
    {
        $this->ensureSchemaOnce();
        $needle = $this->escapeLike(trim($keyword));
        if ($needle === '') {
            return [];
        }
        return $this->db->createCommand()
            ->select('node_code AS code, parent_code, depth, label')
            ->from($this->nodeTableName())
            ->where(
                'dictionary_code = :code AND dictionary_version = :version'
                // 转义符用 ! 而不是反斜杠：PostgreSQL 把 SQL 里的两字符转义串直接报
                // invalid escape string，而 MySQL 不报。双库都跑才抄出这一条（只跑 MySQL 是绿的）。
                . " AND (search_text LIKE :contains ESCAPE '!' OR node_code LIKE :prefix ESCAPE '!')",
                [
                    ':code' => $code,
                    ':version' => $version,
                    ':contains' => '%' . mb_strtolower($needle) . '%',
                    ':prefix' => $needle . '%',
                ]
            )
            ->order('depth, sort_key, node_code')
            ->limit(max(1, $limit))
            ->queryAll();
    }

    /**
     * 一次取回这几个代码在这一版里的节点。路径判定用它——代码在一版之内全局唯一，
     * 所以一条语句就够，不必逐级往返。
     *
     * @param string[] $codes
     * @return array<string, array<string, mixed>> 代码 => 节点；不存在的代码不出现
     */
    public function nodesIn(string $code, string $version, array $codes): array
    {
        $this->ensureSchemaOnce();
        $legal = array_values(array_unique(array_filter(
            $codes,
            static function ($value): bool {
                return is_string($value) && preg_match(self::CODE_PATTERN, $value) === 1;
            }
        )));
        if ($legal === []) {
            return [];
        }
        $params = [':code' => $code, ':version' => $version];
        $placeholders = [];
        foreach ($legal as $index => $value) {
            $name = ':n' . $index;
            $placeholders[] = $name;
            $params[$name] = $value;
        }
        $rows = $this->db->createCommand()
            ->select('node_code AS code, parent_code, depth, label')
            ->from($this->nodeTableName())
            ->where(
                'dictionary_code = :code AND dictionary_version = :version'
                . ' AND node_code IN (' . implode(', ', $placeholders) . ')',
                $params
            )
            ->queryAll();
        $found = [];
        foreach ($rows as $row) {
            $found[(string) $row['code']] = $row;
        }
        return $found;
    }

    // ---------------------------------------------------------------- 内部

    /**
     * 把三元组算成带深度与排序的行。深度由根往下推：推不出深度的节点同时覆盖了孤儿与环。
     *
     * @param array<int, array<int, string>> $nodes
     * @return array<int, array<string, mixed>>
     */
    private function resolve(array $nodes): array
    {
        if ($nodes === [] || count($nodes) > self::MAX_NODES) {
            throw new InvalidArgumentException('字典快照的节点数必须在 1 到 ' . self::MAX_NODES . ' 之间');
        }
        $parents = [];
        $labels = [];
        $order = [];
        foreach ($nodes as $node) {
            if (!is_array($node) || count($node) !== 3) {
                throw new InvalidArgumentException('每个节点都必须是 [代码, 父代码, 标签] 三元组');
            }
            [$code, $parent, $label] = array_values($node);
            $this->requireNode($code, $parent, $label);
            if (isset($parents[$code])) {
                throw new InvalidArgumentException('字典快照里的节点代码重复：' . $code);
            }
            $parents[$code] = (string) $parent;
            $labels[$code] = (string) $label;
            $order[] = $code;
        }
        $depths = [];
        $rows = [];
        foreach ($order as $index => $code) {
            $rows[] = [
                'node_code' => $code,
                'parent_code' => $parents[$code],
                'depth' => $this->depthOf($code, $parents, $depths),
                'label' => $labels[$code],
                'sort_key' => $index,
                'search_text' => mb_substr(mb_strtolower(trim($labels[$code])), 0, self::LABEL_MAX_LENGTH),
            ];
        }
        return $rows;
    }

    /**
     * @param array<string, string> $parents
     * @param array<string, int> $depths
     */
    private function depthOf(string $code, array $parents, array &$depths): int
    {
        $chain = [];
        $cursor = $code;
        while ($cursor !== '' && !isset($depths[$cursor])) {
            if (count($chain) > count($parents)) {
                throw new InvalidArgumentException('字典快照里的父链成环：' . $code);
            }
            if (!isset($parents[$cursor])) {
                throw new InvalidArgumentException('字典快照里的父节点不存在：' . $cursor);
            }
            $chain[] = $cursor;
            $cursor = $parents[$cursor];
        }
        $depth = $cursor === '' ? 0 : $depths[$cursor];
        foreach (array_reverse($chain) as $item) {
            $depth++;
            if ($depth > self::MAX_DEPTH) {
                throw new InvalidArgumentException('字典快照超过 ' . self::MAX_DEPTH . ' 层：' . $item);
            }
            $depths[$item] = $depth;
        }
        return $depth;
    }

    /**
     * @param array<int, array<string, mixed>> $rows
     */
    private function insertNodes(string $code, string $version, array $rows): void
    {
        // 批量插入：几千个节点逐条插会往返几千次。装一版是一次性的，但一次也不该要几十秒。
        $builder = $this->db->getCommandBuilder();
        $table = $this->db->getSchema()->getTable($this->nodeTableName());
        foreach (array_chunk($rows, self::INSERT_BATCH) as $batch) {
            $values = array_map(
                static function (array $row) use ($code, $version): array {
                    return array_merge(['dictionary_code' => $code, 'dictionary_version' => $version], $row);
                },
                $batch
            );
            $builder->createMultipleInsertCommand($table, $values)->execute();
        }
    }

    /**
     * @param mixed $code
     * @param mixed $parent
     * @param mixed $label
     */
    private function requireNode($code, $parent, $label): void
    {
        if (!is_string($code) || preg_match(self::CODE_PATTERN, $code) !== 1) {
            throw new InvalidArgumentException('字典快照里的节点代码不合法');
        }
        if (!is_string($parent) || ($parent !== '' && preg_match(self::CODE_PATTERN, $parent) !== 1)) {
            throw new InvalidArgumentException('字典快照里的父代码不合法：' . $code);
        }
        if ($parent === $code) {
            throw new InvalidArgumentException('字典快照里的节点以自己为父：' . $code);
        }
        if (!is_string($label) || trim($label) === '' || mb_strlen($label) > self::LABEL_MAX_LENGTH) {
            throw new InvalidArgumentException('字典快照里的标签必须是 1–' . self::LABEL_MAX_LENGTH . ' 个字符');
        }
    }

    private function requireReference(string $code, string $version, string $digest): void
    {
        if (preg_match(self::DICTIONARY_PATTERN, $code) !== 1) {
            throw new InvalidArgumentException('字典代码不合法：' . $code);
        }
        if (preg_match(self::VERSION_PATTERN, $version) !== 1) {
            throw new InvalidArgumentException('字典版本不合法：' . $version);
        }
        if (preg_match(self::DIGEST_PATTERN, $digest) !== 1) {
            throw new InvalidArgumentException('字典摘要不合法：' . $digest);
        }
    }

    private function escapeLike(string $raw): string
    {
        return str_replace(['!', '%', '_'], ['!!', '!%', '!_'], $raw);
    }
}
