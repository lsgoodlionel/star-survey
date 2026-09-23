<?php

namespace ls\tests;

/**
 * WP-06.4：扩展表答案的读取端点（ADR 0018，契约 plugin-channel-v1）。
 *
 * 这条端点是匿名可达的，而副表作答是个人数据，所以两件事必须同时成立：
 *  - 验签之前的一切拒绝**逐字相同**（否则就是预言机，见 ADR 0014 飞书回调那次）；
 *  - 未验签的请求**碰不到数据库**（本类用一个「一被调用就抛」的读取器来证明）。
 */
class MjyExtensionAnswerEndpointTest extends TestBaseClass
{
    private const INSTANCE = 'hd-engine-01';
    private const INSTANCE_SECRET = '0123456789abcdef0123456789abcdef-instance';
    private const CHANNEL_SECRET = '204745a72bcb796d18bf2df097af7efea324f61077971e41b50189196036ff31';
    private const NOW = 1800000000;
    private const SURVEY_ID = 991042;
    private const GENERATION = 'aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa';
    private const OTHER_GENERATION = 'bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb';
    private const QUESTION_CODE = 'TABLE1';
    private const STRUCTURE = 'rt3';

    /** @var \MjyStructuredAnswerStore */
    private static $store;

    /** @var \MjyExtensionAnswerReader */
    private static $reader;

    public static function setUpBeforeClass(): void
    {
        parent::setUpBeforeClass();
        self::$store = new \MjyStructuredAnswerStore(\App()->getDb(), self::INSTANCE);
        self::$store->ensureSchema();
        self::$reader = new \MjyExtensionAnswerReader(\App()->getDb(), self::$store);
    }

    protected function setUp(): void
    {
        parent::setUp();
        foreach ([self::$store->tableName(), self::$store->stateTableName()] as $table) {
            \App()->getDb()->createCommand()->delete($table, 'survey_id = :sid', [':sid' => self::SURVEY_ID]);
        }
    }

    // ---- 装配 ----

    private function endpoint(
        ?\MjyExtensionAnswerReader $reader = null,
        ?\MjyChannelRateLimit $limit = null,
        array $instanceSecrets = [self::INSTANCE_SECRET]
    ): \MjyExtensionAnswerEndpoint {
        return new \MjyExtensionAnswerEndpoint(
            new \MjyChannelAuth($instanceSecrets, self::INSTANCE),
            $reader ?? self::$reader,
            $limit ?? new AlwaysAllowRateLimit(),
            self::INSTANCE
        );
    }

    private function params(array $overrides = []): array
    {
        return $overrides + [
            'plugin' => 'MjyQuestionExtensions',
            'function' => 'extensionAnswers',
            'sid' => (string) self::SURVEY_ID,
            'generation' => self::GENERATION,
            'responseIds' => '7',
            'questionCodes' => self::QUESTION_CODE,
            'ts' => (string) self::NOW,
        ];
    }

    private function signed(array $params): array
    {
        $canonical = \MjyChannelAuth::canonicalQuery($params);
        $params['sig'] = hash_hmac('sha256', $params['ts'] . '.' . $canonical, self::CHANNEL_SECRET);
        return $params;
    }

    /** 播一条有效作答。 */
    private function seed(int $responseId, array $rows, string $generation = self::GENERATION): void
    {
        self::$store->recordAnswer(
            self::SURVEY_ID,
            $generation,
            $responseId,
            self::QUESTION_CODE,
            self::STRUCTURE,
            \MjyValidationResult::valid($rows)
        );
    }

    private function decode(\MjyChannelResponse $response): array
    {
        return json_decode($response->body(), true);
    }

    // ---- 正常读取 ----

    public function testReturnsRowsForASignedRequest(): void
    {
        $this->seed(7, [['item' => '甲', 'qty' => '2'], ['item' => '乙', 'qty' => '3']]);

        $response = $this->endpoint()->handle($this->signed($this->params()), self::NOW);

        $this->assertSame(200, $response->status());
        $body = $this->decode($response);
        $this->assertSame(self::INSTANCE, $body['engineInstanceId']);
        $this->assertSame(self::SURVEY_ID, $body['surveyId']);
        $this->assertSame(
            [['item' => '甲', 'qty' => '2'], ['item' => '乙', 'qty' => '3']],
            $body['answers']['7'][self::QUESTION_CODE]['rows']
        );
        $this->assertSame(self::STRUCTURE, $body['answers']['7'][self::QUESTION_CODE]['structureVersion']);
        $this->assertTrue($body['answers']['7'][self::QUESTION_CODE]['isValid']);
    }

    public function testReturnsSeveralResponsesInOneRead(): void
    {
        $this->seed(7, [['item' => '甲']]);
        $this->seed(9, [['item' => '乙']]);

        $response = $this->endpoint()->handle(
            $this->signed($this->params(['responseIds' => '7,9'])), self::NOW);

        // 线上格式的键是答卷号的十进制字符串（契约），网关侧按字符串键解析；
        // 这里断言原文，因为 json_decode(assoc) 会把数字键悄悄变回整数。
        $this->assertStringContainsString('"7":', $response->body());
        $this->assertStringContainsString('"9":', $response->body());
        $this->assertSame([7, 9], array_keys($this->decode($response)['answers']));
    }

