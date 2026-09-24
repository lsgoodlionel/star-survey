<?php

/**
 * 作答页取字典某一层的一页（R02-03，ADR 0019 决定 4）。
 *
 *   GET index.php/plugins/direct?plugin=MjyQuestionExtensions&function=dictionaryNodes
 *       &sid=42&dictionary=cn-admin-divisions&version=2024.1&parent=440000&offset=0&limit=200
 *
 * ## 为什么必须有这条端点
 *
 * 别的 mjy- 副表主题把可选项打进 data-* 属性；本主题不能——行政区划有数千个节点。
 * 作答者一次只看一层，所以一次只取一层。
 *
 * ## 与网关通道（ADR 0018）的区别：这条**没有签名**
 *
 * 作答者手里没有通道密钥，也不该有。因此这里刻意不套用那条通道的两条规矩：
 *
 * - **不统一成一个 401**。那条规矩是为了不给持密钥的攻击者留预言机；这里读的是公共参考数据
 *   （行政区划），区分「参数写错了」与「这份问卷没用这本字典」不泄漏任何东西，
 *   含糊其辞只会让前端没法自查。
 * - **不接 MjyChannelRateLimit**。那张计数表是落库的，而它自己的注释写得很清楚：
 *   放在验签之前就等于为匿名请求维护一张表、亲手造出放大面（ADR 0018 决定 6）。
 *   这条端点本来就没有验签，接上去只会把那个放大面造出来。
 *
 * 取而代之的自律是：**形状检查全部在任何数据库访问之前**，参数不合法的请求一次查询都不会发生；
 * 合法请求是两次带索引的查询（授权一次、取页一次），比作答页本身轻得多。
 * 已知缺口如实记在 ADR 0019 里：这条端点没有按 IP 的限流。
 *
 * ## 授权
 *
 * 只服务「这份问卷真的有一道题引用了这本字典的这一版」。所以它给不出任何一份已发布问卷
 * 没有向作答者展示过的东西；拿它来枚举别的字典是不行的。
 */
class MjyDictionaryNodesEndpoint
{
    public const FUNCTION_NAME = 'dictionaryNodes';

    /** 一次最多取多少个节点。与平台 DictionaryLimits.MAX_PAGE_SIZE 一致。 */
    public const MAX_LIMIT = 200;
    public const DEFAULT_LIMIT = 200;

    /** 参数白名单：多一个就拒，免得日后有人靠追加参数改变行为。 */
    private const ALLOWED = [
        'plugin', 'function', 'sid', 'dictionary', 'version', 'parent', 'offset', 'limit',
    ];
    private const REQUIRED = ['plugin', 'function', 'sid', 'dictionary', 'version'];

    private const SID_PATTERN = '/^\d{1,10}$/';
    private const DICTIONARY_PATTERN = '/^[a-z][a-z0-9-]{1,63}$/';
    private const VERSION_PATTERN = '/^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$/';
    private const NODE_PATTERN = '/^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$/';
    private const NUMBER_PATTERN = '/^\d{1,6}$/';

    /** @var CDbConnection */
    private $db;

    /** @var MjyDictionaryStore */
    private $dictionaries;

    public function __construct(CDbConnection $db, MjyDictionaryStore $dictionaries)
    {
        $this->db = $db;
        $this->dictionaries = $dictionaries;
    }

    /**
     * @param array<string, mixed> $query 本次请求的全部查询参数（原样，含多余的）
     */
    public function handle(array $query): MjyChannelResponse
    {
        $shape = $this->checkShape($query);
        if ($shape !== '') {
            return MjyChannelResponse::badRequest($shape);
        }
        $surveyId = (int) $query['sid'];
        $dictionary = (string) $query['dictionary'];
        $version = (string) $query['version'];

        if (!$this->surveyUses($surveyId, $dictionary, $version)) {
            return MjyChannelResponse::notFound('dictionary_not_used_by_survey');
        }
        $parent = isset($query['parent']) ? (string) $query['parent'] : '';
        $offset = isset($query['offset']) ? (int) $query['offset'] : 0;
        $limit = isset($query['limit']) ? min((int) $query['limit'], self::MAX_LIMIT) : self::DEFAULT_LIMIT;

        try {
            $nodes = $this->dictionaries->children($dictionary, $version, $parent, $offset, max(1, $limit));
            $total = $this->dictionaries->countChildren($dictionary, $version, $parent);
        } catch (Exception $e) {
            return MjyChannelResponse::unavailable('dictionary_read_failed');
        }
        return MjyChannelResponse::ok($this->encode($dictionary, $version, $parent, $nodes, $total));
    }

