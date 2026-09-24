<?php

namespace ls\tests;

/**
 * 作答页取字典某一层的一页（R02-03，ADR 0019 决定 4）。
 *
 * 这条端点**没有签名**——作答者手里没有通道密钥。它的把关全在两处：
 *   1. 形状检查在任何数据库访问之前（参数不合法的请求一次查询都不该发生）；
 *   2. 只服务「这份问卷真的引用了这本字典的这一版」，拿它枚举别的字典是不行的。
 */
class MjyDictionaryNodesEndpointTest extends TestBaseClass
{
    private const DICTIONARY = 'cn-admin-divisions';
    private const VERSION = '2024.1';
    private const DIGEST = 'dg1:0123456789abcdef';
    private const SURVEY_ID = 991303;

    /** @var \MjyDictionaryStore */
    private static $store;

    public static function setUpBeforeClass(): void
    {
        parent::setUpBeforeClass();
        self::$store = new \MjyDictionaryStore(\App()->getDb());
        self::$store->ensureSchema();
        self::$store->forget(self::DICTIONARY, self::VERSION);
        self::$store->install(self::DICTIONARY, self::VERSION, self::DIGEST, [
            ['110000', '', '北京市'],
            ['110100', '110000', '市辖区'],
            ['110101', '110100', '东城区'],
            ['110102', '110100', '西城区'],
            ['440000', '', '广东省'],
        ]);
        self::declareQuestion();
    }

    public static function tearDownAfterClass(): void
    {
        $db = \App()->getDb();
        $db->createCommand()->delete(
            $db->tablePrefix . 'question_attributes',
            'qid = :qid',
            [':qid' => self::SURVEY_ID]
        );
        $db->createCommand()->delete(
            $db->tablePrefix . 'questions',
            'sid = :sid',
            [':sid' => self::SURVEY_ID]
        );
        parent::tearDownAfterClass();
    }

    /** 造一道「引用了这本字典这一版」的题，端点的授权查询就靠它。 */
    private static function declareQuestion(): void
    {
        $db = \App()->getDb();
        $prefix = $db->tablePrefix;
        $db->createCommand()->delete($prefix . 'question_attributes', 'qid = :qid', [':qid' => self::SURVEY_ID]);
        $db->createCommand()->delete($prefix . 'questions', 'sid = :sid', [':sid' => self::SURVEY_ID]);
        $db->createCommand()->insert($prefix . 'questions', [
            'qid' => self::SURVEY_ID,
            'sid' => self::SURVEY_ID,
            'gid' => 1,
            'parent_qid' => 0,
            'type' => 'T',
            'title' => 'QREGION',
            'question_order' => 1,
            'question_theme_name' => \MjyThemedQuestionMap::CASCADING_SELECT_THEME,
        ]);
        foreach ([
            \MjyQuestionAttributeDefinitions::DICTIONARY => self::DICTIONARY,
            \MjyQuestionAttributeDefinitions::DICTIONARY_VERSION => self::VERSION,
        ] as $attribute => $value) {
            $db->createCommand()->insert($prefix . 'question_attributes', [
                'qid' => self::SURVEY_ID,
                'attribute' => $attribute,
                'value' => $value,
            ]);
        }
    }

    private function endpoint(): \MjyDictionaryNodesEndpoint
    {
        return new \MjyDictionaryNodesEndpoint(\App()->getDb(), self::$store);
    }

    /**
     * @param array<string, mixed> $overrides
     */
    private function ask(array $overrides = []): \MjyChannelResponse
    {
        return $this->endpoint()->handle(array_merge([
            'plugin' => 'MjyQuestionExtensions',
            'function' => \MjyDictionaryNodesEndpoint::FUNCTION_NAME,
            'sid' => (string) self::SURVEY_ID,
            'dictionary' => self::DICTIONARY,
            'version' => self::VERSION,
        ], $overrides));
    }

    /**
     * @return array<string, mixed>
     */
    private function body(\MjyChannelResponse $response): array
    {
        return json_decode($response->body(), true);
    }

