<?php

namespace ls\tests;

/**
 * WP-06.4 收尾：附件取件端点与定位器（R06-07，ADR 0015 增补四，契约 plugin-channel-v1「附件取件」）。
 *
 * 与扩展表答案端点同源的两条必须同时成立：
 *  - 验签之前的一切拒绝**逐字相同**（否则就是预言机）；
 *  - 未验签的请求**碰不到数据库、碰不到文件系统**（本类用一个「一被调用就抛」的定位器来证明）。
 *
 * 另加这条端点自己的三道闸门：代次、「这份文件真的列在这份答卷的这一列里」、路径不出上传目录。
 */
class MjyAttachmentFileEndpointTest extends TestBaseClass
{
    private const INSTANCE = 'hd-engine-01';
    private const INSTANCE_SECRET = '0123456789abcdef0123456789abcdef-instance';
    private const CHANNEL_SECRET = '204745a72bcb796d18bf2df097af7efea324f61077971e41b50189196036ff31';
    private const NOW = 1800000000;
    private const SURVEY_ID = 991043;
    private const FIELD = '991043X1X1';
    private const STORED_NAME = 'fu_1a2b3c';
    private const CONTENT = 'not really a png';

    /** @var string 本测试自己的上传根目录 */
    private static $uploadDir;

    /** @var \MjyGenerationRef */
    private static $generations;

    /** @var string */
    private static $generation;

    public static function setUpBeforeClass(): void
    {
        parent::setUpBeforeClass();
        self::$uploadDir = sys_get_temp_dir() . '/mjy-attachment-test-' . getmypid();
        @mkdir(self::$uploadDir . '/surveys/' . self::SURVEY_ID . '/files', 0700, true);
        self::$generations = new \MjyGenerationRef(\App()->getDb());
        self::$generation = self::$generations->current(self::SURVEY_ID);
        self::createResponseTable();
    }

    public static function tearDownAfterClass(): void
    {
        self::dropResponseTable();
        self::removeTree(self::$uploadDir);
        parent::tearDownAfterClass();
    }

    private static function responseTable(): string
    {
        return \App()->getDb()->tablePrefix . 'survey_' . self::SURVEY_ID;
    }

    private static function createResponseTable(): void
    {
        self::dropResponseTable();
        \App()->getDb()->createCommand()->createTable(self::responseTable(), [
            'id' => 'pk',
            self::FIELD => 'text NULL',
            'othercol' => 'text NULL',
        ]);
        \App()->getDb()->getSchema()->refresh();
    }

    private static function dropResponseTable(): void
    {
        try {
            \App()->getDb()->createCommand()->dropTable(self::responseTable());
        } catch (\Exception $ignored) {
            // 首次运行时表本来就不存在。
        }
        \App()->getDb()->getSchema()->refresh();
    }

    private static function removeTree(string $path): void
    {
        if (!is_dir($path)) {
            @unlink($path);
            return;
        }
        foreach (array_diff(scandir($path), ['.', '..']) as $entry) {
            self::removeTree($path . '/' . $entry);
        }
        @rmdir($path);
    }

    protected function setUp(): void
    {
        parent::setUp();
        \App()->getDb()->createCommand()->truncateTable(self::responseTable());
        self::removeTree(self::$uploadDir . '/surveys/' . self::SURVEY_ID . '/files');
        @mkdir(self::$uploadDir . '/surveys/' . self::SURVEY_ID . '/files', 0700, true);
    }

    // ---- 装配 ----

    private function locator(): \MjyAttachmentLocator
    {
        return new \MjyAttachmentLocator(\App()->getDb(), self::$generations, self::$uploadDir);
    }

    private function endpoint(
        ?\MjyAttachmentLocator $locator = null,
        ?\MjyChannelRateLimit $limit = null,
        array $instanceSecrets = [self::INSTANCE_SECRET]
    ): \MjyAttachmentFileEndpoint {
        return new \MjyAttachmentFileEndpoint(
            new \MjyChannelAuth($instanceSecrets, self::INSTANCE),
            $locator ?? $this->locator(),
            $limit ?? new AlwaysAllowRateLimit()
        );
    }

    private function params(array $overrides = []): array
    {
        return $overrides + [
            'plugin' => 'MjyQuestionExtensions',
            'function' => 'attachmentFile',
            'sid' => (string) self::SURVEY_ID,
            'generation' => self::$generation,
            'responseId' => '7',
            'field' => self::FIELD,
            'storedName' => self::STORED_NAME,
            'maxBytes' => '1048576',
            'ts' => (string) self::NOW,
        ];
    }

