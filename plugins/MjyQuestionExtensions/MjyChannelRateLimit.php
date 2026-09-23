<?php

/**
 * 通道的速率额度（ADR 0018 决定 7，契约 plugin-channel-v1）。
 *
 * **只在验签之后调用。** 未验签的请求不该消耗额度，更不该触发落库的计数器——
 * 把限流放到验签之前，就得为匿名请求维护一张表，等于亲手造出本来不存在的放大面。
 * 未签名洪水由「验签前零 IO」挡住（决定 6），不由这里挡。
 *
 * 因此这里限的是**已经持有通道密钥的调用方**：给「我们自己的网关跑飞了」和
 * 「密钥泄露后的批量拖库」封顶。按 (引擎实例, 问卷) 分桶，固定窗口。
 *
 * 固定窗口不是令牌桶：窗口边界处可能放过接近两倍的额度。对「给已验签调用方封顶」
 * 这个目的够用（AnonymousRateLimitFilter 用令牌桶，是因为它挡的是匿名洪水）。
 * 并发下计数可能少算几次，同理可接受——它是上限，不是计费。
 */
class MjyChannelRateLimit
{
    public const TABLE = 'mjyquestionextensions_channel_rate';
    public const WINDOW_SECONDS = 60;
    public const DEFAULT_LIMIT = 120;

    /** @var CDbConnection */
    private $db;

    /** @var string */
    private $engineInstanceId;

    /** @var int */
    private $limit;

    public function __construct(CDbConnection $db, string $engineInstanceId, int $limit = self::DEFAULT_LIMIT)
    {
        $this->db = $db;
        $this->engineInstanceId = $engineInstanceId;
        $this->limit = $limit;
    }

    public function tableName(): string
    {
        return $this->db->tablePrefix . self::TABLE;
    }

    public function ensureSchema(): void
    {
        if ($this->db->getSchema()->getTable($this->tableName(), true) !== null) {
            return;
        }
        try {
            $this->db->createCommand()->createTable($this->tableName(), [
                'engine_instance_id' => 'string(64) NOT NULL',
                'survey_id' => 'integer NOT NULL',
                'window_start' => 'integer NOT NULL',
                'hits' => 'integer NOT NULL',
            ]);
            $this->db->createCommand()->createIndex(
                $this->tableName() . '_bucket',
                $this->tableName(),
                'engine_instance_id,survey_id',
                true
            );
        } catch (CDbException $exception) {
            // 并发首用时另一个请求可能已经建好表（同 MjyStructuredAnswerStore::createIfMissing）。
            if ($this->db->getSchema()->getTable($this->tableName(), true) === null) {
                throw $exception;
            }
        }
        $this->db->getSchema()->refresh();
    }

    /**
     * 记一次调用，并回答「还在额度内吗」。
     */
    public function allow(int $surveyId, int $now): bool
    {
        $window = intdiv($now, self::WINDOW_SECONDS) * self::WINDOW_SECONDS;
        $hits = $this->bump($surveyId, $window);

        return $hits <= $this->limit;
    }

    /**
     * @return int 本窗口内累计的次数（含这一次）
     */
    private function bump(int $surveyId, int $window): int
    {
        $params = [':instance' => $this->engineInstanceId, ':sid' => $surveyId, ':win' => $window];
        $bucket = 'engine_instance_id = :instance AND survey_id = :sid';

        $advanced = $this->db->createCommand()->update(
            $this->tableName(),
            ['hits' => new CDbExpression('hits + 1')],
            $bucket . ' AND window_start = :win',
            $params
        );
        if ($advanced === 0) {
            // 要么还没有这一桶，要么它停在上一个窗口里。先试着翻窗，再试着新建。
            $rolled = $this->db->createCommand()->update(
                $this->tableName(),
                ['window_start' => $window, 'hits' => 1],
                $bucket . ' AND window_start <> :win',
                $params
            );
            if ($rolled === 0) {
                $this->insertBucket($surveyId, $window);
            }
        }

        return (int) $this->db->createCommand()
            ->select('hits')
            ->from($this->tableName())
            ->where($bucket . ' AND window_start = :win', $params)
            ->queryScalar();
    }

    private function insertBucket(int $surveyId, int $window): void
    {
        try {
            $this->db->createCommand()->insert($this->tableName(), [
                'engine_instance_id' => $this->engineInstanceId,
                'survey_id' => $surveyId,
                'window_start' => $window,
                'hits' => 1,
            ]);
        } catch (CDbException $exception) {
            // 并发下另一个请求刚建好同一桶：把这一次记到它上面即可。
            $this->db->createCommand()->update(
                $this->tableName(),
                ['hits' => new CDbExpression('hits + 1')],
                'engine_instance_id = :instance AND survey_id = :sid AND window_start = :win',
                [':instance' => $this->engineInstanceId, ':sid' => $surveyId, ':win' => $window]
            );
        }
    }
}
