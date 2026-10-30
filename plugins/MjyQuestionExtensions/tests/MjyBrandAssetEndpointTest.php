<?php

namespace ls\tests;

require_once dirname(__DIR__) . '/MjyChannelAuth.php';
require_once dirname(__DIR__) . '/MjyChannelResponse.php';
require_once dirname(__DIR__) . '/MjyBrandAssetEndpoint.php';

/**
 * 发布网关写入按问卷品牌资源的受限端点。
 *
 * 这些用例刻意不借助真实 HTTP：端点接收一个惰性 body reader，使我们能证明签名
 * 未通过时连 php://input 都不会读取。写入器和主题配置应用器同样可替换，以覆盖
 * 原子失败分支，而不把测试变成对宿主磁盘权限的偶然依赖。
 */
class MjyBrandAssetEndpointTest extends TestBaseClass
{
    private const INSTANCE = 'hd-engine-01';
    private const INSTANCE_SECRET = '0123456789abcdef0123456789abcdef-instance';
    private const CHANNEL_SECRET = '204745a72bcb796d18bf2df097af7efea324f61077971e41b50189196036ff31';
    private const NOW = 1800000000;
    private const SURVEY_ID = 991043;

    /** @var string */
    private static $root;

    public static function setUpBeforeClass(): void
    {
        parent::setUpBeforeClass();
        self::$root = sys_get_temp_dir() . '/mjy-brand-asset-endpoint-test';
    }

    protected function setUp(): void
    {
        parent::setUp();
        $dir = self::$root . '/surveys/' . self::SURVEY_ID . '/images';
        if (is_dir($dir)) {
            foreach ((array) glob($dir . '/*') as $file) {
                @unlink($file);
            }
        }
    }

    private function endpoint(?callable $applyOptions = null, ?callable $atomicWriter = null): \MjyBrandAssetEndpoint
    {
        return new \MjyBrandAssetEndpoint(
            new \MjyChannelAuth([self::INSTANCE_SECRET], self::INSTANCE),
            self::$root,
            $applyOptions,
            $atomicWriter
        );
    }

    private function body(array $overrides = []): string
    {
        $logo = 'logo-bytes';
        $favicon = 'favicon-bytes';
        $payload = [
            'version' => 1,
            'theme' => 'zh-business',
            'branding' => [
                'brandingVersion' => 1,
                'primaryColor' => '#1f6feb',
                'pageTitle' => ['zh-Hans' => '客户回访', 'en' => 'Customer feedback'],
                'footerText' => '版权所有',
                'logoFile' => 'mjy-brand-logo.png',
                'logoAlt' => '客户品牌',
                'faviconFile' => 'mjy-brand-favicon.png',
            ],
            'assets' => [
                $this->asset('logoAsset', 'mjy-brand-logo.png', 'image/png', $logo),
                $this->asset('faviconAsset', 'mjy-brand-favicon.png', 'image/png', $favicon),
            ],
        ];
        foreach ($overrides as $key => $value) {
            $payload[$key] = $value;
        }
        return json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    }

    private function asset(string $slot, string $filename, string $contentType, string $bytes): array
    {
        return [
            'slot' => $slot,
            'fileName' => $filename,
            'contentType' => $contentType,
            'sizeBytes' => strlen($bytes),
            'sha256' => hash('sha256', $bytes),
            'contentBase64' => base64_encode($bytes),
        ];
    }

    private function params(string $body, array $overrides = []): array
    {
        return $overrides + [
            'plugin' => 'MjyQuestionExtensions',
            'function' => 'brandAssetsV1',
            'sid' => (string) self::SURVEY_ID,
            'bodySha256' => hash('sha256', $body),
            'bodySize' => (string) strlen($body),
            'ts' => (string) self::NOW,
        ];
    }

    private function signed(array $params): array
    {
        $canonical = \MjyChannelAuth::canonicalQuery($params);
        $params['sig'] = hash_hmac('sha256', $params['ts'] . '.' . $canonical, self::CHANNEL_SECRET);
        return $params;
    }

