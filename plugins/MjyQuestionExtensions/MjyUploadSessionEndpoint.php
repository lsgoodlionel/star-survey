<?php

/**
 * 作答者上传的读取端点（ADR 0019 决定 8，走 ADR 0018 那条签名通道）：
 *
 *     GET index.php/plugins/direct?plugin=MjyQuestionExtensions&function=uploadSessions&…&sig=…
 *     GET index.php/plugins/direct?plugin=MjyQuestionExtensions&function=uploadContent&…&sig=…
 *
 * 顺序与 MjyExtensionAnswerEndpoint 完全一致，不能改（ADR 0018 决定 6）：
 *
 *     1. 参数白名单与形状检查   —— 纯字符串操作，无 IO
 *     2. 验签 ＋ 时间戳窗口     —— 进程内一次 HMAC，无 IO
 *     ──────────────────────── 这条线以上不碰数据库、不碰磁盘
 *     3. 限流
 *     4. 读会话表 / 读上传目录，出 JSON
 *
 * **清单只来自上传会话表。** 答卷字段里那张文件清单一个字都不参与：它是作答者可控的
 * POST 数据，而会话行是引擎在 beforeProcessFileUpload 里自己写的。本端点因此
 * **没有**接受文件名／大小／类型的入参形状——不是「传了也忽略」，是根本没有那个参数，
 * 白名单会把它当未知参数归到统一 401。
 *
 * **一次只取一件。** `uploadContent` 收单个 token，不收列表：作答者上传是整块二进制，
 * 一次一件才让单次应答的体量有界（这正是 ADR 0015 那条「整卷 base64」遗留的反面教材）。
 */
class MjyUploadSessionEndpoint
{
    public const FUNCTION_LIST = 'uploadSessions';
    public const FUNCTION_CONTENT = 'uploadContent';
    public const PLUGIN_NAME = 'MjyQuestionExtensions';
    public const MAX_RESPONSE_IDS = 200;
    /** 单件上传的字节上限；与平台的 platform.asset.max-bytes 同量级，两端各判各的。 */
    public const MAX_CONTENT_BYTES = 16777216;
    private const LOG_CATEGORY = 'plugin.MjyQuestionExtensions';

    /** 封闭白名单，两个函数各一份：出现任何其他参数即拒绝。 */
    private const LIST_PARAMS = ['plugin', 'function', 'sid', 'generation', 'responseIds', 'ts', 'sig'];
    private const CONTENT_PARAMS = ['plugin', 'function', 'sid', 'generation', 'uploadToken', 'ts', 'sig'];

    private const SID_PATTERN = '/\A[1-9][0-9]{0,9}\z/D';
    private const GENERATION_PATTERN = '/\A[A-Za-z0-9][A-Za-z0-9._-]{0,35}\z/D';
    private const RESPONSE_ID_PATTERN = '/\A[1-9][0-9]{0,9}\z/D';
    private const TOKEN_PATTERN = '/\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\z/D';

    /** @var MjyChannelAuth */
    private $auth;

    /** @var MjyUploadSessionStore */
    private $sessions;

    /** @var MjyUploadFileReader */
    private $files;

    /** @var MjyChannelRateLimit */
    private $rateLimit;

    /** @var string */
    private $engineInstanceId;

    public function __construct(
        MjyChannelAuth $auth,
        MjyUploadSessionStore $sessions,
        MjyUploadFileReader $files,
        MjyChannelRateLimit $rateLimit,
        string $engineInstanceId
    ) {
        $this->auth = $auth;
        $this->sessions = $sessions;
        $this->files = $files;
        $this->rateLimit = $rateLimit;
        $this->engineInstanceId = $engineInstanceId;
    }

    /**
     * @param array<string, mixed> $query 查询参数（含 sig）
     */
    public function handle(array $query, int $now): MjyChannelResponse
    {
        $isContent = isset($query['function']) && is_scalar($query['function'])
            && (string) $query['function'] === self::FUNCTION_CONTENT;
        $shape = $isContent ? self::checkContentShape($query) : self::checkListShape($query);
        if ($shape !== '') {
            return MjyChannelResponse::unauthorized($shape);
        }
        $reason = $this->auth->check(self::strings($query), $now);
        if ($reason !== '') {
            return MjyChannelResponse::unauthorized($reason);
        }

        // ── 以下才允许触碰数据库与磁盘 ──
        $surveyId = (int) $query['sid'];
        if (!$this->rateLimit->allow($surveyId, $now)) {
            return MjyChannelResponse::rateLimited();
        }
        $generation = (string) $query['generation'];
        if ($isContent) {
            return $this->readContent($surveyId, $generation, (string) $query['uploadToken']);
        }
        return $this->readList($surveyId, $generation, self::responseIds($query));
    }

