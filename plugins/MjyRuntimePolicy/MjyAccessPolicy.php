<?php

/**
 * 平台下发的访问策略（WP-04，ADR 0016，契约 survey-access-policy-v1 §4）。不可变值对象。
 *
 * 载荷由发布网关编译，经 LSS 的 plugin_settings 写进引擎库。这里只做形状校验：
 * 任何一处不对都抛 InvalidArgumentException，调用方按"查不下去即拒绝"处理。
 * 规则的含义（开多久、几次、哪些网段）全部来自平台，插件不做任何业务判断。
 */
class MjyAccessPolicy
{
    public const SCHEMA = 'mjy-access-policy/1';
    public const IDENTITIES = ['token', 'device', 'ip'];

    private const UTC_PATTERN = '/\A\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\z/';
    private const REGION_PATTERN = '/\A[A-Z]{2}(-[A-Z0-9]{1,3})?\z/';
    private const HASH_PREFIX = 'pbkdf2-sha256$';
    private const MAX_RESPONSES = 10000;
    private const MIN_DURATION = 60;
    private const MAX_DURATION = 604800;

    /** @var array<string, mixed> */
    private $payload;

    /** @var string */
    private $digest;

    /**
     * @param array<string, mixed> $payload
     */
    private function __construct(array $payload, string $digest)
    {
        $this->payload = $payload;
        $this->digest = $digest;
    }

    public static function fromJson(string $raw): self
    {
        $payload = json_decode($raw, true);
        if (!is_array($payload) || array_values($payload) === $payload) {
            throw new InvalidArgumentException('访问策略不是 JSON 对象');
        }
        if (($payload['schema'] ?? null) !== self::SCHEMA) {
            throw new InvalidArgumentException('不认识的访问策略版本');
        }
        self::checkWindow($payload['window'] ?? null);
        self::checkPassword($payload['passwordHash'] ?? null);
        self::checkResponses($payload['responses'] ?? []);
        self::checkDuration($payload['maxDurationSeconds'] ?? null);
        self::checkNetwork($payload['network'] ?? null);
        return new self($payload, hash('sha256', $raw));
    }

    public function digest(): string
    {
        return $this->digest;
    }

    public function opensAtUtc(): ?string
    {
        return $this->payload['window']['opensAt'] ?? null;
    }

    public function closesAtUtc(): ?string
    {
        return $this->payload['window']['closesAt'] ?? null;
    }

    /** 给作答者看的本地时刻，如"2026-10-01 09:00（Asia/Shanghai）"。 */
    public function opensAtText(): string
    {
        return $this->localText('opensAtLocal');
    }

    public function closesAtText(): string
    {
        return $this->localText('closesAtLocal');
    }

    public function passwordHash(): ?string
    {
        return $this->payload['passwordHash'] ?? null;
    }

    /**
     * @return array<int, array{by: string, max: int}>
     */
    public function responseLimits(): array
    {
        return $this->payload['responses'] ?? [];
    }

    public function maxDurationSeconds(): ?int
    {
        return $this->payload['maxDurationSeconds'] ?? null;
    }

    public function tracksAttempts(): bool
    {
        return $this->responseLimits() !== [] || $this->maxDurationSeconds() !== null;
    }

    public function hasNetworkRules(): bool
    {
        return !empty($this->payload['network']);
    }

    /** @return string[] */
    public function allowIps(): array
    {
        return $this->payload['network']['allowIps'] ?? [];
    }

    /** @return string[] */
    public function denyIps(): array
    {
        return $this->payload['network']['denyIps'] ?? [];
    }

    /** @return string[] */
    public function allowRegions(): array
    {
        return $this->payload['network']['allowRegions'] ?? [];
    }

    /** @return string[] */
    public function denyRegions(): array
    {
        return $this->payload['network']['denyRegions'] ?? [];
    }

    public function denyUnknownRegion(): bool
    {
        return ($this->payload['network']['regionUnknown'] ?? 'deny') !== 'allow';
    }

    private function localText(string $key): string
    {
        $local = (string) ($this->payload['window'][$key] ?? '');
        $zone = (string) ($this->payload['window']['timezone'] ?? '');
        return str_replace('T', ' ', $local) . '（' . $zone . '）';
    }

    // ------------------------------------------------------------ 形状校验

    private static function checkWindow($window): void
    {
        if ($window === null) {
            return;
        }
        if (!is_array($window) || !is_string($window['timezone'] ?? null)) {
            throw new InvalidArgumentException('时间窗形状不对');
        }
        foreach (['opensAt', 'closesAt'] as $key) {
            $value = $window[$key] ?? null;
            if ($value !== null && (!is_string($value) || preg_match(self::UTC_PATTERN, $value) !== 1)) {
                throw new InvalidArgumentException("时间窗 {$key} 不是 UTC 时刻");
            }
        }
    }

    private static function checkPassword($hash): void
    {
        if ($hash !== null && (!is_string($hash) || strpos($hash, self::HASH_PREFIX) !== 0)) {
            throw new InvalidArgumentException('密码哈希格式不对');
        }
    }

    private static function checkResponses($responses): void
    {
        if (!is_array($responses)) {
            throw new InvalidArgumentException('限次规则必须是数组');
        }
        foreach ($responses as $limit) {
            $by = $limit['by'] ?? null;
            $max = $limit['max'] ?? null;
            if (!in_array($by, self::IDENTITIES, true) || !is_int($max) || $max < 1 || $max > self::MAX_RESPONSES) {
                throw new InvalidArgumentException('限次规则形状不对');
            }
        }
    }

    private static function checkDuration($seconds): void
    {
        if ($seconds !== null && (!is_int($seconds) || $seconds < self::MIN_DURATION || $seconds > self::MAX_DURATION)) {
            throw new InvalidArgumentException('作答时长不对');
        }
    }

    private static function checkNetwork($network): void
    {
        if ($network === null) {
            return;
        }
        if (!is_array($network) || !in_array($network['regionUnknown'] ?? null, ['deny', 'allow'], true)) {
            throw new InvalidArgumentException('网络规则形状不对');
        }
        foreach (['allowIps', 'denyIps'] as $key) {
            foreach (self::stringList($network, $key) as $cidr) {
                if (!MjyIpRules::isValidCidr($cidr)) {
                    throw new InvalidArgumentException("{$key} 里有非法网段");
                }
            }
        }
        foreach (['allowRegions', 'denyRegions'] as $key) {
            foreach (self::stringList($network, $key) as $region) {
                if (preg_match(self::REGION_PATTERN, $region) !== 1) {
                    throw new InvalidArgumentException("{$key} 里有非法地区码");
                }
            }
        }
    }

    /**
     * @return string[]
     */
    private static function stringList(array $block, string $key): array
    {
        $values = $block[$key] ?? [];
        if (!is_array($values)) {
            throw new InvalidArgumentException("{$key} 必须是数组");
        }
        foreach ($values as $value) {
            if (!is_string($value)) {
                throw new InvalidArgumentException("{$key} 必须是字符串数组");
            }
        }
        return $values;
    }
}