    public function testUnsignedRequestReadsNeitherBodyNorDisk(): void
    {
        $response = $this->endpoint()->handle(
            $this->params($this->body()),
            static function (): string {
                throw new \RuntimeException('body must not be read before authentication');
            },
            self::NOW
        );

        $this->assertSame(401, $response->status());
        $this->assertSame('{"error":"unauthorized"}', $response->body());
    }

    public function testDigestMismatchIsRejectedBeforeJsonAndWritesNothing(): void
    {
        $body = $this->body();
        $params = $this->signed($this->params($body, ['bodySha256' => str_repeat('0', 64)]));
        $readerCalls = 0;
        $response = $this->endpoint()->handle($params, static function () use (&$readerCalls, $body): string {
            $readerCalls++;
            return $body;
        }, self::NOW);

        $this->assertSame(400, $response->status());
        $this->assertSame('{"error":"bad_request"}', $response->body());
        $this->assertSame(1, $readerCalls);
        $this->assertSame([], glob(self::$root . '/surveys/' . self::SURVEY_ID . '/images/*') ?: []);
    }

    public function testEveryUnauthenticatedQueryFailureHasTheSameResponse(): void
    {
        $body = $this->body();
        $valid = $this->signed($this->params($body));
        $cases = [
            'missing signature' => array_diff_key($valid, ['sig' => null]),
            'unknown query key' => $valid + ['unexpected' => '1'],
            'array signature' => array_merge($valid, ['sig' => ['x']]),
            'stale timestamp' => $this->signed($this->params($body, ['ts' => (string) (self::NOW - 301)])),
        ];
        foreach ($cases as $name => $params) {
            $response = $this->endpoint()->handle($params, static function (): string {
                throw new \RuntimeException('unauthenticated request must not read the body');
            }, self::NOW);
            $this->assertSame(401, $response->status(), $name);
            $this->assertSame('{"error":"unauthorized"}', $response->body(), $name);
        }
    }

    public function testRejectsUnexpectedOrUnsafePayloadBeforeWrites(): void
    {
        $payload = json_decode($this->body(), true);
        $payload['branding']['logoFile'] = '../../secret.png';
        $body = json_encode($payload);
        $response = $this->endpoint()->handle(
            $this->signed($this->params($body)),
            static function () use ($body): string { return $body; },
            self::NOW
        );

        $this->assertSame(400, $response->status());
        $this->assertSame([], glob(self::$root . '/surveys/' . self::SURVEY_ID . '/images/*') ?: []);
    }

    public function testWritesOnlyFixedFilesAndAppliesSafeThemeOptions(): void
    {
        $body = $this->body();
        $captured = null;
        $response = $this->endpoint(static function (int $sid, array $options) use (&$captured): void {
            $captured = [$sid, $options];
        })->handle(
            $this->signed($this->params($body)),
            static function () use ($body): string { return $body; },
            self::NOW
        );

        $this->assertSame(200, $response->status());
        $this->assertSame('logo-bytes', file_get_contents(self::$root . '/surveys/' . self::SURVEY_ID . '/images/mjy-brand-logo.png'));
        $this->assertSame('favicon-bytes', file_get_contents(self::$root . '/surveys/' . self::SURVEY_ID . '/images/mjy-brand-favicon.png'));
        $this->assertSame(self::SURVEY_ID, $captured[0]);
        $this->assertSame('#1f6feb', $captured[1]['mjybrandprimary']);
        $this->assertSame('image::survey::mjy-brand-logo.png', $captured[1]['mjybrandlogofile']);
        $this->assertSame('image::survey::mjy-brand-favicon.png', $captured[1]['mjybrandfavicon']);
        $this->assertSame('客户回访', $captured[1]['mjybrandtitle__zh-Hans']);
        $this->assertArrayNotHasKey('logoAsset', $captured[1]);
    }

