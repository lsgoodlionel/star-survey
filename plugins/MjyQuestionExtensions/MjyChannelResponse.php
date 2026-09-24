<?php

/**
 * 通道端点的一次应答（ADR 0018，契约 plugin-channel-v1）。
 *
 * 把「状态＋响应体」与「原因码」分开是刻意的：**原因码只给日志**，
 * 永远不进响应体。验签之前的每一种拒绝都共用 unauthorized() 这**一个**出口，
 * 因此它们的状态与响应体逐字相同，构不成预言机（ADR 0014 飞书回调那次的教训）。
 *
 * 不可变：构造之后不改。
 */
class MjyChannelResponse
{
    /** @var int */
    private $status;

    /** @var string */
    private $body;

    /** @var string */
    private $reason;

    private function __construct(int $status, string $body, string $reason)
    {
        $this->status = $status;
        $this->body = $body;
        $this->reason = $reason;
    }

    /**
     * 验签之前（含验签本身）的一切拒绝的**唯一**出口。
     * 原因只作为 reason() 交给日志，绝不出现在 body() 里。
     */
    public static function unauthorized(string $reason): self
    {
        return new self(401, '{"error":"unauthorized"}', $reason);
    }

    /** 验签通过但这一页太大。绝不静默截断：少几行而没人察觉比报错危险得多。 */
    public static function pageTooLarge(): self
    {
        return new self(400, '{"error":"page_too_large"}', 'page_too_large');
    }

    /** 验签通过但超出速率额度。只取决于速率、不取决于请求内容，因此不构成预言机。 */
    public static function rateLimited(): self
    {
        return new self(429, '{"error":"rate_limited"}', 'rate_limited');
    }

    /** 验签通过但插件内部出错。原因只进服务端日志。 */
    public static function unavailable(string $reason): self
    {
        return new self(500, '{"error":"unavailable"}', $reason);
    }

    public static function ok(string $body): self
    {
        return new self(200, $body, '');
    }

    public function status(): int
    {
        return $this->status;
    }

    public function body(): string
    {
        return $this->body;
    }

    /** 给日志用的稳定原因码；通过时为空串。 */
    public function reason(): string
    {
        return $this->reason;
    }
}
