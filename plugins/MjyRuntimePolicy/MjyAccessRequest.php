<?php

/**
 * 一次作答请求在访问闸门眼里的样子。不可变；全部由服务端取得：
 * IP 来自 MjyIpRules（不信客户端头），token 来自引擎会话，设备号来自插件自己发的 Cookie，
 * 是否已解锁来自服务端会话。请求里的任何时间字段都不在这里。
 */
class MjyAccessRequest
{
    /** @var int */
    private $surveyId;

    /** @var string */
    private $clientIp;

    /** @var string|null */
    private $token;

    /** @var string */
    private $deviceId;

    /** @var bool */
    private $isUnlocked;

    /** @var bool */
    private $hasTokenTable;

    public function __construct(
        int $surveyId,
        string $clientIp,
        ?string $token,
        string $deviceId,
        bool $isUnlocked,
        bool $hasTokenTable
    ) {
        $this->surveyId = $surveyId;
        $this->clientIp = $clientIp;
        $this->token = ($token === null || $token === '') ? null : $token;
        $this->deviceId = $deviceId;
        $this->isUnlocked = $isUnlocked;
        $this->hasTokenTable = $hasTokenTable;
    }

    public function surveyId(): int
    {
        return $this->surveyId;
    }

    public function clientIp(): string
    {
        return $this->clientIp;
    }

    public function token(): ?string
    {
        return $this->token;
    }

    public function isUnlocked(): bool
    {
        return $this->isUnlocked;
    }

    public function hasTokenTable(): bool
    {
        return $this->hasTokenTable;
    }

    /**
     * 某个身份维度上的身份键；token 维度在没有 token 时返回 null。
     */
    public function identity(string $dimension): ?string
    {
        switch ($dimension) {
            case 'token':
                return $this->token === null ? null : 'token:' . $this->token;
            case 'device':
                return 'device:' . $this->deviceId;
            case 'ip':
                return 'ip:' . $this->clientIp;
        }
        throw new InvalidArgumentException("不认识的身份维度 {$dimension}");
    }

    /**
     * 最可靠的身份：有 token 用 token，否则用设备。计时与"第几次作答"都挂在它上面。
     */
    public function primaryDimension(): string
    {
        return $this->token === null ? 'device' : 'token';
    }
}
