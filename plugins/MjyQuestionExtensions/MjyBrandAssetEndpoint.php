<?php

/**
 * 发布网关写入按问卷品牌资源的受限端点。
 *
 * 资源只在 RemoteControl import 已取得 sid 后写入。认证、请求查询形状以及原始 body
 * 的摘要/长度都通过之前，不读取 body、不解码 JSON、不接触数据库或文件系统。
 */
class MjyBrandAssetEndpoint
{
    public const FUNCTION_NAME = 'brandAssetsV1';
    public const PLUGIN_NAME = 'MjyQuestionExtensions';
    public const THEME_NAME = 'zh-business';
    public const MAX_ASSET_BYTES = 5242880;
    // Four 5 MiB images expand to roughly 26.7 MiB in base64; leave bounded JSON overhead.
    public const MAX_BODY_BYTES = 29360128;

    private const ALLOWED_PARAMS = ['plugin', 'function', 'sid', 'bodySha256', 'bodySize', 'ts', 'sig'];
    private const REQUIRED_PARAMS = ['plugin', 'function', 'sid', 'bodySha256', 'bodySize', 'ts'];
    private const TOP_LEVEL_FIELDS = ['version', 'theme', 'branding', 'assets'];
    private const BRANDING_FIELDS = [
        'brandingVersion', 'primaryColor', 'pageTitle', 'footerText', 'watermarkText',
        'logoFile', 'logoAlt', 'faviconFile', 'coverFile', 'backgroundFile',
    ];
    private const ASSET_FIELDS = ['slot', 'fileName', 'contentType', 'sizeBytes', 'sha256', 'contentBase64'];
    private const SID_PATTERN = '/\A[1-9][0-9]{0,9}\z/D';
    private const SHA256_PATTERN = '/\A[0-9a-f]{64}\z/D';
    private const SIZE_PATTERN = '/\A[1-9][0-9]{0,7}\z/D';
    private const COLOR_PATTERN = '/\A#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?\z/D';
    private const LANGUAGE_PATTERN = '/\A[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*\z/D';
    private const MAX_TEXT_BYTES = 4000;

    /** @var MjyChannelAuth */
    private $auth;

    /** @var string */
    private $uploadRoot;

    /** @var callable|null */
    private $applyOptions;

    /** @var callable|null */
    private $writeFile;

    public function __construct(MjyChannelAuth $auth, string $uploadRoot, ?callable $applyOptions = null, ?callable $writeFile = null)
    {
        $this->auth = $auth;
        $this->uploadRoot = rtrim($uploadRoot, '/');
        $this->applyOptions = $applyOptions;
        $this->writeFile = $writeFile;
    }

    /**
     * 从真实 HTTP 请求处理 body。读取 php://input 被故意放在 handle() 的签名验证之后。
     *
     * @param array<string, mixed> $query
     */
    public function handleHttp(array $query, int $now): MjyChannelResponse
    {
        return $this->handle($query, static function (): string {
            // Read at most one byte beyond the signed contract limit so an
            // oversized request fails the existing length check without an
            // unbounded allocation.
            $body = file_get_contents('php://input', false, null, 0, self::MAX_BODY_BYTES + 1);
            return $body === false ? '' : $body;
        }, $now);
    }

    /**
     * @param array<string, mixed> $query
     * @param callable              $readRawBody 返回原始 request body；认证前绝不调用
     */
    public function handle(array $query, callable $readRawBody, int $now): MjyChannelResponse
    {
        $shape = self::checkQueryShape($query);
        if ($shape !== '') {
            return MjyChannelResponse::unauthorized($shape);
        }
        $reason = $this->auth->check(self::strings($query), $now);
        if ($reason !== '') {
            return MjyChannelResponse::unauthorized($reason);
        }

        // 签名已经覆盖 body 的摘要与长度；直到此处才允许读取 body。
        try {
            $rawBody = $readRawBody();
        } catch (Throwable $exception) {
            return MjyChannelResponse::unavailable('body_read_failed');
        }
        if (!is_string($rawBody)
            || strlen($rawBody) !== (int) $query['bodySize']
            || !hash_equals((string) $query['bodySha256'], hash('sha256', $rawBody))) {
            return MjyChannelResponse::badRequest('body_digest_mismatch');
        }

        $parsed = $this->parseBody($rawBody);
        if ($parsed === null) {
            return MjyChannelResponse::badRequest('invalid_brand_payload');
        }

        try {
            $this->writeAndApply((int) $query['sid'], $parsed['assets'], $parsed['options']);
        } catch (Throwable $exception) {
            Yii::log(
                sprintf('brand asset apply failed: %s', get_class($exception)),
                CLogger::LEVEL_ERROR,
                'plugin.MjyQuestionExtensions'
            );
            return MjyChannelResponse::unavailable('brand_asset_apply_failed');
        }

        return MjyChannelResponse::ok('{"status":"ok"}');
    }