    /**
     * 一页答卷的上传清单（不含字节）。
     *
     * 只列**已绑定**的会话：未绑定的那些字节还在临时目录里、文件名还会再变一次
     * （引擎提交时会重命名成一个全新的 fu_，且不派发任何事件），此刻取不出可靠的东西。
     *
     * @param int[] $responseIds
     */
    private function readList(int $surveyId, string $generation, array $responseIds): MjyChannelResponse
    {
        try {
            $rows = $this->sessions->fetchBound($surveyId, $generation, $responseIds);
        } catch (Throwable $exception) {
            return $this->failed('upload session list failed', $exception);
        }
        $uploads = [];
        foreach ($rows as $row) {
            $uploads[(string) (int) $row['response_id']][] = [
                'uploadToken' => (string) $row['upload_token'],
                'questionCode' => (string) $row['question_code'],
                // 原始文件名只是线索：平台拿它当元数据留档，绝不用它拼路径。
                'originalName' => (string) ($row['original_name'] ?? ''),
                'extension' => (string) ($row['extension'] ?? ''),
                'sizeBytes' => (int) $row['size_bytes'],
            ];
        }

        return $this->encoded([
            'surveyId' => $surveyId,
            'generation' => $generation,
            // 契约里 uploads 永远是对象，键是答卷号的十进制字符串。必须显式转对象：
            // PHP 会把 "7" 这样的键悄悄变回整数，于是输出成数组还是对象取决于
            // 键恰好是不是 0,1,2,…——不能依赖这种巧合。
            'uploads' => (object) $uploads,
        ]);
    }

    /** 一件上传的字节。按 (sid, 代次, token) 三者定位，光有 token 改不到别的代次去。 */
    private function readContent(int $surveyId, string $generation, string $token): MjyChannelResponse
    {
        try {
            $row = $this->sessions->findBound($surveyId, $generation, $token);
        } catch (Throwable $exception) {
            return $this->failed('upload session lookup failed', $exception);
        }
        if ($row === null) {
            // 验签之后才可能走到这里，所以区分「有没有」不构成预言机（ADR 0018 决定 5）。
            return MjyChannelResponse::notFound('unknown_upload_token');
        }
        $bytes = $this->files->read($surveyId, (string) ($row['stored_name'] ?? ''));
        if ($bytes === null) {
            // 会话行在、字节没了：报错。回一个「看着完整、其实空的」应答会让平台
            // 存下一件空资产，而没有任何人察觉——静默的数据缺失比报错危险得多。
            Yii::log(
                sprintf('upload content unreadable for token %s on survey %d', $token, $surveyId),
                CLogger::LEVEL_ERROR,
                self::LOG_CATEGORY
            );
            return MjyChannelResponse::unavailable('content_unreadable');
        }
        if (strlen($bytes) > self::MAX_CONTENT_BYTES) {
            // 绝不静默截断：截断会让平台存下一件半截文件而没人察觉。
            return MjyChannelResponse::pageTooLarge();
        }

        return $this->encoded([
            'surveyId' => $surveyId,
            'generation' => $generation,
            'uploadToken' => $token,
            'questionCode' => (string) $row['question_code'],
            'responseId' => (int) $row['response_id'],
            'originalName' => (string) ($row['original_name'] ?? ''),
            'extension' => (string) ($row['extension'] ?? ''),
            'sizeBytes' => strlen($bytes),
            'contentBase64' => base64_encode($bytes),
        ]);
    }

