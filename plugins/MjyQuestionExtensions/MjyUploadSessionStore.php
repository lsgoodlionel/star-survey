<?php

/**
 * 平台侧上传会话副表。
 *
 * 引擎的上传分两步且中间**没有事件**：
 *   1. UploaderController 把文件落成 tmp/upload/futmp_<随机>_<后缀>
 *      （application/controllers/UploaderController.php:187），此时派发
 *      beforeProcessFileUpload，答卷行可能还不存在（responseId 为 null）。
 *   2. 页面提交时引擎把临时文件改名成一个**全新的** fu_<随机>
 *      （application/helpers/expressions/em_manager_helper.php:8809），不派发任何事件。
 * 所以临时文件名不能当资产标识，只能在保存后用「原始文件名＋大小」按到达顺序回绑。
 *
 * 表 {prefix}mjyquestionextensions_upload_session，自然键与结构化副表一致，
 * 再加 upload_token（平台侧资产 id）。
 *
 * 引擎报的大小是 KB（`$_FILES[...]['size'] / 1024`，UploaderController.php:144），
 * 答卷字段里存的也是这个数；两边都按同一个换算回字节，避免小文件全部取整成 0。
 */
class MjyUploadSessionStore
{
    public const TABLE = 'mjyquestionextensions_upload_session';

    public const STATE_RECEIVED = 'received';
    public const STATE_BOUND = 'bound';

    private const UNBOUND_RESPONSE_ID = 0;

    private const BYTES_PER_KB = 1024;

    /** @var CDbConnection */
    private $db;

    /** @var string */
    private $engineInstanceId;

    public function __construct(CDbConnection $db, string $engineInstanceId)
    {
        $this->db = $db;
        $this->engineInstanceId = $engineInstanceId;
    }

    public function tableName(): string
    {
        return $this->db->tablePrefix . self::TABLE;
    }

    public function ensureSchema(): void
    {
        $table = $this->tableName();
        if ($this->db->getSchema()->getTable($table, true) !== null) {
            return;
        }
        try {
            $this->db->createCommand()->createTable($table, [
                'id' => 'pk',
                'upload_token' => 'string(36) NOT NULL',
                'engine_instance_id' => 'string(64) NOT NULL',
                'survey_id' => 'integer NOT NULL',
                'generation' => 'string(36) NOT NULL',
                'response_id' => 'integer NOT NULL',
                'question_code' => 'string(64) NOT NULL',
                'field_name' => 'string(128) NULL',
                'original_name' => 'string(255) NULL',
                'temp_name' => 'string(255) NULL',
                'stored_name' => 'string(255) NULL',
                'extension' => 'string(16) NULL',
                'size_bytes' => 'integer NOT NULL',
                'state' => 'string(16) NOT NULL',
                'created_at' => 'datetime NOT NULL',
                'bound_at' => 'datetime NULL',
            ]);
            $command = $this->db->createCommand();
            $command->createIndex($table . '_token', $table, 'upload_token', true);
            $command->createIndex(
                $table . '_answer',
                $table,
                'engine_instance_id,survey_id,generation,response_id,question_code'
            );
        } catch (CDbException $exception) {
            if ($this->db->getSchema()->getTable($table, true) === null) {
                throw $exception;
            }
        }
        $this->db->getSchema()->refresh();
    }

    /**
     * 文件刚落到临时目录时登记一条会话。
     *
     * @param array<string, mixed> $file beforeProcessFileUpload 事件里的文件信息
     * @return string 平台侧资产 id
     */
    public function open(
        int $surveyId,
        string $generation,
        ?int $responseId,
        string $questionCode,
        array $file
    ): string {
        $token = MjyGenerationRef::uuidV4();
        $this->db->createCommand()->insert($this->tableName(), [
            'upload_token' => $token,
            'engine_instance_id' => $this->engineInstanceId,
            'survey_id' => $surveyId,
            'generation' => $generation,
            'response_id' => $responseId ?? self::UNBOUND_RESPONSE_ID,
            'question_code' => $questionCode,
            'field_name' => isset($file['fieldname']) ? (string) $file['fieldname'] : null,
            'original_name' => isset($file['filename']) ? (string) $file['filename'] : null,
            'temp_name' => isset($file['randfilename']) ? (string) $file['randfilename'] : null,
            'stored_name' => null,
            'extension' => isset($file['ext']) ? (string) $file['ext'] : null,
            'size_bytes' => self::toBytes($file['size'] ?? 0),
            'state' => self::STATE_RECEIVED,
            'created_at' => gmdate('Y-m-d H:i:s'),
        ]);
        return $token;
    }