    public function testWritesCoverAndBackgroundAndAppliesLocalizedWatermark(): void
    {
        $payload = json_decode($this->body(), true);
        $payload['branding']['coverFile'] = 'mjy-brand-cover.png';
        $payload['branding']['backgroundFile'] = 'mjy-brand-background.png';
        $payload['branding']['watermarkText'] = ['zh-Hans' => '内部资料', 'en' => 'Internal'];
        $payload['assets'][] = $this->asset('coverAsset', 'mjy-brand-cover.png', 'image/png', 'cover-bytes');
        $payload['assets'][] = $this->asset('backgroundAsset', 'mjy-brand-background.png', 'image/png', 'background-bytes');
        $body = json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
        $captured = null;
        $response = $this->endpoint(static function (int $sid, array $options) use (&$captured): void {
            $captured = $options;
        })->handle($this->signed($this->params($body)), static function () use ($body): string {
            return $body;
        }, self::NOW);

        $this->assertSame(200, $response->status());
        $this->assertSame('cover-bytes', file_get_contents(self::$root . '/surveys/' . self::SURVEY_ID . '/images/mjy-brand-cover.png'));
        $this->assertSame('background-bytes', file_get_contents(self::$root . '/surveys/' . self::SURVEY_ID . '/images/mjy-brand-background.png'));
        $this->assertSame('image::survey::mjy-brand-cover.png', $captured['mjybrandcover']);
        $this->assertSame('image::survey::mjy-brand-background.png', $captured['mjybrandbackground']);
        $this->assertSame('内部资料', $captured['mjybrandwatermark__zh-Hans']);
    }

    public function testNewLogoCanCoexistWithLegacyFaviconFile(): void
    {
        $payload = json_decode($this->body(), true);
        $payload['branding']['faviconFile'] = 'legacy-favicon.png';
        $payload['assets'] = [$payload['assets'][0]];
        $body = json_encode($payload);
        $captured = null;
        $response = $this->endpoint(static function (int $sid, array $options) use (&$captured): void {
            $captured = $options;
        })->handle($this->signed($this->params($body)), static function () use ($body): string {
            return $body;
        }, self::NOW);

        $this->assertSame(200, $response->status());
        $this->assertSame('image::survey::mjy-brand-logo.png', $captured['mjybrandlogofile']);
        $this->assertArrayNotHasKey('mjybrandfavicon', $captured);
    }

    public function testNewFaviconCanCoexistWithLegacyLogoFile(): void
    {
        $payload = json_decode($this->body(), true);
        $payload['branding']['logoFile'] = 'legacy-logo.png';
        $payload['assets'] = [$payload['assets'][1]];
        $body = json_encode($payload);
        $captured = null;
        $response = $this->endpoint(static function (int $sid, array $options) use (&$captured): void {
            $captured = $options;
        })->handle($this->signed($this->params($body)), static function () use ($body): string {
            return $body;
        }, self::NOW);

        $this->assertSame(200, $response->status());
        $this->assertSame('image::survey::mjy-brand-favicon.png', $captured['mjybrandfavicon']);
        $this->assertArrayNotHasKey('mjybrandlogofile', $captured);
    }

    public function testMixedSlotsKeepTheImportedLegacyImageOption(): void
    {
        $merge = new \ReflectionMethod(\MjyBrandAssetEndpoint::class, 'mergeThemeOptions');
        $merge->setAccessible(true);
        $existing = [
            'mjybrandlogo' => 'on',
            'mjybrandlogofile' => 'image::survey::legacy-logo.png',
            'mjybrandfavicon' => 'image::survey::legacy-favicon.png',
            'mjybrandtitle' => 'stale title',
            'unrelated' => 'keep',
        ];
        $newLogo = $merge->invoke(null, $existing, [
            'mjybrandlogofile' => 'image::survey::mjy-brand-logo.png',
            'mjybrandlogo' => 'on',
            'mjybrandtitle' => 'new title',
        ]);
        $this->assertSame('image::survey::mjy-brand-logo.png', $newLogo['mjybrandlogofile']);
        $this->assertSame('image::survey::legacy-favicon.png', $newLogo['mjybrandfavicon']);
        $this->assertSame('new title', $newLogo['mjybrandtitle']);
        $this->assertSame('keep', $newLogo['unrelated']);

        $newFavicon = $merge->invoke(null, $existing, [
            'mjybrandfavicon' => 'image::survey::mjy-brand-favicon.png',
        ]);
        $this->assertSame('image::survey::legacy-logo.png', $newFavicon['mjybrandlogofile']);
        $this->assertSame('on', $newFavicon['mjybrandlogo']);
        $this->assertSame('image::survey::mjy-brand-favicon.png', $newFavicon['mjybrandfavicon']);
        $this->assertArrayNotHasKey('mjybrandtitle', $newFavicon);
    }