    /**
     * @return array{assets: array<int, array{fileName: string, bytes: string}>, options: array<string, string>}|null
     */
    private function parseBody(string $rawBody): ?array
    {
        try {
            $payload = json_decode($rawBody, true, 16, JSON_THROW_ON_ERROR);
        } catch (Throwable $exception) {
            return null;
        }
        if (!is_array($payload) || !self::hasExactKeys($payload, self::TOP_LEVEL_FIELDS)
            || $payload['version'] !== 1 || $payload['theme'] !== self::THEME_NAME
            || !is_array($payload['branding']) || !is_array($payload['assets']) || count($payload['assets']) > 4) {
            return null;
        }

        $assets = [];
        $slots = [];
        $names = [];
        foreach ($payload['assets'] as $asset) {
            $parsed = self::parseAsset($asset);
            if ($parsed === null || isset($slots[$parsed['slot']]) || isset($names[$parsed['fileName']])) {
                return null;
            }
            $slots[$parsed['slot']] = $parsed['fileName'];
            $names[$parsed['fileName']] = true;
            $assets[] = ['fileName' => $parsed['fileName'], 'bytes' => $parsed['bytes']];
        }

        $options = self::parseBranding($payload['branding'], $slots);
        if ($options === null) {
            return null;
        }
        return ['assets' => $assets, 'options' => $options];
    }

    /** @return array{slot: string, fileName: string, bytes: string}|null */
    private static function parseAsset($asset): ?array
    {
        if (!is_array($asset) || !self::hasExactKeys($asset, self::ASSET_FIELDS)) {
            return null;
        }
        $slot = $asset['slot'];
        $fileName = $asset['fileName'];
        $contentType = $asset['contentType'];
        $sizeBytes = $asset['sizeBytes'];
        $sha256 = $asset['sha256'];
        $contentBase64 = $asset['contentBase64'];
        if (!is_string($slot) || !is_string($fileName) || !is_string($contentType)
            || !is_int($sizeBytes) || $sizeBytes < 1 || $sizeBytes > self::MAX_ASSET_BYTES
            || !is_string($sha256) || preg_match(self::SHA256_PATTERN, $sha256) !== 1
            || !is_string($contentBase64)) {
            return null;
        }
        $expected = self::expectedFilename($slot, $contentType);
        if ($expected === null || !hash_equals($expected, $fileName)) {
            return null;
        }
        $bytes = base64_decode($contentBase64, true);
        if ($bytes === false || strlen($bytes) !== $sizeBytes || !hash_equals($sha256, hash('sha256', $bytes))) {
            return null;
        }
        return ['slot' => $slot, 'fileName' => $fileName, 'bytes' => $bytes];
    }