    /**
     * 答卷保存后把会话绑到最终文件名上。
     *
     * @param array<int, array<string, mixed>> $files 答卷字段里的文件列表（name/size/filename）
     * @return int 完成绑定的会话数
     */
    public function bind(
        int $surveyId,
        string $generation,
        int $responseId,
        string $questionCode,
        array $files
    ): int {
        $bound = 0;
        foreach ($files as $file) {
            $session = $this->findUnbound(
                $surveyId,
                $generation,
                $responseId,
                $questionCode,
                (string) ($file['name'] ?? ''),
                self::toBytes($file['size'] ?? 0)
            );
            if ($session === null) {
                continue;
            }
            $this->db->createCommand()->update($this->tableName(), [
                'response_id' => $responseId,
                'stored_name' => (string) ($file['filename'] ?? ''),
                'state' => self::STATE_BOUND,
                'bound_at' => gmdate('Y-m-d H:i:s'),
            ], 'id = :id', [':id' => $session['id']]);
            $bound++;
        }
        return $bound;
    }

    /**
     * @return array<int, array<string, mixed>>
     */
    public function fetchSessions(int $surveyId, string $generation, int $responseId, string $questionCode): array
    {
        return $this->db->createCommand()
            ->select('*')
            ->from($this->tableName())
            ->where(
                'engine_instance_id = :instance AND survey_id = :sid AND generation = :gen'
                . ' AND response_id = :rid AND question_code = :code',
                [
                    ':instance' => $this->engineInstanceId,
                    ':sid' => $surveyId,
                    ':gen' => $generation,
                    ':rid' => $responseId,
                    ':code' => $questionCode,
                ]
            )
            ->order('id')
            ->queryAll();
    }

    public function purgeResponse(int $surveyId, string $generation, int $responseId): int
    {
        return $this->db->createCommand()->delete(
            $this->tableName(),
            'engine_instance_id = :instance AND survey_id = :sid AND generation = :gen AND response_id = :rid',
            [
                ':instance' => $this->engineInstanceId,
                ':sid' => $surveyId,
                ':gen' => $generation,
                ':rid' => $responseId,
            ]
        );
    }

    /**
     * 引擎报的是 KB（浮点），统一换算回字节，小文件才不会全部变成 0。
     *
     * @param mixed $sizeInKilobytes
     */
    private static function toBytes($sizeInKilobytes): int
    {
        return (int) round((float) $sizeInKilobytes * self::BYTES_PER_KB);
    }

    /**
     * 会话在上传时可能还没有答卷 id，所以既找本答卷的，也找尚未归属的（response_id = 0）。
     * 同名同大小的文件按到达顺序绑定 —— 引擎没有留下更强的标识。
     *
     * @return array<string, mixed>|null
     */
    private function findUnbound(
        int $surveyId,
        string $generation,
        int $responseId,
        string $questionCode,
        string $originalName,
        int $size
    ): ?array {
        $row = $this->db->createCommand()
            ->select('*')
            ->from($this->tableName())
            ->where(
                'engine_instance_id = :instance AND survey_id = :sid AND generation = :gen'
                . ' AND question_code = :code AND state = :state'
                . ' AND original_name = :name AND size_bytes = :size'
                . ' AND response_id IN (:unbound, :rid)',
                [
                    ':instance' => $this->engineInstanceId,
                    ':sid' => $surveyId,
                    ':gen' => $generation,
                    ':code' => $questionCode,
                    ':state' => self::STATE_RECEIVED,
                    ':name' => $originalName,
                    ':size' => $size,
                    ':unbound' => self::UNBOUND_RESPONSE_ID,
                    ':rid' => $responseId,
                ]
            )
            ->order('id')
            ->limit(1)
            ->queryRow();
        return $row === false ? null : $row;
    }
}
