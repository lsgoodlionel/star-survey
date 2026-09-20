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