    /** @param array<string, string> $slots @return array<string, string>|null */
    private static function parseBranding(array $branding, array $slots): ?array
    {
        if (!self::hasOnlyKeys($branding, self::BRANDING_FIELDS)
            || !isset($branding['brandingVersion']) || $branding['brandingVersion'] !== 1) {
            return null;
        }
        $options = [];
        if (isset($branding['primaryColor'])) {
            if (!is_string($branding['primaryColor']) || preg_match(self::COLOR_PATTERN, $branding['primaryColor']) !== 1) {
                return null;
            }
            $options['mjybrandprimary'] = $branding['primaryColor'];
        }
        foreach (['pageTitle' => 'mjybrandtitle', 'footerText' => 'mjybrandfooter',
            'watermarkText' => 'mjybrandwatermark'] as $field => $prefix) {
            if (isset($branding[$field]) && !self::addTextOptions($options, $prefix, $branding[$field])) {
                return null;
            }
        }
        if (isset($branding['logoAlt'])) {
            if (!self::isText($branding['logoAlt'])) {
                return null;
            }
            $options['mjybrandlogoalt'] = $branding['logoAlt'];
        }
        foreach (['logoFile' => ['logoAsset', 'mjybrandlogofile'],
            'faviconFile' => ['faviconAsset', 'mjybrandfavicon'],
            'coverFile' => ['coverAsset', 'mjybrandcover'],
            'backgroundFile' => ['backgroundAsset', 'mjybrandbackground']] as $field => $target) {
            if (!isset($branding[$field])) {
                continue;
            }
            if (!is_string($branding[$field])) {
                return null;
            }
            if (!isset($slots[$target[0]])) {
                // A legacy file in the other slot was already handled by the
                // import; this endpoint only applies newly uploaded slots.
                if (preg_match('/\A[A-Za-z0-9][A-Za-z0-9._-]{0,127}\z/D', $branding[$field]) !== 1
                    || strpos($branding[$field], '..') !== false) {
                    return null;
                }
                continue;
            }
            if (!hash_equals($slots[$target[0]], $branding[$field])) {
                return null;
            }
            if ($field === 'logoFile') {
                $options['mjybrandlogo'] = 'on';
            }
            $options[$target[1]] = 'image::survey::' . $branding[$field];
        }
        // 每个已上传槽位必须在受控主题配置中被消费，避免死文件与意外语义。
        if ((isset($slots['logoAsset']) && !isset($branding['logoFile']))
            || (isset($slots['faviconAsset']) && !isset($branding['faviconFile']))
            || (isset($slots['coverAsset']) && !isset($branding['coverFile']))
            || (isset($slots['backgroundAsset']) && !isset($branding['backgroundFile']))) {
            return null;
        }
        return $options;
    }

    /** @param array<string, string> $options */
    private static function addTextOptions(array &$options, string $prefix, $value): bool
    {
        if (is_string($value)) {
            if (!self::isText($value)) {
                return false;
            }
            $options[$prefix] = $value;
            return true;
        }
        if (!is_array($value) || $value === []) {
            return false;
        }
        foreach ($value as $language => $text) {
            if (!is_string($language) || preg_match(self::LANGUAGE_PATTERN, $language) !== 1 || !self::isText($text)) {
                return false;
            }
            $options[$prefix . '__' . $language] = $text;
        }
        return true;
    }

    private static function isText($value): bool
    {
        return is_string($value) && strlen($value) <= self::MAX_TEXT_BYTES;
    }

    private function writeAndApply(int $surveyId, array $assets, array $options): void
    {
        $directory = $this->uploadRoot . '/surveys/' . $surveyId . '/images';
        if (!is_dir($directory) && !mkdir($directory, 0770, true) && !is_dir($directory)) {
            throw new RuntimeException('brand image directory unavailable');
        }
        $staged = [];
        $backups = [];
        $replaced = [];
        try {
            foreach ($assets as $asset) {
                $target = $directory . '/' . $asset['fileName'];
                $temp = $target . '.mjy-brand-' . bin2hex(random_bytes(8)) . '.tmp';
                $this->writeTemp($temp, $asset['bytes']);
                $staged[$target] = $temp;
            }
            foreach ($staged as $target => $temp) {
                if (file_exists($target)) {
                    $backup = $target . '.mjy-brand-' . bin2hex(random_bytes(8)) . '.bak';
                    if (!rename($target, $backup)) {
                        throw new RuntimeException('brand backup failed');
                    }
                    $backups[$target] = $backup;
                }
                if (!rename($temp, $target)) {
                    throw new RuntimeException('brand replace failed');
                }
                $replaced[] = $target;
                unset($staged[$target]);
            }
            $this->applyThemeOptions($surveyId, $options);
            foreach ($backups as $backup) {
                @unlink($backup);
            }
        } catch (Throwable $exception) {
            foreach ($staged as $temp) {
                @unlink($temp);
            }
            // 没有旧文件的目标也必须清掉，否则一次半途失败会留下本不该可见的半套品牌。
            foreach ($replaced as $target) {
                @unlink($target);
            }
            foreach ($backups as $target => $backup) {
                @rename($backup, $target);
            }
            throw $exception;
        }
    }

    private function writeTemp(string $path, string $bytes): void
    {
        if ($this->writeFile !== null) {
            call_user_func($this->writeFile, $path, $bytes);
            return;
        }
        $written = file_put_contents($path, $bytes, LOCK_EX);
        if ($written === false || $written !== strlen($bytes)) {
            @unlink($path);
            throw new RuntimeException('brand temp write failed');
        }
    }

