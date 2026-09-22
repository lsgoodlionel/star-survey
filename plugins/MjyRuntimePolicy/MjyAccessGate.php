<?php

/**
 * 访问闸门（WP-04.1 / 04.2，ADR 0016）：一次请求能不能进入／继续作答。
 *
 * 判定顺序（先便宜、先不花名额的）：时间窗 → IP → 地区 → 密码 → 限次 → 时长。
 *
 * 限次与时长的记账（ADR 0016 决定 6）：
 * - 主身份＝有 token 用 token，否则设备；"第几次作答"＝主身份已确认的次数＋1；
 * - 租约持有者＝`<主身份>#<第几次>`：同一个人刷新、换标签页、重连命中同一张租约；
 * - 每条限次规则一个名额锚点 `person:<sid>:<维度身份>`，上限＝规则里的 max；
 *   主身份即使没有规则也要一个锚点（上限视同不限）来记"交过几次"；
 * - 时长在截止时刻表里按 `<主身份>#<第几次>` 一次写死。
 *
 * `$nowUtc` 只来自 MjyServerClock（或测试显式给定），永远不来自请求。
 */
class MjyAccessGate
{
    /** 主身份没有限次规则时的名额上限：只用来记账，不限人。 */
    private const UNLIMITED = 1000000;
    /** 放弃的作答在"时长＋宽限"之后归还名额；没设时长时 24 小时。 */
    private const LEASE_GRACE_SECONDS = 300;
    private const DEFAULT_LEASE_SECONDS = 86400;

    /** @var MjyQuotaLeaseStore */
    private $leases;

    /** @var MjyExamDeadlineStore */
    private $deadlines;

    /** @var MjyRegionResolver */
    private $regions;

    public function __construct(CDbConnection $db, string $engineInstanceId, MjyRegionResolver $regions)
    {
        $this->leases = new MjyQuotaLeaseStore($db);
        $this->deadlines = new MjyExamDeadlineStore($db, $engineInstanceId);
        $this->regions = $regions;
    }

    public function evaluate(MjyAccessPolicy $policy, MjyAccessRequest $request, string $nowUtc): MjyPolicyDecision
    {
        $denial = $this->checkWindow($policy, $nowUtc)
            ?? $this->checkIp($policy, $request)
            ?? $this->checkRegion($policy, $request);
        if ($denial !== null) {
            return $denial;
        }
        if ($policy->passwordHash() !== null && !$request->isUnlocked()) {
            return MjyPolicyDecision::passwordRequired();
        }
        if (!$policy->tracksAttempts()) {
            return MjyPolicyDecision::allow();
        }
        return $this->checkAttempts($policy, $request, $nowUtc);
    }

    /**
     * 交卷时把本次作答在各维度上的租约确认。之后"第几次"加一。
     *
     * @return int 确认了几张租约
     */
    public function confirm(MjyAccessPolicy $policy, MjyAccessRequest $request, string $nowUtc): int
    {
        if (!$policy->tracksAttempts()) {
            return 0;
        }
        $holder = $this->holder($request);
        $confirmed = 0;
        foreach ($this->dimensions($policy, $request) as $dimension) {
            $lease = $this->leases->findActiveLease($dimension['quotaKey'], $holder, $nowUtc);
            if ($lease !== null && $this->leases->confirm((string) $lease['lease_id'])) {
                $confirmed++;
            }
        }
        return $confirmed;
    }

    // ------------------------------------------------------------ 时间、IP、地区

    private function checkWindow(MjyAccessPolicy $policy, string $nowUtc): ?MjyPolicyDecision
    {
        $opensAt = $policy->opensAtUtc();
        if ($opensAt !== null && MjyServerClock::isAfter($opensAt, $nowUtc)) {
            return MjyPolicyDecision::denyNotOpen($policy->opensAtText());
        }
        $closesAt = $policy->closesAtUtc();
        // 区间是 [opensAt, closesAt)：截止时刻本身已经不接收。
        if ($closesAt !== null && !MjyServerClock::isAfter($closesAt, $nowUtc)) {
            return MjyPolicyDecision::denyClosed($policy->closesAtText());
        }
        return null;
    }

    private function checkIp(MjyAccessPolicy $policy, MjyAccessRequest $request): ?MjyPolicyDecision
    {
        $ip = $request->clientIp();
        if (MjyIpRules::matchesAny($ip, $policy->denyIps())) {
            return MjyPolicyDecision::denyNetwork();
        }
        if ($policy->allowIps() !== [] && !MjyIpRules::matchesAny($ip, $policy->allowIps())) {
            return MjyPolicyDecision::denyNetwork();
        }
        return null;
    }

