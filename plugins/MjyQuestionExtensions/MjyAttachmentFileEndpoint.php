<?php

/**
 * 附件取件端点（ADR 0015 增补四，契约 platform/contracts/plugin-channel-v1.md「附件取件」）。
 *
 *     GET index.php/plugins/direct?plugin=MjyQuestionExtensions&function=attachmentFile&…&sig=…
 *
 * 一次**一份**文件，应答是字节流。刻意不做「一次取一份答卷的全部附件」：
 * RemoteControl 的 `get_uploaded_files` 就是那么做的——整份答卷的文件 base64 进一个 JSON
 * 应答，内存随附件数与大小一起涨，而且其中一份不在磁盘上会让整次调用报错。
 * 一份一次既让内存有界，也让失败项可以单独重试。
 *
 * 顺序与扩展表答案端点完全相同，且同样不能改（ADR 0018 决定 6）：
 *
 *     1. 参数白名单与形状检查   —— 纯字符串操作，无 IO
 *     2. 验签 ＋ 时间戳窗口     —— 进程内一次 HMAC，无 IO
 *     ──────────────────────── 这条线以上不碰数据库、不碰任何文件
 *     3. 限流
 *     4. 定位、判大小、出字节
 *
 * 第 1、2 步的每一种失败都走 MjyChannelResponse::unauthorized() 这**一个**出口。
 * 第 3 步之后才可以带明确错误码——不持通道密钥的人走不到那里。
 *
 * 取不到的那一份一律 `404 {"error":"not_found"}`，不区分是代次不符、答卷不在、
 * 这一列里没有这个名字，还是文件已被清理：对调用方来说它们是同一件事（这一份取不到、
 * 不必重试），区分它们只会让一个持有密钥的调用方多一件能探测的事。
 */
class MjyAttachmentFileEndpoint
{
    public const FUNCTION_NAME = 'attachmentFile';
    public const PLUGIN_NAME = 'MjyQuestionExtensions';
    /** 单份上限的硬顶；调用方给的 maxBytes 再大也不超过它。 */
    public const MAX_BYTES_CEILING = 536870912;
    private const LOG_CATEGORY = 'plugin.MjyQuestionExtensions';

    /** 封闭白名单：出现任何其他参数即拒绝。 */
    private const ALLOWED_PARAMS = [
        'plugin', 'function', 'sid', 'generation', 'responseId', 'field', 'storedName', 'maxBytes', 'ts', 'sig',
    ];
    private const REQUIRED_PARAMS = [
        'plugin', 'function', 'sid', 'generation', 'responseId', 'field', 'storedName', 'maxBytes', 'ts',
    ];
    private const SID_PATTERN = '/\A[1-9][0-9]{0,9}\z/D';
    private const GENERATION_PATTERN = '/\A[A-Za-z0-9][A-Za-z0-9._-]{0,35}\z/D';
    private const RESPONSE_ID_PATTERN = '/\A[1-9][0-9]{0,9}\z/D';
    private const FIELD_PATTERN = '/\A[A-Za-z0-9_#]{1,64}\z/D';
    /** 引擎生成的存储名（`fu_…`）。不含 `/`、不以点开头，另在下面单独挡 `..`。 */
    private const STORED_NAME_PATTERN = '/\A[A-Za-z0-9][A-Za-z0-9._-]{0,254}\z/D';
    private const MAX_BYTES_PATTERN = '/\A[1-9][0-9]{0,11}\z/D';
    /**
     * 答卷表里**一定不是**上传列的那几列。定位器本来就只拿那一列去比对存储名、从不把它的内容
     * 交出去，所以今天点名 token 也只会得到 404；但把这条规矩写明**不依赖**那个间接性——
     * 将来谁把 decodeUploadedFiles() 改成"解不出就原样返回"，这里就是唯一还立着的那道。
     * 网关侧 pubgw/attachments.py 有同一份名单，两端不靠不同的推理得出同一个结论。
     */
    private const NEVER_UPLOAD_COLUMNS = ['token', 'id', 'submitdate', 'startlanguage', 'seed'];

    /** @var MjyChannelAuth */
    private $auth;

    /** @var MjyAttachmentLocator */
    private $locator;

    /** @var MjyChannelRateLimit */
    private $rateLimit;

    public function __construct(MjyChannelAuth $auth, MjyAttachmentLocator $locator, MjyChannelRateLimit $rateLimit)
    {
        $this->auth = $auth;
        $this->locator = $locator;
        $this->rateLimit = $rateLimit;
    }