    private function signed(array $params): array
    {
        $canonical = \MjyChannelAuth::canonicalQuery($params);
        $params['sig'] = hash_hmac('sha256', $params['ts'] . '.' . $canonical, self::CHANNEL_SECRET);
        return $params;
    }

    /** 播一份答卷：那一列列出这些存储名，文件也真的放到上传目录里。 */
    private function seed(int $responseId, array $storedNames, bool $onDisk = true, string $content = self::CONTENT): void
    {
        $files = [];
        foreach ($storedNames as $name) {
            $files[] = ['name' => '甲乙.png', 'size' => '1', 'ext' => 'png', 'filename' => $name];
            if ($onDisk) {
                file_put_contents(self::$uploadDir . '/surveys/' . self::SURVEY_ID . '/files/' . $name, $content);
            }
        }
        \App()->getDb()->createCommand()->insert(self::responseTable(), [
            'id' => $responseId,
            self::FIELD => json_encode($files, JSON_UNESCAPED_UNICODE),
        ]);
    }

    // ---- 定位器本身 ----

    /** 三道闸门都过时给出上传目录里的那个绝对路径。 */
    public function testTheLocatorResolvesAListedFile(): void
    {
        $this->seed(7, [self::STORED_NAME]);

        $path = $this->locator()->locate(
            self::SURVEY_ID, self::$generation, 7, self::FIELD, self::STORED_NAME);

        $this->assertNotNull($path);
        $this->assertStringEndsWith('/surveys/' . self::SURVEY_ID . '/files/' . self::STORED_NAME, $path);
    }

    // ---- 正常取件 ----

    public function testReturnsTheFileForASignedRequest(): void
    {
        $this->seed(7, [self::STORED_NAME]);

        $outcome = $this->endpoint()->handle($this->signed($this->params()), self::NOW);

        $this->assertInstanceOf(\MjyChannelFile::class, $outcome);
        $this->assertSame(strlen(self::CONTENT), $outcome->size());
        $this->assertSame(self::CONTENT, file_get_contents($outcome->path()));
    }

    /** 端点从不把内容读进内存：它交出的是路径与大小，连放内容的地方都没有。 */
    public function testTheEndpointNeverHoldsTheFileContents(): void
    {
        $this->seed(7, [self::STORED_NAME], true, str_repeat('x', 100000));

        $outcome = $this->endpoint()->handle($this->signed($this->params()), self::NOW);

        $this->assertInstanceOf(\MjyChannelFile::class, $outcome);
        $this->assertSame([], array_filter(get_object_vars($outcome), 'is_resource'));
        $this->assertSame(100000, $outcome->size());
    }

    // ---- 三道闸门 ----

    public function testAFileFromAnotherGenerationIsGone(): void
    {
        $this->seed(7, [self::STORED_NAME]);

        $outcome = $this->endpoint()->handle(
            $this->signed($this->params(['generation' => 'bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb'])), self::NOW);

        $this->assertNotFound($outcome);
    }

    public function testAFileThatIsNotListedInThatColumnIsGone(): void
    {
        // 文件在磁盘上，但这份答卷的这一列里没有它：只凭名字猜不到别人的附件。
        $this->seed(7, ['fu_other']);
        file_put_contents(self::$uploadDir . '/surveys/' . self::SURVEY_ID . '/files/' . self::STORED_NAME, 'x');

        $this->assertNotFound($this->endpoint()->handle($this->signed($this->params()), self::NOW));
    }

    public function testAFileListedByAnotherResponseIsGone(): void
    {
        $this->seed(7, [self::STORED_NAME]);
        $this->seed(9, ['fu_nine']);

        $outcome = $this->endpoint()->handle($this->signed($this->params(['responseId' => '9'])), self::NOW);

        $this->assertNotFound($outcome);
    }

    public function testAColumnThatIsNotInTheResponseTableIsGone(): void
    {
        $this->seed(7, [self::STORED_NAME]);

        $outcome = $this->endpoint()->handle($this->signed($this->params(['field' => 'nosuchcolumn'])), self::NOW);

        $this->assertNotFound($outcome);
    }

    public function testAListedFileThatIsNoLongerOnDiskIsGone(): void
    {
        $this->seed(7, [self::STORED_NAME], false);

        $this->assertNotFound($this->endpoint()->handle($this->signed($this->params()), self::NOW));
    }

    public function testAMissingResponseIsGone(): void
    {
        $this->assertNotFound($this->endpoint()->handle($this->signed($this->params()), self::NOW));
    }

