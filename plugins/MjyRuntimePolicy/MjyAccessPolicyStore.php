<?php

/**
 * 读平台下发的访问策略（ADR 0016 决定 4）。
 *
 * 策略随 LSS 导入写进 lime_plugin_settings（model='Survey', model_id=sid,
 * key='mjy_access_policy'），导入之外没有写路径，所以每个 sid 正常只有一行。
 * 多于一行说明被人手工动过：无法判断哪份算数，判定失败（fail closed）。
 */
class MjyAccessPolicyStore
{
    public const POLICY_KEY = 'mjy_access_policy';
    private const MODEL = 'Survey';

    /** @var CDbConnection */
    private $db;

    /** @var int */
    private $pluginId;

    public function __construct(CDbConnection $db, int $pluginId)
    {
        $this->db = $db;
        $this->pluginId = $pluginId;
    }

    /**
     * @return MjyAccessPolicy|null 没有策略返回 null
     * @throws RuntimeException 多于一份
     * @throws InvalidArgumentException 解析失败
     */
    public function find(int $surveyId): ?MjyAccessPolicy
    {
        $values = $this->rawValues($surveyId);
        if ($values === []) {
            return null;
        }
        if (count($values) > 1) {
            throw new RuntimeException("问卷 {$surveyId} 的访问策略有 " . count($values) . ' 份');
        }
        return MjyAccessPolicy::fromJson($values[0]);
    }

    /**
     * 给发布网关回读用：只报份数、能否解析、摘要，不报策略内容。
     *
     * @return array{surveyId: int, rows: int, valid: bool, policyDigest: string|null}
     */
    public function status(int $surveyId): array
    {
        $values = $this->rawValues($surveyId);
        $isValid = false;
        $digest = null;
        if (count($values) === 1) {
            $digest = hash('sha256', $values[0]);
            try {
                MjyAccessPolicy::fromJson($values[0]);
                $isValid = true;
            } catch (InvalidArgumentException $exception) {
                $isValid = false;
            }
        }
        return ['surveyId' => $surveyId, 'rows' => count($values), 'valid' => $isValid, 'policyDigest' => $digest];
    }

    /**
     * @return string[]
     */
    private function rawValues(int $surveyId): array
    {
        $key = $this->db->quoteColumnName('key');
        $rows = $this->db->createCommand()
            ->select('value')
            ->from('{{plugin_settings}}')
            ->where(
                "plugin_id = :plugin AND model = :model AND model_id = :sid AND {$key} = :key",
                [':plugin' => $this->pluginId, ':model' => self::MODEL, ':sid' => $surveyId, ':key' => self::POLICY_KEY]
            )
            ->queryColumn();
        return array_map('strval', $rows);
    }
}