    public function testTheRootLevelComesBackWhenNoParentIsGiven()
    {
        $response = $this->ask();

        $this->assertSame(200, $response->status());
        $body = $this->body($response);
        $this->assertSame(['110000', '440000'], array_column($body['nodes'], 'code'));
        $this->assertSame(['北京市', '广东省'], array_column($body['nodes'], 'label'));
        $this->assertSame(2, $body['total']);
    }

    public function testOneLevelAtATime()
    {
        $body = $this->body($this->ask(['parent' => '110100']));

        $this->assertSame(['110101', '110102'], array_column($body['nodes'], 'code'));
        $this->assertSame([3, 3], array_column($body['nodes'], 'depth'));
    }

    public function testAPageIsAPage()
    {
        $first = $this->body($this->ask(['parent' => '110100', 'limit' => '1']));
        $second = $this->body($this->ask(['parent' => '110100', 'limit' => '1', 'offset' => '1']));

        $this->assertSame(['110101'], array_column($first['nodes'], 'code'));
        $this->assertSame(['110102'], array_column($second['nodes'], 'code'));
        $this->assertSame(2, $first['total'], 'total 要说明这一层一共有多少，前端才知道还有没有下一页');
    }

    /** 授权：这份问卷没引用的字典取不到，端点不是通用的字典浏览器。 */
    public function testADictionaryThisSurveyDoesNotUseIsNotFound()
    {
        $response = $this->ask(['dictionary' => 'some-other-dictionary']);

        $this->assertSame(404, $response->status());
        $this->assertSame('{"error":"not_found"}', $response->body());
    }

    public function testAVersionThisSurveyDoesNotUseIsNotFound()
    {
        $this->assertSame(404, $this->ask(['version' => '1999.1'])->status());
    }

    public function testAnotherSurveyCannotBorrowThisOnesDictionary()
    {
        $this->assertSame(404, $this->ask(['sid' => '123456'])->status());
    }

    /** 参数白名单：多一个参数就拒，免得日后有人靠追加参数改变行为。 */
    public function testAnUnexpectedParameterIsRefused()
    {
        $response = $this->ask(['surprise' => '1']);

        $this->assertSame(400, $response->status());
        $this->assertSame('{"error":"bad_request"}', $response->body());
    }

    public function testMalformedParametersAreRefused()
    {
        $this->assertSame(400, $this->ask(['sid' => 'not-a-number'])->status());
        $this->assertSame(400, $this->ask(['dictionary' => 'Not A Code'])->status());
        $this->assertSame(400, $this->ask(['version' => '2024 年版'])->status());
        $this->assertSame(400, $this->ask(['parent' => "'; DROP TABLE"])->status());
        $this->assertSame(400, $this->ask(['limit' => '-1'])->status());
    }

    public function testAMissingParameterIsRefused()
    {
        $query = [
            'plugin' => 'MjyQuestionExtensions',
            'function' => \MjyDictionaryNodesEndpoint::FUNCTION_NAME,
            'sid' => (string) self::SURVEY_ID,
        ];
        $this->assertSame(400, $this->endpoint()->handle($query)->status());
    }

    /** 形状不对的请求一次数据库访问都不该发生。 */
    public function testAMalformedRequestNeverReachesTheDatabase()
    {
        $exploding = new class (\App()->getDb()) extends \MjyDictionaryStore {
            public function children(
                string $code,
                string $version,
                ?string $parent,
                int $offset,
                int $limit
            ): array {
                throw new \RuntimeException('形状检查之后才允许碰数据库');
            }
        };
        $endpoint = new \MjyDictionaryNodesEndpoint(\App()->getDb(), $exploding);

        $this->assertSame(400, $endpoint->handle(['plugin' => 'x'])->status());
    }

    public function testTheLimitIsCappedRatherThanHonouredBlindly()
    {
        $body = $this->body($this->ask(['limit' => '999999']));

        $this->assertLessThanOrEqual(\MjyDictionaryNodesEndpoint::MAX_LIMIT, count($body['nodes']));
    }
}
