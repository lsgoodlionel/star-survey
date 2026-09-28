<?php

namespace ls\tests;

/**
 * 作答者上传的读取端点（ADR 0019 决定 8，走 ADR 0018 那条签名通道）。
 *
 * 这条端点交出去的是**作答者上传的文件本身**——比副表作答更敏感。三件事必须同时成立：
 *  - 清单只来自**上传会话表**，答卷字段里那张文件清单一个字都不参与；
 *  - 验签之前的一切拒绝逐字相同，且**碰不到数据库、碰不到磁盘**；
 *  - 取字节时的文件名只认会话行里那一个，且必须过得了路径白名单——
 *    否则一个被改坏的 stored_name 就是一次任意文件读取。
 */
class MjyUploadSessionEndpointTest extends TestBaseClass
{
    private const INSTANCE = 'hd-engine-01';
    private const INSTANCE_SECRET = '0123456789abcdef0123456789abcdef-instance';
    private const CHANNEL_SECRET = '204745a72bcb796d18bf2df097af7efea324f61077971e41b50189196036ff31';
    private const NOW = 1800000000;
    private const SURVEY_ID = 991043;
    private const GENERATION = 'aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa';
    private const OTHER_GENERATION = 'bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb';
    private const QUESTION_CODE = 'REC1';
    private const RESPONSE_ID = 7;

    /** @var \MjyUploadSessionStore */
    private static $store;

    /** @var string */
    private static $root;

    public static function setUpBeforeClass(): void
    {
        parent::setUpBeforeClass();
        self::$store = new \MjyUploadSessionStore(\App()->getDb(), self::INSTANCE);
        self::$store->ensureSchema();
        self::$root = sys_get_temp_dir() . '/mjy-upload-endpoint-test';
    }

    protected function setUp(): void
    {
        parent::setUp();
        \App()->getDb()->createCommand()->delete(
            self::$store->tableName(),
            'survey_id = :sid',
            [':sid' => self::SURVEY_ID]
        );
        $dir = self::$root . '/surveys/' . self::SURVEY_ID . '/files';
        if (!is_dir($dir)) {
            mkdir($dir, 0777, true);
        }
        foreach ((array) glob($dir . '/*') as $file) {
            @unlink($file);
        }
    }

    // ---- 装配 ----

    private function endpoint(
        array $instanceSecrets = [self::INSTANCE_SECRET],
        ?string $root = null
    ): \MjyUploadSessionEndpoint {
        return new \MjyUploadSessionEndpoint(
            new \MjyChannelAuth($instanceSecrets, self::INSTANCE),
            self::$store,
            new \MjyUploadFileReader($root ?? self::$root),
            new AlwaysAllowRateLimit(),
            self::INSTANCE
        );
    }

    private function listParams(array $overrides = []): array
    {
        return $overrides + [
            'plugin' => 'MjyQuestionExtensions',
            'function' => 'uploadSessions',
            'sid' => (string) self::SURVEY_ID,
            'generation' => self::GENERATION,
            'responseIds' => (string) self::RESPONSE_ID,
            'ts' => (string) self::NOW,
        ];
    }

    private function contentParams(string $token, array $overrides = []): array
    {
        return $overrides + [
            'plugin' => 'MjyQuestionExtensions',
            'function' => 'uploadContent',
            'sid' => (string) self::SURVEY_ID,
            'generation' => self::GENERATION,
            'uploadToken' => $token,
            'ts' => (string) self::NOW,
        ];
    }

    private function signed(array $params): array
    {
        $canonical = \MjyChannelAuth::canonicalQuery($params);
        $params['sig'] = hash_hmac('sha256', $params['ts'] . '.' . $canonical, self::CHANNEL_SECRET);
        return $params;
    }

    private function decode(\MjyChannelResponse $response): array
    {
        return json_decode($response->body(), true);
    }