    /** @param array<string, string> $updates */
    private function applyThemeOptions(int $surveyId, array $updates): void
    {
        if ($this->applyOptions !== null) {
            call_user_func($this->applyOptions, $surveyId, $updates);
            return;
        }
        $configuration = TemplateConfiguration::getInstanceFromSurveyId($surveyId, self::THEME_NAME);
        if (!$configuration instanceof TemplateConfiguration) {
            throw new RuntimeException('theme configuration unavailable');
        }
        $existing = json_decode((string) $configuration->options, true);
        $existing = is_array($existing) ? $existing : [];
        $configuration->template_name = self::THEME_NAME;
        $configuration->options = json_encode(self::mergeThemeOptions($existing, $updates), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
        if ($configuration->options === false || !$configuration->save()) {
            throw new RuntimeException('theme configuration update failed');
        }
    }

    /** @param array<string, mixed> $existing @param array<string, string> $updates
     *  @return array<string, mixed>
     */
    private static function mergeThemeOptions(array $existing, array $updates): array
    {
        $replaceLogo = isset($updates['mjybrandlogofile']);
        $replaceFavicon = isset($updates['mjybrandfavicon']);
        $replaceCover = isset($updates['mjybrandcover']);
        $replaceBackground = isset($updates['mjybrandbackground']);
        foreach (array_keys($existing) as $key) {
            if (preg_match('/\Amjybrand(?:primary|logo|logofile|logoalt|favicon|cover|background|title(?:__.*)?|footer(?:__.*)?|watermark(?:__.*)?)\z/D', (string) $key) !== 1) {
                continue;
            }
            // The import keeps a legacy image slot when only the other slot
            // has a new asset. Do not erase that imported image reference.
            if (($key === 'mjybrandlogo' || $key === 'mjybrandlogofile') && !$replaceLogo) {
                continue;
            }
            if ($key === 'mjybrandfavicon' && !$replaceFavicon) {
                continue;
            }
            if ($key === 'mjybrandcover' && !$replaceCover) {
                continue;
            }
            if ($key === 'mjybrandbackground' && !$replaceBackground) {
                continue;
            }
            unset($existing[$key]);
        }
        return $updates + $existing;
    }

    private static function expectedFilename(string $slot, string $contentType): ?string
    {
        $extensions = ['image/png' => 'png', 'image/jpeg' => 'jpg', 'image/gif' => 'gif', 'image/webp' => 'webp'];
        if (!isset($extensions[$contentType])) {
            return null;
        }
        if ($slot === 'logoAsset') {
            return 'mjy-brand-logo.' . $extensions[$contentType];
        }
        if ($slot === 'faviconAsset') {
            return 'mjy-brand-favicon.' . $extensions[$contentType];
        }
        if ($slot === 'coverAsset') {
            return 'mjy-brand-cover.' . $extensions[$contentType];
        }
        if ($slot === 'backgroundAsset') {
            return 'mjy-brand-background.' . $extensions[$contentType];
        }
        return null;
    }

    /** @param array<string, mixed> $query */
    private static function checkQueryShape(array $query): string
    {
        if (!self::hasOnlyKeys($query, self::ALLOWED_PARAMS)) {
            return 'unknown_parameter';
        }
        foreach ($query as $value) {
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
        if (preg_match(self::SID_PATTERN, (string) $query['sid']) !== 1
            || preg_match(self::SHA256_PATTERN, (string) $query['bodySha256']) !== 1
            || preg_match(self::SIZE_PATTERN, (string) $query['bodySize']) !== 1
            || (int) $query['bodySize'] > self::MAX_BODY_BYTES) {
            return 'bad_body_identity';
        }
        return '';
    }

    /** @param array<mixed> $value @param string[] $allowed */
    private static function hasOnlyKeys(array $value, array $allowed): bool
    {
        foreach ($value as $key => $_) {
            if (!is_string($key) || !in_array($key, $allowed, true)) {
                return false;
            }
        }
        return true;
    }

    /** @param array<mixed> $value @param string[] $expected */
    private static function hasExactKeys(array $value, array $expected): bool
    {
        return count($value) === count($expected) && self::hasOnlyKeys($value, $expected);
    }

    /** @param array<string, mixed> $query @return array<string, string> */
    private static function strings(array $query): array
    {
        $strings = [];
        foreach ($query as $name => $value) {
            $strings[(string) $name] = (string) $value;
        }
        return $strings;
    }
}