    /**
     * @param array<string, mixed> $payload
     */
    private function encoded(array $payload): MjyChannelResponse
    {
        $json = json_encode(
            ['plugin' => self::PLUGIN_NAME, 'engineInstanceId' => $this->engineInstanceId] + $payload,
            JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES
        );
        if ($json === false) {
            // 编不出来就报错（原始文件名里可能有非法 UTF-8）。
            Yii::log(
                'upload session payload could not be encoded as JSON (invalid UTF-8 in a file name?)',
                CLogger::LEVEL_ERROR,
                self::LOG_CATEGORY
            );
            return MjyChannelResponse::unavailable('encode_failed');
        }

        return MjyChannelResponse::ok($json);
    }

    private function failed(string $what, Throwable $exception): MjyChannelResponse
    {
        // 原因只进服务端日志；不带请求参数、不带文件名、不带字节。
        Yii::log(
            sprintf('%s: %s: %s', $what, get_class($exception), $exception->getMessage()),
            CLogger::LEVEL_ERROR,
            self::LOG_CATEGORY
        );
        return MjyChannelResponse::unavailable('read_failed');
    }

    /**
     * @param array<string, mixed> $query
     * @return string 空串＝通过；否则是给日志用的原因码
     */
    private static function checkListShape(array $query): string
    {
        $common = self::checkCommon($query, self::LIST_PARAMS, self::FUNCTION_LIST);
        if ($common !== '') {
            return $common;
        }
        $ids = explode(',', (string) $query['responseIds']);
        if (count($ids) < 1 || count($ids) > self::MAX_RESPONSE_IDS) {
            return 'too_many_response_ids';
        }
        $previous = 0;
        foreach ($ids as $id) {
            if (preg_match(self::RESPONSE_ID_PATTERN, $id) !== 1) {
                return 'bad_response_id';
            }
            // 严格升序（因此也互不相同）：同一组答卷号只有一种写法，签名串才规范。
            if ((int) $id <= $previous) {
                return 'unordered_response_ids';
            }
            $previous = (int) $id;
        }

        return '';
    }

    /**
     * @param array<string, mixed> $query
     */
    private static function checkContentShape(array $query): string
    {
        $common = self::checkCommon($query, self::CONTENT_PARAMS, self::FUNCTION_CONTENT);
        if ($common !== '') {
            return $common;
        }
        // 单个 UUID，不是列表：一次只取一件，体量因此有界。
        if (preg_match(self::TOKEN_PATTERN, (string) $query['uploadToken']) !== 1) {
            return 'bad_upload_token';
        }

        return '';
    }

    /**
     * 两个函数共用的形状检查。全部是纯字符串操作，**不碰数据库、不碰磁盘**，
     * 且每一种失败都归到统一 401。
     *
     * @param array<string, mixed> $query
     * @param string[]             $allowed
     */
    private static function checkCommon(array $query, array $allowed, string $function): string
    {
        foreach ($query as $name => $value) {
            if (!in_array((string) $name, $allowed, true)) {
                return 'unknown_parameter';
            }
            // 每个值都要在这里挡住非标量：`sig[]=x` 这样的数组会一路走到 (string) 转换，
            // 触发 PHP 的 "Array to string conversion" 警告；开了 display_errors 的环境
            // 会把带服务器路径的警告喷在响应体前面，既破坏「逐字相同」又泄露路径。
            if (!is_scalar($value)) {
                return 'non_scalar_parameter';
            }
        }
        foreach ($allowed as $name) {
            if ($name === 'sig') {
                continue;
            }
            if (!isset($query[$name])) {
                return 'missing_parameter';
            }
        }
        if ((string) $query['plugin'] !== self::PLUGIN_NAME || (string) $query['function'] !== $function) {
            return 'wrong_target';
        }
        if (preg_match(self::SID_PATTERN, (string) $query['sid']) !== 1) {
            return 'bad_survey_id';
        }
        if (preg_match(self::GENERATION_PATTERN, (string) $query['generation']) !== 1) {
            return 'bad_generation';
        }

        return '';
    }

    /**
     * @param array<string, mixed> $query
     * @return array<string, string>
     */
    private static function strings(array $query): array
    {
        $strings = [];
        foreach ($query as $name => $value) {
            $strings[(string) $name] = (string) $value;
        }

        return $strings;
    }

    /**
     * @param array<string, mixed> $query
     * @return int[]
     */
    private static function responseIds(array $query): array
    {
        return array_map('intval', explode(',', (string) $query['responseIds']));
    }
}