    /**
     * 造一条走完整生命周期的上传会话：open() 登记，再 bind() 绑到最终文件名上，
     * 并把字节真的写到引擎的上传目录里。
     */
    private function seedUpload(
        string $storedName = 'fu_abcdef',
        string $bytes = "\x1a\x45\xdf\xa3recording",
        string $generation = self::GENERATION,
        int $responseId = self::RESPONSE_ID
    ): string {
        $token = self::$store->open(self::SURVEY_ID, $generation, $responseId, self::QUESTION_CODE, [
            'fieldname' => '991043X1X1',
            'filename' => '录音.webm',
            'randfilename' => 'futmp_zzz',
            'ext' => 'webm',
            'size' => strlen($bytes) / 1024,
        ]);
        self::$store->bind(self::SURVEY_ID, $generation, $responseId, self::QUESTION_CODE, [[
            'name' => '录音.webm',
            'size' => strlen($bytes) / 1024,
            'filename' => $storedName,
        ]]);
        file_put_contents(self::$root . '/surveys/' . self::SURVEY_ID . '/files/' . $storedName, $bytes);
        return $token;
    }

    // ---- 清单 ----

    public function testListsTheUploadSessionsOfAResponse(): void
    {
        $token = $this->seedUpload();

        $response = $this->endpoint()->handle($this->signed($this->listParams()), self::NOW);

        $this->assertSame(200, $response->status());
        $body = $this->decode($response);
        $this->assertSame(self::INSTANCE, $body['engineInstanceId']);
        $this->assertSame(self::SURVEY_ID, $body['surveyId']);
        $entry = $body['uploads'][(string) self::RESPONSE_ID][0];
        $this->assertSame($token, $entry['uploadToken']);
        $this->assertSame(self::QUESTION_CODE, $entry['questionCode']);
        $this->assertSame('录音.webm', $entry['originalName']);
        $this->assertSame('webm', $entry['extension']);
    }

    /** uploads 永远是 JSON 对象，不是数组——网关侧按对象解析。 */
    public function testUploadsIsAlwaysAJsonObject(): void
    {
        $response = $this->endpoint()->handle($this->signed($this->listParams()), self::NOW);

        $this->assertStringContainsString('"uploads":{}', $response->body());
    }

    /** 还没绑定到最终文件名的会话不进清单：它的字节还在临时目录里，取不出来。 */
    public function testAnUnboundSessionIsNotListed(): void
    {
        self::$store->open(self::SURVEY_ID, self::GENERATION, self::RESPONSE_ID, self::QUESTION_CODE, [
            'filename' => '半截.webm',
            'size' => 1,
        ]);

        $response = $this->endpoint()->handle($this->signed($this->listParams()), self::NOW);

        $this->assertSame([], $this->decode($response)['uploads']);
    }

    /** 代次隔离：停用再激活后答卷号会重号，绝不能把别的代次的上传挂上来。 */
    public function testAnotherGenerationIsNeverListed(): void
    {
        $this->seedUpload('fu_other', 'x', self::OTHER_GENERATION);

        $response = $this->endpoint()->handle($this->signed($this->listParams()), self::NOW);

        $this->assertSame([], $this->decode($response)['uploads']);
    }

    /** sid 不存在是正常的空，不是错误——也因此不泄露「这个问卷存不存在」。 */
    public function testAnUnknownSurveyReturnsAnEmptyList(): void
    {
        $response = $this->endpoint()->handle($this->signed($this->listParams(['sid' => '991999'])), self::NOW);

        $this->assertSame(200, $response->status());
        $this->assertSame([], $this->decode($response)['uploads']);
    }

    // ---- 取字节 ----

    public function testFetchesTheBytesOfOneUpload(): void
    {
        $bytes = "\x1a\x45\xdf\xa3recording";
        $token = $this->seedUpload('fu_abcdef', $bytes);

        $response = $this->endpoint()->handle($this->signed($this->contentParams($token)), self::NOW);

        $this->assertSame(200, $response->status());
        $body = $this->decode($response);
        $this->assertSame($token, $body['uploadToken']);
        $this->assertSame(self::QUESTION_CODE, $body['questionCode']);
        $this->assertSame(base64_encode($bytes), $body['contentBase64']);
        $this->assertSame(strlen($bytes), $body['sizeBytes']);
    }