    private function checkRegion(MjyAccessPolicy $policy, MjyAccessRequest $request): ?MjyPolicyDecision
    {
        if ($policy->allowRegions() === [] && $policy->denyRegions() === []) {
            return null;
        }
        $region = $this->regions->resolve($request->clientIp());
        if ($region === null) {
            return $policy->denyUnknownRegion() ? MjyPolicyDecision::denyRegionUnknown() : null;
        }
        if ($this->regionListed($region, $policy->denyRegions())) {
            return MjyPolicyDecision::denyRegion();
        }
        if ($policy->allowRegions() !== [] && !$this->regionListed($region, $policy->allowRegions())) {
            return MjyPolicyDecision::denyRegion();
        }
        return null;
    }

    /**
     * @param string[] $rules
     */
    private function regionListed(string $region, array $rules): bool
    {
        foreach ($rules as $rule) {
            if (MjyIpRules::regionMatches($region, $rule)) {
                return true;
            }
        }
        return false;
    }

    // ------------------------------------------------------------ 限次与时长

    private function checkAttempts(MjyAccessPolicy $policy, MjyAccessRequest $request, string $nowUtc): MjyPolicyDecision
    {
        if ($this->needsToken($policy) && $request->token() === null) {
            // 闭合访问（access_mode=C）时，引擎自己的 token 入口页会拦住没有有效 token 的人；
            // 开放访问时没有 token 就无从按 token 限次，只能拒绝。
            return $request->isClosedAccess() ? MjyPolicyDecision::allow() : MjyPolicyDecision::denyTokenRequired();
        }
        $holder = $this->holder($request);
        $ttl = $this->leaseTtl($policy);
        $created = [];
        foreach ($this->dimensions($policy, $request) as $dimension) {
            $this->leases->ensureQuota($dimension['quotaKey'], $request->surveyId(), $dimension['max']);
            $isExisting = $this->leases->findActiveLease($dimension['quotaKey'], $holder, $nowUtc) !== null;
            $lease = $this->leases->reserve($dimension['quotaKey'], $holder, $ttl, $nowUtc);
            if ($lease === null) {
                $this->releaseAll($created);
                return MjyPolicyDecision::denyAttempts($dimension['max']);
            }
            if (!$isExisting) {
                $created[] = $lease['lease_id'];
            }
        }
        return $this->checkDuration($policy, $request, $holder, $nowUtc);
    }

    private function checkDuration(
        MjyAccessPolicy $policy,
        MjyAccessRequest $request,
        string $holder,
        string $nowUtc
    ): MjyPolicyDecision {
        $duration = $policy->maxDurationSeconds();
        if ($duration === null) {
            return MjyPolicyDecision::allow();
        }
        $record = $this->deadlines->start($request->surveyId(), $holder, $duration, $nowUtc);
        if (MjyServerClock::isAfter($nowUtc, $record['deadline_at'])) {
            return MjyPolicyDecision::denyDuration($record['deadline_at'], $duration);
        }
        return MjyPolicyDecision::allow($record['deadline_at']);
    }

    /**
     * 本次作答要记账的维度：每条限次规则一个，再加上主身份（没有规则时视同不限）。
     *
     * @return array<int, array{quotaKey: string, max: int}>
     */
    private function dimensions(MjyAccessPolicy $policy, MjyAccessRequest $request): array
    {
        $primary = $request->primaryDimension();
        $dimensions = [];
        $hasPrimary = false;
        foreach ($policy->responseLimits() as $limit) {
            $identity = $request->identity($limit['by']);
            if ($identity === null) {
                continue;
            }
            $hasPrimary = $hasPrimary || $limit['by'] === $primary;
            $dimensions[] = ['quotaKey' => $this->quotaKey($request, $identity), 'max' => $limit['max']];
        }
        if (!$hasPrimary) {
            $identity = (string) $request->identity($primary);
            array_unshift($dimensions, ['quotaKey' => $this->quotaKey($request, $identity), 'max' => self::UNLIMITED]);
        }
        return $dimensions;
    }

    private function holder(MjyAccessRequest $request): string
    {
        $identity = (string) $request->identity($request->primaryDimension());
        $ordinal = $this->leases->confirmedCount($this->quotaKey($request, $identity)) + 1;
        return $identity . '#' . $ordinal;
    }

    private function quotaKey(MjyAccessRequest $request, string $identity): string
    {
        return 'person:' . $request->surveyId() . ':' . $identity;
    }

    private function needsToken(MjyAccessPolicy $policy): bool
    {
        foreach ($policy->responseLimits() as $limit) {
            if ($limit['by'] === 'token') {
                return true;
            }
        }
        return false;
    }

    private function leaseTtl(MjyAccessPolicy $policy): int
    {
        $duration = $policy->maxDurationSeconds();
        return $duration === null ? self::DEFAULT_LEASE_SECONDS : $duration + self::LEASE_GRACE_SECONDS;
    }

    /**
     * @param string[] $leaseIds
     */
    private function releaseAll(array $leaseIds): void
    {
        foreach ($leaseIds as $leaseId) {
            $this->leases->release($leaseId);
        }
    }
}