    /** answers 永远是 JSON 对象，不是数组——网关侧按对象解析。 */
    public function testAnswersIsAlwaysAJsonObject(): void
    {
        $this->seed(7, [['item' => '甲']]);

        $response = $this->endpoint()->handle($this->signed($this->params()), self::NOW);

        $this->assertStringContainsString('"answers":{', $response->body());
    }

    public function testAnEmptyAnswersSetIsAnObjectNotAnArray(): void
    {
        $response = $this->endpoint()->handle($this->signed($this->params()), self::NOW);

        $this->assertStringContainsString('"answers":{}', $response->body());
    }

    public function testAnEmptyResultIsNotAnError(): void
    {
        $response = $this->endpoint()->handle($this->signed($this->params()), self::NOW);

        $this->assertSame(200, $response->status());
        $this->assertSame([], $this->decode($response)['answers']);
    }

    /** sid 不存在是正常的空，不是错误——也因此不泄露「这个问卷存不存在」。 */
    public function testAnUnknownSurveyReturnsAnEmptyResult(): void
    {
        $params = $this->signed($this->params(['sid' => '991999']));

        $response = $this->endpoint()->handle($params, self::NOW);

        $this->assertSame(200, $response->status());
        $this->assertSame([], $this->decode($response)['answers']);
    }

    /** 代次隔离：停用再激活后答卷号会重号，绝不能把别的代次的作答挂上来。 */
    public function testAnotherGenerationIsNeverReturned(): void
    {
        $this->seed(7, [['item' => '旧代次']], self::OTHER_GENERATION);

        $response = $this->endpoint()->handle($this->signed($this->params()), self::NOW);

        $this->assertSame(200, $response->status());
        $this->assertSame([], $this->decode($response)['answers']);
    }

    public function testOnlyTheResponsesThatWereAskedForComeBack(): void
    {
        $this->seed(7, [['item' => '甲']]);
        $this->seed(8, [['item' => '乙']]);

        $body = $this->decode($this->endpoint()->handle($this->signed($this->params()), self::NOW));

        $this->assertSame([7], array_keys($body['answers']));
    }

    public function testOnlyTheQuestionsThatWereAskedForComeBack(): void
    {
        $this->seed(7, [['item' => '甲']]);
        self::$store->recordAnswer(self::SURVEY_ID, self::GENERATION, 7, 'OTHERQ', self::STRUCTURE,
            \MjyValidationResult::valid([['item' => '别的题']]));

        $body = $this->decode($this->endpoint()->handle($this->signed($this->params()), self::NOW));

        $this->assertSame([self::QUESTION_CODE], array_keys($body['answers']['7']));
    }

    /** errors 列是给作答者看的拒绝理由，不是导出内容，绝不出现在应答里。 */
    public function testTheRejectionReasonIsNeverReturned(): void
    {
        self::$store->recordAnswer(self::SURVEY_ID, self::GENERATION, 7, self::QUESTION_CODE, self::STRUCTURE,
            \MjyValidationResult::invalid(['第 1 行的数量必须是整数']));

        $response = $this->endpoint()->handle($this->signed($this->params()), self::NOW);

        $this->assertStringNotContainsString('数量必须是整数', $response->body());
        $this->assertFalse($this->decode($response)['answers']['7'][self::QUESTION_CODE]['isValid']);
    }

    // ---- 统一拒绝（本类最要紧的一条）----

    /**
     * 验签之前的每一种拒绝，都必须返回**逐字相同**的状态与响应体。
     * 任何按原因分叉的应答都是预言机（ADR 0018 决定 5）。
     */
    public function testEveryPreVerificationRejectionIsByteIdentical(): void
    {
        $good = $this->signed($this->params());
        $bad = [
            'no signature' => $this->params(),
            'empty signature' => ['sig' => ''] + $this->params(),
            'malformed signature' => ['sig' => str_repeat('z', 64)] + $this->params(),
            'wrong signature' => ['sig' => str_repeat('a', 64)] + $this->params(),
            'stale timestamp' => $this->signed($this->params(['ts' => (string) (self::NOW - 4000)])),
            'non numeric timestamp' => $this->signed($this->params(['ts' => 'soon'])),
            'retargeted survey' => ['sid' => '991999'] + $good,
            'unknown survey, unsigned' => $this->params(['sid' => '991999']),
            'appended parameter' => ['debug' => '1'] + $good,
            'missing parameter' => array_diff_key($good, ['generation' => null]),
            'wrong function' => $this->signed($this->params(['function' => 'somethingElse'])),
            'too many response ids' => $this->signed($this->params([
                'responseIds' => implode(',', range(1, 201))])),
            'too many question codes' => $this->signed($this->params([
                'questionCodes' => implode(',', array_map(fn($i) => "Q$i", range(1, 51)))])),
            'response id out of grammar' => $this->signed($this->params(['responseIds' => '7;DROP'])),
            'question code out of grammar' => $this->signed($this->params(['questionCodes' => 'TAB LE'])),
            'unsorted response ids' => $this->signed($this->params(['responseIds' => '9,7'])),
            'duplicate response ids' => $this->signed($this->params(['responseIds' => '7,7'])),
            'sid not a number' => $this->signed($this->params(['sid' => 'abc'])),
            'generation out of grammar' => $this->signed($this->params(['generation' => 'gen 1'])),
        ];

        $seen = [];
        foreach ($bad as $label => $params) {
            $response = $this->endpoint()->handle($params, self::NOW);
            $seen[$label] = $response->status() . ' ' . $response->body();
        }

        $this->assertSame(
            ['401 {"error":"unauthorized"}'],
            array_values(array_unique($seen)),
            '拒绝应答出现了分叉：' . json_encode($seen, JSON_UNESCAPED_UNICODE)
        );
    }

