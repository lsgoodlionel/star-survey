<?php

/**
 * 剩余时间（WP-09.2）。整套里只有这一处算"还剩多久"。
 *
 * 构造参数只有两个：**服务端定死的截止时刻**与**服务端当下时刻**
 * （MjyServerClock::nowUtc()，取自数据库，ADR 0007 决定 1）。客户端时钟、
 * 请求头里的 Date、表单里的任何时间字段都不是输入——它们连参数都进不来。
 *
 * toPayload() 是下发给页面的全部内容：只有"还剩多少秒"和服务端当下时刻。
 * 刻意**不下发**开始时刻与总时长：那两样凑在一起就是一台可以被改回去的客户端
 * 计时器，而考试里客户端算出来的时间一律不作数。页面上的倒计时只是展示，
 * 判定永远在服务端重算。
 */
class MjyExamTimer
{
    /** @var int */
    private $remainingSeconds;

    /** @var string */
    private $serverNowUtc;

    /**
     * @throws InvalidArgumentException 时刻格式不对
     */
    public function __construct(string $deadlineUtc, string $serverNowUtc)
    {
        $deadline = MjyServerClock::toTimestamp($deadlineUtc);
        $now = MjyServerClock::toTimestamp($serverNowUtc);
        // 截止时刻本身已经不算剩余：区间是 [开始, 截止)，与 MjyAccessGate 的时间窗一致。
        $this->remainingSeconds = max(0, $deadline - $now);
        $this->serverNowUtc = $serverNowUtc;
    }

    public function remainingSeconds(): int
    {
        return $this->remainingSeconds;
    }

    public function isExpired(): bool
    {
        return $this->remainingSeconds === 0;
    }

    public function serverNowUtc(): string
    {
        return $this->serverNowUtc;
    }

    /**
     * @return array{remainingSeconds: int, serverNow: string, expired: bool}
     */
    public function toPayload(): array
    {
        return [
            'remainingSeconds' => $this->remainingSeconds,
            'serverNow' => $this->serverNowUtc,
            'expired' => $this->isExpired(),
        ];
    }
}
