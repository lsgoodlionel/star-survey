<?php

/**
 * 扩展表答案的读取端点（ADR 0018，契约 platform/contracts/plugin-channel-v1.md）。
 *
 *     GET index.php/plugins/direct?plugin=MjyQuestionExtensions&function=extensionAnswers&…&sig=…
 *
 * 顺序是本端点的安全核心，不能改（ADR 0018 决定 6）：
 *
 *     1. 参数白名单与形状检查   —— 纯字符串操作，无 IO
 *     2. 验签 ＋ 时间戳窗口     —— 进程内一次 HMAC，无 IO
 *     ──────────────────────── 这条线以上不碰数据库、不碰任何作答
 *     3. 限流
 *     4. 读副表，出 JSON
 *
 * 第 1、2 步的每一种失败都走 MjyChannelResponse::unauthorized() 这**一个**出口，
 * 状态与响应体逐字相同，原因只作为 reason() 交给日志。任何按原因分叉的应答都是
 * 预言机（ADR 0014 飞书回调那次）。第 3 步之后才可以带明确错误码——
 * 不持通道密钥的人走不到那里，区分只有我们自己的网关看得见。
 */
class MjyExtensionAnswerEndpoint
{
    public const FUNCTION_NAME = 'extensionAnswers';
    public const PLUGIN_NAME = 'MjyQuestionExtensions';
    public const MAX_RESPONSE_IDS = 200;
    public const MAX_QUESTION_CODES = 50;
    public const MAX_CELLS = 20000;

    /** 封闭白名单：出现任何其他参数即拒绝。 */
    private const ALLOWED_PARAMS = [
        'plugin', 'function', 'sid', 'generation', 'responseIds', 'questionCodes', 'ts', 'sig',
    ];
    private const REQUIRED_PARAMS = [
        'plugin', 'function', 'sid', 'generation', 'responseIds', 'questionCodes', 'ts',
    ];
    private const SID_PATTERN = '/\A[1-9][0-9]{0,9}\z/D';
    private const GENERATION_PATTERN = '/\A[A-Za-z0-9][A-Za-z0-9._-]{0,35}\z/D';
    private const RESPONSE_ID_PATTERN = '/\A[1-9][0-9]{0,9}\z/D';
    private const QUESTION_CODE_PATTERN = '/\A[A-Za-z0-9_]{1,64}\z/D';

    /** @var MjyChannelAuth */
    private $auth;

    /** @var MjyExtensionAnswerReader */
    private $reader;

    /** @var MjyChannelRateLimit */
    private $rateLimit;

    /** @var string */
    private $engineInstanceId;

    public function __construct(
        MjyChannelAuth $auth,
        MjyExtensionAnswerReader $reader,
        MjyChannelRateLimit $rateLimit,
        string $engineInstanceId
    ) {
        $this->auth = $auth;
        $this->reader = $reader;
        $this->rateLimit = $rateLimit;
        $this->engineInstanceId = $engineInstanceId;
    }

    /**
     * @param array<string, mixed> $query 查询参数（含 sig）
     */
    public function handle(array $query, int $now): MjyChannelResponse
    {
        $shape = self::checkShape($query);
        if ($shape !== '') {
            return MjyChannelResponse::unauthorized($shape);
        }
        $reason = $this->auth->check(self::strings($query), $now);
        if ($reason !== '') {
            return MjyChannelResponse::unauthorized($reason);
        }

        // ── 以下才允许触碰数据库 ──
        $surveyId = (int) $query['sid'];
        if (!$this->rateLimit->allow($surveyId, $now)) {
            return MjyChannelResponse::rateLimited();
        }
        return $this->readPage(
            $surveyId,
            (string) $query['generation'],
            self::responseIds($query),
            self::questionCodes($query)
        );
    }

    /**
     * @param int[]    $responseIds
     * @param string[] $questionCodes
     */
    private function readPage(int $surveyId, string $generation, array $responseIds, array $questionCodes): MjyChannelResponse
    {
        try {
            $answers = $this->reader->read($surveyId, $generation, $responseIds, $questionCodes, self::MAX_CELLS);
        } catch (Throwable $exception) {
            // 原因只进服务端日志：调用方拿到的永远是同一段无细节的体。
            return MjyChannelResponse::unavailable('read_failed');
        }
        if ($answers === null) {
            return MjyChannelResponse::pageTooLarge();
        }

        return MjyChannelResponse::ok($this->encode($surveyId, $generation, $answers));
    }

    /**
     * @param array<int, array<string, array{structureVersion: string, isValid: bool, rows: array}>> $answers
     */
    private function encode(int $surveyId, string $generation, array $answers): string
    {
        $payload = [];
        foreach ($answers as $responseId => $questions) {
            foreach ($questions as $questionCode => $entry) {
                // errors 列刻意不出现：它是给作答者看的拒绝理由，不是导出内容。
                $payload[(string) $responseId][$questionCode] = [
                    'structureVersion' => $entry['structureVersion'],
                    'isValid' => $entry['isValid'],
                    'rows' => $entry['rows'],
                ];
            }
        }

        return (string) json_encode([
            'plugin' => self::PLUGIN_NAME,
            'engineInstanceId' => $this->engineInstanceId,
            'surveyId' => $surveyId,
            'generation' => $generation,
            // 契约里 answers 永远是对象，键是答卷号的十进制字符串。
            // 必须显式转对象：PHP 会把 "7" 这样的键悄悄变回整数，
            // 于是 json_encode 要不要输出成数组就取决于键恰好是不是 0,1,2,…——不能依赖这种巧合。
            'answers' => (object) $payload,
        ], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    }

    /**
     * 形状检查。全部是纯字符串操作，**不碰数据库**，且每一种失败都归到统一 401。
     *
     * @param array<string, mixed> $query
     * @return string 空串＝通过；否则是给日志用的原因码
     */
    private static function checkShape(array $query): string
    {
        foreach (array_keys($query) as $name) {
            if (!in_array((string) $name, self::ALLOWED_PARAMS, true)) {
                return 'unknown_parameter';
            }
        }
        foreach (self::REQUIRED_PARAMS as $name) {
            if (!isset($query[$name]) || !is_scalar($query[$name])) {
                return 'missing_parameter';
            }
        }
        if ((string) $query['plugin'] !== self::PLUGIN_NAME || (string) $query['function'] !== self::FUNCTION_NAME) {
            return 'wrong_target';
        }
        if (preg_match(self::SID_PATTERN, (string) $query['sid']) !== 1) {
            return 'bad_survey_id';
        }
        if (preg_match(self::GENERATION_PATTERN, (string) $query['generation']) !== 1) {
            return 'bad_generation';
        }

        return self::checkLists($query);
    }

    /**
     * @param array<string, mixed> $query
     */
    private static function checkLists(array $query): string
    {
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
        $codes = explode(',', (string) $query['questionCodes']);
        if (count($codes) < 1 || count($codes) > self::MAX_QUESTION_CODES) {
            return 'too_many_question_codes';
        }
        foreach ($codes as $code) {
            if (preg_match(self::QUESTION_CODE_PATTERN, $code) !== 1) {
                return 'bad_question_code';
            }
        }
        if (count(array_unique($codes)) !== count($codes)) {
            return 'duplicate_question_codes';
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

    /**
     * @param array<string, mixed> $query
     * @return string[]
     */
    private static function questionCodes(array $query): array
    {
        return explode(',', (string) $query['questionCodes']);
    }
}
