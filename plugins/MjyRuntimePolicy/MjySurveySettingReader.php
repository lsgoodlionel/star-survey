<?php

/**
 * 读某个问卷名下的一类插件设置（lime_plugin_settings，model='Survey'）。
 *
 * 平台下发给本插件的东西都走这条载体：访问策略（mjy_access_policy）、
 * 考试答案键（mjy_exam_key）。它们的读法完全一样——按 (plugin_id, sid, key)
 * 取值，正常恰好一行，多于一行说明被人手工动过。
 *
 * 之所以把"份数"也交给调用方而不是这里就报错：两个调用方对"不是一行"的处理
 * 不同。判定路径要 fail closed 地抛出，回读状态端点要把份数如实报给网关。
 */
class MjySurveySettingReader
{
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
     * @return string[] 该 (问卷, 键) 下的全部值，正常是 0 或 1 个
     */
    public function values(int $surveyId, string $key): array
    {
        $column = $this->db->quoteColumnName('key');
        $rows = $this->db->createCommand()
            ->select('value')
            ->from('{{plugin_settings}}')
            ->where(
                "plugin_id = :plugin AND model = :model AND model_id = :sid AND {$column} = :key",
                [':plugin' => $this->pluginId, ':model' => self::MODEL, ':sid' => $surveyId, ':key' => $key]
            )
            ->queryColumn();
        return array_map('strval', $rows);
    }
}