    /**
     * @param array<string, mixed> $query 查询参数（含 sig）
     * @return MjyChannelResponse|MjyChannelFile 前者是 JSON 应答，后者是要流式写出的文件
     */
    public function handle(array $query, int $now)
    {
        $shape = self::checkShape($query);
        if ($shape !== '') {
            return MjyChannelResponse::unauthorized($shape);
        }
        $reason = $this->auth->check(self::strings($query), $now);
        if ($reason !== '') {
            return MjyChannelResponse::unauthorized($reason);
        }

        // ── 以下才允许触碰数据库与文件系统 ──
        $surveyId = (int) $query['sid'];
        if (!$this->rateLimit->allow($surveyId, $now)) {
            return MjyChannelResponse::rateLimited();
        }
        return $this->serve($surveyId, $query);
    }

    /**
     * @param array<string, mixed> $query
     * @return MjyChannelResponse|MjyChannelFile
     */
    private function serve(int $surveyId, array $query)
    {
        try {
            $path = $this->locator->locate(
                $surveyId,
                (string) $query['generation'],
                (int) $query['responseId'],
                (string) $query['field'],
                (string) $query['storedName']
            );
        } catch (Throwable $exception) {
            // 原因只进服务端日志：调用方拿到的永远是同一段无细节的体。
            Yii::log(
                sprintf('attachment lookup failed: %s: %s', get_class($exception), $exception->getMessage()),
                CLogger::LEVEL_ERROR,
                self::LOG_CATEGORY
            );
            return MjyChannelResponse::unavailable('locate_failed');
        }
        if ($path === null) {
            return MjyChannelResponse::notFound('attachment_gone');
        }
        $size = filesize($path);
        if ($size === false) {
            Yii::log('attachment size is unreadable', CLogger::LEVEL_ERROR, self::LOG_CATEGORY);
            return MjyChannelResponse::unavailable('size_failed');
        }
        $limit = min((int) $query['maxBytes'], self::MAX_BYTES_CEILING);
        if ($size > $limit) {
            // 绝不截断：半份文件比报错危险得多（与「结果体量绝不静默截断」同一条规矩）。
            return MjyChannelResponse::tooLarge();
        }

        return new MjyChannelFile($path, (int) $size);
    }

    /**
     * 形状检查。全部是纯字符串操作，**不碰数据库、不碰文件系统**，
     * 且每一种失败都归到统一 401。
     *
     * @param array<string, mixed> $query
     * @return string 空串＝通过；否则是给日志用的原因码
     */
    private static function checkShape(array $query): string
    {
        foreach ($query as $name => $value) {
            if (!in_array((string) $name, self::ALLOWED_PARAMS, true)) {
                return 'unknown_parameter';
            }
            // 非标量在这里就挡住：`sig[]=x` 会一路走到 (string) 转换并触发
            // "Array to string conversion" 警告，开了 display_errors 的环境会把带
            // 服务器路径的警告喷在响应体前面。
            if (!is_scalar($value)) {
                return 'non_scalar_parameter';
            }
        }
        foreach (self::REQUIRED_PARAMS as $name) {
            if (!isset($query[$name])) {
                return 'missing_parameter';
            }
        }
        if ((string) $query['plugin'] !== self::PLUGIN_NAME || (string) $query['function'] !== self::FUNCTION_NAME) {
            return 'wrong_target';
        }

        return self::checkValues($query);
    }

    /**
     * @param array<string, mixed> $query
     */
    private static function checkValues(array $query): string
    {
        $patterns = [
            'sid' => self::SID_PATTERN,
            'generation' => self::GENERATION_PATTERN,
            'responseId' => self::RESPONSE_ID_PATTERN,
            'field' => self::FIELD_PATTERN,
            'storedName' => self::STORED_NAME_PATTERN,
            'maxBytes' => self::MAX_BYTES_PATTERN,
        ];
        foreach ($patterns as $name => $pattern) {
            if (preg_match($pattern, (string) $query[$name]) !== 1) {
                return 'bad_' . $name;
            }
        }
        // 字符集已经排除了 `/`，这一条是第二道：将来字符集放宽时它仍然成立。
        if (strpos((string) $query['storedName'], '..') !== false) {
            return 'bad_storedName';
        }
        if (in_array(strtolower((string) $query['field']), self::NEVER_UPLOAD_COLUMNS, true)) {
            return 'bad_field';
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
}