    /**
     * 形状检查。**一次数据库访问都不做**：参数不合法的请求不该变成一次查询。
     *
     * @param array<string, mixed> $query
     * @return string 空串表示通过；否则是给日志的原因码
     */
    private function checkShape(array $query): string
    {
        foreach (array_keys($query) as $name) {
            if (!in_array($name, self::ALLOWED, true)) {
                return 'unexpected_parameter';
            }
        }
        foreach (self::REQUIRED as $name) {
            if (!isset($query[$name]) || !is_scalar($query[$name])) {
                return 'missing_parameter';
            }
        }
        foreach ($query as $value) {
            if (!is_scalar($value)) {
                return 'non_scalar_parameter';
            }
        }
        if (preg_match(self::SID_PATTERN, (string) $query['sid']) !== 1) {
            return 'bad_sid';
        }
        if (preg_match(self::DICTIONARY_PATTERN, (string) $query['dictionary']) !== 1) {
            return 'bad_dictionary';
        }
        if (preg_match(self::VERSION_PATTERN, (string) $query['version']) !== 1) {
            return 'bad_version';
        }
        // parent 为空串＝取根那一层，是正常用法。
        $parent = isset($query['parent']) ? (string) $query['parent'] : '';
        if ($parent !== '' && preg_match(self::NODE_PATTERN, $parent) !== 1) {
            return 'bad_parent';
        }
        foreach (['offset', 'limit'] as $name) {
            if (isset($query[$name]) && preg_match(self::NUMBER_PATTERN, (string) $query[$name]) !== 1) {
                return 'bad_' . $name;
            }
        }
        return '';
    }

    /**
     * 这份问卷真的有一道题引用了这本字典的这一版吗。
     *
     * 一次带索引的连接查询，不读任何作答。
     */
    private function surveyUses(int $surveyId, string $dictionary, string $version): bool
    {
        $prefix = $this->db->tablePrefix;
        $found = $this->db->createCommand()
            ->select('q.qid')
            ->from($prefix . 'questions q')
            ->join(
                $prefix . 'question_attributes d',
                'd.qid = q.qid AND d.attribute = :dictAttr AND d.value = :dict'
            )
            ->join(
                $prefix . 'question_attributes v',
                'v.qid = q.qid AND v.attribute = :verAttr AND v.value = :ver'
            )
            ->where('q.sid = :sid AND q.parent_qid = 0')
            ->limit(1)
            ->queryScalar([
                ':sid' => $surveyId,
                ':dictAttr' => MjyQuestionAttributeDefinitions::DICTIONARY,
                ':dict' => $dictionary,
                ':verAttr' => MjyQuestionAttributeDefinitions::DICTIONARY_VERSION,
                ':ver' => $version,
            ]);
        return $found !== false && $found !== null;
    }

    /**
     * @param array<int, array<string, mixed>> $nodes
     */
    private function encode(string $dictionary, string $version, string $parent, array $nodes, int $total): string
    {
        $items = [];
        foreach ($nodes as $node) {
            $items[] = [
                'code' => (string) $node['code'],
                'label' => (string) $node['label'],
                // 叶子与否由层深决定，前端据它知道还要不要再往下取一级。
                'depth' => (int) $node['depth'],
            ];
        }
        return (string) json_encode([
            'dictionary' => $dictionary,
            'version' => $version,
            'parent' => $parent,
            'nodes' => $items,
            'total' => $total,
        ], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    }
}
