<?php

/**
 * 策略判定核心：把"这次请求能不能继续作答"收敛成一个纯服务端判定。
 *
 * 输入只有三样，全部来自数据库：平台下发的问卷策略、已定死的截止时刻、已发出
 * 的名额租约。请求里的任何字段（伪造的时间戳、伪造的隐藏域、旧 Cookie）都不是
 * 输入，所以改客户端时钟、刷新、重连、重开浏览器都不会改变结果。
 *
 * `$nowUtc` 只允许由 MjyPolicyEngine 自己从 MjyServerClock 取，或由测试显式给
 * 定；它永远不来自 HTTP 请求。
 */
class MjyPolicyEngine
{
    /** @var MjyServerClock */
    private $clock;

    /** @var MjySurveyPolicyStore */
    private $policies;

    /** @var MjyExamDeadlineStore */
    private $deadlines;

    /** @var MjyQuotaLeaseStore */
    private $leases;

    public function __construct(CDbConnection $db, string $engineInstanceId)
    {
        $this->clock = new MjyServerClock($db);
        $this->policies = new MjySurveyPolicyStore($db);
        $this->deadlines = new MjyExamDeadlineStore($db, $engineInstanceId);
        $this->leases = new MjyQuotaLeaseStore($db);
    }

    public function ensureSchema(): void
    {
        $this->policies->ensureSchema();
        $this->deadlines->ensureSchema();
        $this->leases->ensureSchema();
    }

    public function clock(): MjyServerClock
    {
        return $this->clock;
    }

    public function policies(): MjySurveyPolicyStore
    {
        return $this->policies;
    }

    public function leases(): MjyQuotaLeaseStore
    {
        return $this->leases;
    }

    /**
     * 每次进入作答页（含提交请求）都要走一遍。
     *
     * @param string $sessionKey 考场身份：优先准考证 token，其次 PHP 会话 id
     * @param string|null $nowUtc 仅测试显式给定；生产路径一律取数据库时钟
     */
    public function evaluateEntry(int $surveyId, string $sessionKey, ?string $nowUtc = null): MjyPolicyDecision
    {
        $policy = $this->policies->find($surveyId);
        if ($policy === null) {
            // 平台没给这份问卷下发策略，插件完全透明。
            return MjyPolicyDecision::allow();
        }
        $now = $nowUtc ?? $this->clock->nowUtc();

        $deadlineAt = null;
        if ($policy['exam_duration_seconds'] > 0) {
            $deadlineAt = $this->resolveDeadline($surveyId, $sessionKey, $policy, $now);
            // 先判超时再动名额：超时的人不该再占掉一个名额。
            if (MjyServerClock::isAfter($now, $deadlineAt)) {
                return MjyPolicyDecision::denyDeadline($deadlineAt);
            }
        }

        if ($policy['quota_slot_limit'] <= 0) {
            return MjyPolicyDecision::allow($deadlineAt);
        }
        $lease = $this->leases->reserve(
            $this->quotaKey($surveyId),
            $sessionKey,
            $policy['lease_ttl_seconds'],
            $now
        );
        if ($lease === null) {
            return MjyPolicyDecision::denyQuota();
        }
        return MjyPolicyDecision::allow($deadlineAt, $lease['lease_id']);
    }

    /**
     * 交卷时把名额落定。租约转成已确认之后，TTL 到点也不再归还。
     *
     * @return bool 是否确实落定了一张租约
     */
    public function confirmEntry(int $surveyId, string $sessionKey, ?string $nowUtc = null): bool
    {
        $policy = $this->policies->find($surveyId);
        if ($policy === null || $policy['quota_slot_limit'] <= 0) {
            return false;
        }
        $now = $nowUtc ?? $this->clock->nowUtc();
        $lease = $this->leases->findActiveLease($this->quotaKey($surveyId), $sessionKey, $now);
        if ($lease === null) {
            return false;
        }
        return $this->leases->confirm((string) $lease['lease_id']);
    }

    /**
     * 周期性把过期租约标成 expired，让状态可审计。
     *
     * @return int 本次回收的租约数
     */
    public function reapExpiredLeases(int $surveyId, ?string $nowUtc = null): int
    {
        return $this->leases->reap($this->quotaKey($surveyId), $nowUtc ?? $this->clock->nowUtc());
    }

    /**
     * 平台下发策略：写问卷策略，并把名额上限同步到配额锚点行。
     */
    public function applyPolicy(int $surveyId, int $examDurationSeconds, int $quotaSlotLimit, int $leaseTtlSeconds): void
    {
        $this->ensureSchema();
        $this->policies->save($surveyId, $examDurationSeconds, $quotaSlotLimit, $leaseTtlSeconds);
        if ($quotaSlotLimit > 0) {
            $this->leases->defineQuota($this->quotaKey($surveyId), $surveyId, $quotaSlotLimit);
        }
    }

    public function quotaKey(int $surveyId): string
    {
        return "survey:{$surveyId}";
    }

    /**
     * @param array{exam_duration_seconds: int, quota_slot_limit: int, lease_ttl_seconds: int} $policy
     */
    private function resolveDeadline(int $surveyId, string $sessionKey, array $policy, string $now): string
    {
        $record = $this->deadlines->start($surveyId, $sessionKey, $policy['exam_duration_seconds'], $now);
        return $record['deadline_at'];
    }
}