    // ---- 上限 ----

    public function testAFileOverTheRequestedLimitIsRefusedRatherThanTruncated(): void
    {
        $this->seed(7, [self::STORED_NAME]);

        $outcome = $this->endpoint()->handle($this->signed($this->params(['maxBytes' => '5'])), self::NOW);

        $this->assertInstanceOf(\MjyChannelResponse::class, $outcome);
        $this->assertSame(413, $outcome->status());
        $this->assertSame('{"error":"too_large"}', $outcome->body());
    }

    // ---- 统一 401 与零 IO ----

    /** 验签之前的每一种拒绝都是同一个状态、同一段体；原因只进日志。 */
    public function testEveryRejectionBeforeTheSignatureIsWordForWordTheSame(): void
    {
        $bad = [
            'missing signature' => $this->params(),
            'bad signature' => ['sig' => str_repeat('0', 64)] + $this->params(),
            'stale timestamp' => $this->signed($this->params(['ts' => (string) (self::NOW - 400)])),
            'unknown parameter' => $this->signed($this->params()) + ['extra' => '1'],
            'array parameter' => $this->signed($this->params(['storedName' => ['a']])),
            'bad stored name' => $this->signed($this->params(['storedName' => '../../etc/passwd'])),
            'dotted stored name' => $this->signed($this->params(['storedName' => 'fu..1'])),
            'bad field' => $this->signed($this->params(['field' => 'no spaces'])),
            'bad generation' => $this->signed($this->params(['generation' => 'bad generation'])),
            'bad response id' => $this->signed($this->params(['responseId' => '0'])),
            'bad max bytes' => $this->signed($this->params(['maxBytes' => '0'])),
            'wrong function' => $this->signed($this->params(['function' => 'extensionAnswers'])),
        ];
        foreach ($bad as $label => $params) {
            $outcome = $this->endpoint(new ExplodingLocator())->handle($params, self::NOW);
            $this->assertInstanceOf(\MjyChannelResponse::class, $outcome, $label);
            $this->assertSame(401, $outcome->status(), $label);
            $this->assertSame('{"error":"unauthorized"}', $outcome->body(), $label);
        }
    }

    /** 没有配通道密钥时端点拒收一切（失败即关闭），且照样不碰任何东西。 */
    public function testWithoutAChannelSecretEverythingIsRefused(): void
    {
        $outcome = $this->endpoint(new ExplodingLocator(), null, [])
            ->handle($this->signed($this->params()), self::NOW);

        $this->assertSame(401, $outcome->status());
    }

    /** 限流在验签之后：未签名的请求连额度表都不该惊动。 */
    public function testTheRateLimiterIsOnlyConsultedAfterTheSignatureChecks(): void
    {
        $limit = new CountingRateLimit();

        $this->endpoint(new ExplodingLocator(), $limit)->handle($this->params(), self::NOW);
        $this->assertSame(0, $limit->calls);

        $this->seed(7, [self::STORED_NAME]);
        $this->endpoint(null, $limit)->handle($this->signed($this->params()), self::NOW);
        $this->assertSame(1, $limit->calls);
    }

    public function testOverTheRateLimitIsRefusedWithoutSayingWhy(): void
    {
        $this->seed(7, [self::STORED_NAME]);

        $outcome = $this->endpoint(new ExplodingLocator(), new AlwaysDenyRateLimit())
            ->handle($this->signed($this->params()), self::NOW);

        $this->assertSame(429, $outcome->status());
        $this->assertSame('{"error":"rate_limited"}', $outcome->body());
    }

    /** 定位失败（数据库故障）不泄露原因：同一段无细节的体。 */
    public function testALookupFailureIsReportedWithoutDetail(): void
    {
        $outcome = $this->endpoint(new ExplodingLocator())->handle($this->signed($this->params()), self::NOW);

        $this->assertSame(500, $outcome->status());
        $this->assertSame('{"error":"unavailable"}', $outcome->body());
    }

    private function assertNotFound($outcome): void
    {
        $this->assertInstanceOf(\MjyChannelResponse::class, $outcome);
        $this->assertSame(404, $outcome->status());
        $this->assertSame('{"error":"not_found"}', $outcome->body());
    }
}

/** 一被调用就抛：用来证明「走到这里之前什么都没碰」。 */
class ExplodingLocator extends \MjyAttachmentLocator
{
    public function __construct()
    {
    }

    public function locate(int $surveyId, string $generation, int $responseId, string $field, string $storedName): ?string
    {
        throw new \RuntimeException('the locator must not be reached');
    }
}