    public function testPartialWriteFailureRestoresExistingBrandFiles(): void
    {
        $dir = self::$root . '/surveys/' . self::SURVEY_ID . '/images';
        if (!is_dir($dir)) {
            mkdir($dir, 0777, true);
        }
        file_put_contents($dir . '/mjy-brand-logo.png', 'old-logo');
        file_put_contents($dir . '/mjy-brand-favicon.png', 'old-favicon');
        $body = $this->body();
        $calls = 0;
        $writer = static function (string $target, string $bytes) use (&$calls): void {
            $calls++;
            if ($calls === 2) {
                throw new \RuntimeException('forced write failure');
            }
            file_put_contents($target, $bytes);
        };

        $response = $this->endpoint(null, $writer)->handle(
            $this->signed($this->params($body)),
            static function () use ($body): string { return $body; },
            self::NOW
        );

        $this->assertSame(500, $response->status());
        $this->assertSame('old-logo', file_get_contents($dir . '/mjy-brand-logo.png'));
        $this->assertSame('old-favicon', file_get_contents($dir . '/mjy-brand-favicon.png'));
    }

    public function testThemeConfigurationFailureRestoresAlreadyReplacedFiles(): void
    {
        $dir = self::$root . '/surveys/' . self::SURVEY_ID . '/images';
        if (!is_dir($dir)) {
            mkdir($dir, 0777, true);
        }
        file_put_contents($dir . '/mjy-brand-logo.png', 'old-logo');
        file_put_contents($dir . '/mjy-brand-favicon.png', 'old-favicon');
        $body = $this->body();
        $response = $this->endpoint(static function (): void {
            throw new \RuntimeException('forced configuration failure');
        })->handle(
            $this->signed($this->params($body)),
            static function () use ($body): string { return $body; },
            self::NOW
        );

        $this->assertSame(500, $response->status());
        $this->assertSame('old-logo', file_get_contents($dir . '/mjy-brand-logo.png'));
        $this->assertSame('old-favicon', file_get_contents($dir . '/mjy-brand-favicon.png'));
    }

    public function testApplyFailureLogDoesNotExposeExceptionDetails(): void
    {
        $body = $this->body();
        $secretDetail = '/srv/private/config.php SQLSTATE[28000] password=secret';
        $before = count(\Yii::getLogger()->getLogs(\CLogger::LEVEL_ERROR, 'plugin.MjyQuestionExtensions'));

        $response = $this->endpoint(static function () use ($secretDetail): void {
            throw new \RuntimeException($secretDetail);
        })->handle(
            $this->signed($this->params($body)),
            static function () use ($body): string { return $body; },
            self::NOW
        );

        $logs = \Yii::getLogger()->getLogs(\CLogger::LEVEL_ERROR, 'plugin.MjyQuestionExtensions');
        $newLogs = array_slice($logs, $before);
        $messages = array_map(static function (array $entry): string {
            return (string) $entry[0];
        }, $newLogs);

        $this->assertSame(500, $response->status());
        $this->assertNotEmpty($messages);
        $this->assertStringContainsString('RuntimeException', end($messages));
        $this->assertStringNotContainsString($secretDetail, implode("\n", $messages));
    }
}