    /** 取件按 (sid, 代次, token) 三者定位：光有 token 改不到别的代次去。 */
    public function testAtokenFromAnotherGenerationIsNotFound(): void
    {
        $token = $this->seedUpload('fu_other', 'x', self::OTHER_GENERATION);

        $response = $this->endpoint()->handle($this->signed($this->contentParams($token)), self::NOW);

        $this->assertSame(404, $response->status());
    }

    public function testAnUnknownTokenIsNotFound(): void
    {
        $params = $this->contentParams('11111111-2222-4333-8444-555555555555');

        $response = $this->endpoint()->handle($this->signed($params), self::NOW);

        $this->assertSame(404, $response->status());
    }

    /** 会话行在、字节没了：报错，绝不回一个「看着完整、其实空的」应答。 */
    public function testAmissingFileIsAnErrorNotAnEmptyBody(): void
    {
        $token = $this->seedUpload('fu_gone');
        unlink(self::$root . '/surveys/' . self::SURVEY_ID . '/files/fu_gone');

        $response = $this->endpoint()->handle($this->signed($this->contentParams($token)), self::NOW);

        $this->assertNotSame(200, $response->status());
        $this->assertStringNotContainsString('contentBase64', $response->body());
    }

    /**
     * 一个被改坏的 stored_name 就是一次任意文件读取。
     *
     * <p>用例刻意指向一个**真的存在、真的读得出来**的文件：早先的写法指向
     * `../../../../etc/passwd`，拼出来的路径压根不存在，于是「拒绝」来自 realpath 失败
     * 而不是来自守卫——把三道守卫全去掉它照样绿。经变异验证过：现在这条去掉守卫即红。
     */
    public function testAstoredNameThatEscapesTheUploadDirectoryIsRefused(): void
    {
        $secret = self::$root . '/secret.txt';
        file_put_contents($secret, 'TOP-SECRET-CONTENT');
        $token = $this->seedUpload('fu_ok');
        $this->rewriteStoredName($token, '../../../secret.txt');

        $response = $this->endpoint()->handle($this->signed($this->contentParams($token)), self::NOW);

        $this->assertSame('TOP-SECRET-CONTENT', file_get_contents($secret), '用例自身的前提：那个文件真的读得出来');
        $this->assertNotSame(200, $response->status());
        $this->assertStringNotContainsString(base64_encode('TOP-SECRET-CONTENT'), $response->body());
    }

    /** 文件名过得了白名单，但它是一条指向目录外的符号链接：realpath 那道闸负责挡住。 */
    public function testAsymlinkOutOfTheUploadDirectoryIsRefused(): void
    {
        $secret = self::$root . '/linked-secret.txt';
        file_put_contents($secret, 'LINKED-SECRET');
        $link = self::$root . '/surveys/' . self::SURVEY_ID . '/files/fu_link';
        @unlink($link);
        $this->assertTrue(symlink($secret, $link), '用例自身的前提：符号链接建得起来');
        $token = $this->seedUpload('fu_ok2');
        $this->rewriteStoredName($token, 'fu_link');

        $response = $this->endpoint()->handle($this->signed($this->contentParams($token)), self::NOW);

        $this->assertNotSame(200, $response->status());
        $this->assertStringNotContainsString(base64_encode('LINKED-SECRET'), $response->body());
    }

    private function rewriteStoredName(string $token, string $storedName): void
    {
        \App()->getDb()->createCommand()->update(
            self::$store->tableName(),
            ['stored_name' => $storedName],
            'upload_token = :token',
            [':token' => $token]
        );
    }

    // ---- 统一 401 与零 IO ----