    /**
     * 未验签的请求碰不到数据库：读取器一被调用就抛，未签名请求仍然是干净的 401。
     * 这是「验签之前零 IO」（ADR 0018 决定 6）的证明。
     */
    public function testAnUnsignedRequestNeverReachesTheDatabase(): void
    {
        $endpoint = $this->endpoint(new ExplodingReader(\App()->getDb(), self::$store));

        $response = $endpoint->handle($this->params(), self::NOW);

        $this->assertSame(401, $response->status());
        $this->assertSame('{"error":"unauthorized"}', $response->body());
    }

    public function testAnUnsignedRequestIsRejectedEvenWhenNoSecretIsConfigured(): void
    {
        $endpoint = $this->endpoint(new ExplodingReader(\App()->getDb(), self::$store), null, []);

        $response = $endpoint->handle($this->signed($this->params()), self::NOW);

        $this->assertSame(401, $response->status());
        $this->assertSame('{"error":"unauthorized"}', $response->body());
    }

    /** 拒绝的原因进日志，不进应答。 */
    public function testTheRejectionReasonCodeIsAvailableForLoggingOnly(): void
    {
        $response = $this->endpoint()->handle($this->params(), self::NOW);

        $this->assertNotSame('', $response->reason());
        $this->assertStringNotContainsString($response->reason(), $response->body());
    }

    // ---- 验签之后才可区分 ----

    /** 体量超限绝不静默截断：少几行而没人察觉，比报错危险得多。 */
    public function testAnOversizedPageIsRejectedInsteadOfTruncated(): void
    {
        $endpoint = $this->endpoint(new TooLargeReader(\App()->getDb(), self::$store));

        $response = $endpoint->handle($this->signed($this->params()), self::NOW);

        $this->assertSame(400, $response->status());
        $this->assertSame('{"error":"page_too_large"}', $response->body());
    }

    public function testRateLimitedRequestsGetA429(): void
    {
        $endpoint = $this->endpoint(null, new AlwaysDenyRateLimit());

        $response = $endpoint->handle($this->signed($this->params()), self::NOW);

        $this->assertSame(429, $response->status());
        $this->assertSame('{"error":"rate_limited"}', $response->body());
    }

    /** 限流在验签之后：未签名的请求不该消耗额度，更不该触发落库的计数器。 */
    public function testRateLimitingHappensAfterVerification(): void
    {
        $limit = new CountingRateLimit();

        $this->endpoint(new ExplodingReader(\App()->getDb(), self::$store), $limit)
            ->handle($this->params(), self::NOW);

        $this->assertSame(0, $limit->calls);
    }

    public function testAnInternalFailureBecomesA500WithoutDetail(): void
    {
        $endpoint = $this->endpoint(new ExplodingReader(\App()->getDb(), self::$store));

        $response = $endpoint->handle($this->signed($this->params()), self::NOW);

        $this->assertSame(500, $response->status());
        $this->assertSame('{"error":"unavailable"}', $response->body());
        $this->assertStringNotContainsString('boom', $response->body());
    }
}

/** 一被调用就抛：用来证明某条路径没有走到读取。 */
class ExplodingReader extends \MjyExtensionAnswerReader
{
    public function read(int $surveyId, string $generation, array $responseIds, array $questionCodes, int $cellLimit): ?array
    {
        throw new \RuntimeException('boom');
    }
}

/** 永远报告「超出单元格上限」。 */
class TooLargeReader extends \MjyExtensionAnswerReader
{
    public function read(int $surveyId, string $generation, array $responseIds, array $questionCodes, int $cellLimit): ?array
    {
        return null;
    }
}

class AlwaysAllowRateLimit extends \MjyChannelRateLimit
{
    public function __construct()
    {
    }

    public function allow(int $surveyId, int $now): bool
    {
        return true;
    }
}

class AlwaysDenyRateLimit extends AlwaysAllowRateLimit
{
    public function allow(int $surveyId, int $now): bool
    {
        return false;
    }
}

class CountingRateLimit extends AlwaysAllowRateLimit
{
    /** @var int */
    public $calls = 0;

    public function allow(int $surveyId, int $now): bool
    {
        $this->calls++;
        return true;
    }
}
