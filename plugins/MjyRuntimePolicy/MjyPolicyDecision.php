<?php

/**
 * 一次策略判定的结果。不可变值对象：构造之后不再修改。
 *
 * 拒绝原因是给平台看的机器可读值，message 是给作答者看的中文提示。
 */
class MjyPolicyDecision
{
    public const REASON_ALLOWED = 'allowed';
    public const REASON_DEADLINE = 'deadline';
    public const REASON_QUOTA = 'quota';
    public const REASON_UNAVAILABLE = 'unavailable';
    // WP-04 访问规则（ADR 0016）。
    public const REASON_NOT_OPEN = 'not_open';
    public const REASON_CLOSED = 'closed';
    public const REASON_NETWORK = 'network';
    public const REASON_REGION = 'region';
    public const REASON_REGION_UNKNOWN = 'region_unknown';
    public const REASON_PASSWORD = 'password';
    public const REASON_TOKEN_REQUIRED = 'token_required';
    public const REASON_ATTEMPTS = 'attempts';
    public const REASON_DURATION = 'duration';

    private const APPEAL = '如认为有误，请联系问卷发布方。';

    /** @var string */
    private $reason;

    /** @var string */
    private $message;

    /** @var string|null */
    private $deadlineAt;

    /** @var string|null */
    private $leaseId;

    private function __construct(string $reason, string $message, ?string $deadlineAt, ?string $leaseId)
    {
        $this->reason = $reason;
        $this->message = $message;
        $this->deadlineAt = $deadlineAt;
        $this->leaseId = $leaseId;
    }

    public static function allow(?string $deadlineAt = null, ?string $leaseId = null): self
    {
        return new self(self::REASON_ALLOWED, '', $deadlineAt, $leaseId);
    }

    public static function denyDeadline(string $deadlineAt): self
    {
        return new self(
            self::REASON_DEADLINE,
            '本场考试的作答时间已经结束，系统不再接收作答。',
            $deadlineAt,
            null
        );
    }

    public static function denyQuota(): self
    {
        return new self(
            self::REASON_QUOTA,
            '本场考试的名额已经全部分配完毕，无法继续作答。',
            null,
            null
        );
    }

    /**
     * 策略本身查不下去（建表失败、数据库不可用）时的判定。考试场景下必须
     * 拒绝而不是放行：放行意味着策略形同虚设。
     */
    public static function denyUnavailable(): self
    {
        return new self(
            self::REASON_UNAVAILABLE,
            '考试策略暂时无法校验，请稍后重试或联系监考人员。',
            null,
            null
        );
    }

    public static function denyNotOpen(string $opensAt): self
    {
        return new self(self::REASON_NOT_OPEN, "本问卷尚未开放，开放时间：{$opensAt}。", null, null);
    }

    public static function denyClosed(string $closesAt): self
    {
        return new self(self::REASON_CLOSED, "本问卷已于 {$closesAt} 截止，不再接收作答。", null, null);
    }

    public static function denyNetwork(): self
    {
        return new self(self::REASON_NETWORK, '当前网络不在本问卷允许的范围内，无法作答。' . self::APPEAL, null, null);
    }

    public static function denyRegion(): self
    {
        return new self(self::REASON_REGION, '当前所在地区不在本问卷允许的范围内，无法作答。' . self::APPEAL, null, null);
    }

    public static function denyRegionUnknown(): self
    {
        return new self(
            self::REASON_REGION_UNKNOWN,
            '暂时无法确认您所在的地区，本问卷只对指定地区开放。' . self::APPEAL,
            null,
            null
        );
    }

    /**
     * 不是最终拒绝：插件据此渲染密码页，而不是拒绝页。
     */
    public static function passwordRequired(): self
    {
        return new self(self::REASON_PASSWORD, '本问卷需要访问密码。', null, null);
    }

    public static function denyTokenRequired(): self
    {
        return new self(self::REASON_TOKEN_REQUIRED, '本问卷需要有效的邀请码才能作答，请使用收到的专属链接进入。', null, null);
    }

    public static function denyAttempts(int $maxResponses): self
    {
        return new self(
            self::REASON_ATTEMPTS,
            "您已达到本问卷允许的作答次数上限（{$maxResponses} 次），不能再次作答。" . self::APPEAL,
            null,
            null
        );
    }

    public static function denyDuration(string $deadlineAt, int $durationSeconds): self
    {
        $minutes = intdiv($durationSeconds, 60);
        return new self(
            self::REASON_DURATION,
            "本次作答限时 {$minutes} 分钟，时间已到，系统不再接收作答。",
            $deadlineAt,
            null
        );
    }

    public function isAllowed(): bool
    {
        return $this->reason === self::REASON_ALLOWED;
    }

    public function reason(): string
    {
        return $this->reason;
    }

    public function message(): string
    {
        return $this->message;
    }

    public function deadlineAt(): ?string
    {
        return $this->deadlineAt;
    }

    public function leaseId(): ?string
    {
        return $this->leaseId;
    }
}