    /**
     * 验签之前的每一种拒绝都必须逐字相同。列出来的每一条都走同一个出口，
     * 任何按原因分叉的应答都是预言机（ADR 0014 飞书回调那次）。
     */
    public function testEveryPreSignatureRefusalIsByteIdentical(): void
    {
        $bodies = [];
        $statuses = [];
        $cases = [
            'no signature' => $this->listParams(),
            'bad signature' => $this->listParams(['sig' => str_repeat('a', 64)]),
            'bad signature format' => $this->listParams(['sig' => 'nope']),
            'stale timestamp' => $this->signed($this->listParams(['ts' => (string) (self::NOW - 400)])),
            'unknown parameter' => $this->signed($this->listParams()) + ['extra' => '1'],
            // 数组形态的 sig 必须在签名之后放进去——signed() 自己会覆盖 sig。
            'array parameter' => array_merge($this->signed($this->listParams()), ['sig' => ['x']]),
            'missing parameter' => array_diff_key($this->signed($this->listParams()), ['generation' => null]),
            'wrong function' => $this->signed($this->listParams(['function' => 'nope'])),
            'bad survey id' => $this->signed($this->listParams(['sid' => 'x'])),
            'bad generation' => $this->signed($this->listParams(['generation' => '../x'])),
            'bad response id' => $this->signed($this->listParams(['responseIds' => '0'])),
            'bad upload token' => $this->signed($this->contentParams('not-a-uuid')),
        ];
        foreach ($cases as $name => $params) {
            $response = $this->endpoint()->handle($params, self::NOW);
            $statuses[$name] = $response->status();
            $bodies[$name] = $response->body();
        }

        $this->assertSame([401], array_values(array_unique($statuses)), '状态必须全是 401：' . json_encode($statuses));
        $this->assertSame(['{"error":"unauthorized"}'], array_values(array_unique($bodies)));
    }

    /** 密钥没配：一律拒收，不放行（失败即关闭，同 ADR 0003）。 */
    public function testWithoutAchannelSecretEverythingIsRefused(): void
    {
        $response = $this->endpoint([])->handle($this->signed($this->listParams()), self::NOW);

        $this->assertSame(401, $response->status());
    }

    /** 轮换期：上一代密钥签的请求也接受。 */
    public function testApreviousInstanceSecretStillVerifies(): void
    {
        $endpoint = $this->endpoint(['another-instance-secret-0123456789abcdef', self::INSTANCE_SECRET]);

        $response = $endpoint->handle($this->signed($this->listParams()), self::NOW);

        $this->assertSame(200, $response->status());
    }

    /**
     * 未验签的请求**碰不到数据库、碰不到磁盘**。用一个「一被调用就抛」的会话表与读取器来证明：
     * 走到它们就说明顺序错了（ADR 0018 决定 6）。
     */
    public function testAnUnsignedRequestTouchesNeitherTheDatabaseNorTheDisk(): void
    {
        $endpoint = new \MjyUploadSessionEndpoint(
            new \MjyChannelAuth([self::INSTANCE_SECRET], self::INSTANCE),
            new ExplodingUploadSessionStore(\App()->getDb(), self::INSTANCE),
            new ExplodingUploadFileReader(self::$root),
            new AlwaysAllowRateLimit(),
            self::INSTANCE
        );

        $response = $endpoint->handle($this->listParams(), self::NOW);

        $this->assertSame(401, $response->status());
    }

    /** 一次只取一件：请求形状里根本没有「一批 token」这个东西，体量因此有界。 */
    public function testContentTakesExactlyOneToken(): void
    {
        $token = $this->seedUpload();
        $params = $this->signed($this->contentParams($token . ',' . $token));

        $response = $this->endpoint()->handle($params, self::NOW);

        $this->assertSame(401, $response->status());
    }
}

/** 一被调用就抛：用来证明未验签的请求没走到数据库。 */
class ExplodingUploadSessionStore extends \MjyUploadSessionStore
{
    public function fetchBound(int $surveyId, string $generation, array $responseIds): array
    {
        throw new \RuntimeException('the upload session store must not be touched before the signature check');
    }

    public function findBound(int $surveyId, string $generation, string $uploadToken): ?array
    {
        throw new \RuntimeException('the upload session store must not be touched before the signature check');
    }
}

/** 一被调用就抛：用来证明未验签的请求没走到磁盘。 */
class ExplodingUploadFileReader extends \MjyUploadFileReader
{
    public function read(int $surveyId, string $storedName): ?string
    {
        throw new \RuntimeException('the upload directory must not be touched before the signature check');
    }
}
