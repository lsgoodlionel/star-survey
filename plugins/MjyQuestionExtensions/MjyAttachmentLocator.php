<?php

/**
 * 把「(问卷, 代次, 答卷号, 引擎列名, 存储名)」解析成上传目录里的一个绝对路径
 * （ADR 0015 增补四，契约 plugin-channel-v1「附件取件」）。
 *
 * 三道闸门，缺一不可，**全部不满足时一律回 null**（调用方据此回同一个 404）：
 *
 *  1. **代次**必须是这份问卷当前的代次。与扩展表作答同一条规矩（ADR 0018 决定 9）：
 *     代次不匹配时返回空，绝不返回别的代次的数据。
 *  2. **这份文件必须真的列在这份答卷的这一列里**。列名先按答卷表的实际列白名单过一遍
 *     （防注入），再把那一列的 JSON 解开，逐个比 `filename`。这与导出的附件清单
 *     用的是**同一个来源**，所以「清单里有」与「取得到」说的是同一件事。
 *     只靠文件名语法是不够的：那样持有通道密钥的一方就能按名字翻遍整个问卷的上传目录。
 *  3. **路径必须落在这份问卷的上传目录之内**。语法（{@see MjyAttachmentFileEndpoint}）
 *     之外再做一次 realpath 归一化比对——两道是故意的，字符集将来放宽时这一道仍然成立。
 *
 * 本类**不读文件内容**：只回路径与大小，字节由端点流式写出。
 */
class MjyAttachmentLocator
{
    /** 引擎把上传的文件放在 uploaddir/surveys/<sid>/files/ 下（remotecontrol_handle.php:3908 同址）。 */
    private const FILES_SUBPATH = '/surveys/%d/files';

    /** @var CDbConnection */
    private $db;

    /** @var MjyGenerationRef */
    private $generations;

    /** @var string */
    private $uploadDir;

    public function __construct(CDbConnection $db, MjyGenerationRef $generations, string $uploadDir)
    {
        $this->db = $db;
        $this->generations = $generations;
        $this->uploadDir = $uploadDir;
    }

    /**
     * @return string|null 绝对路径；null ＝ 这份附件在引擎里取不到（任一闸门没过）
     */
    public function locate(int $surveyId, string $generation, int $responseId, string $field, string $storedName): ?string
    {
        if ($this->generations->current($surveyId) !== $generation) {
            return null;
        }
        $value = $this->answerColumn($surveyId, $responseId, $field);
        if ($value === null || !self::lists($value, $storedName)) {
            return null;
        }
        return $this->path($surveyId, $storedName);
    }

    /**
     * 那一列的原始作答值；表不存在、列不存在、答卷不存在都回 null。
     *
     * 列名先按答卷表的实际列名白名单过一遍再拼进 SQL——它虽然已经过了语法闸门，
     * 但语法允许的字符集比「这张表真有这一列」宽得多。
     */
    private function answerColumn(int $surveyId, int $responseId, string $field): ?string
    {
        $table = $this->db->tablePrefix . 'survey_' . $surveyId;
        $schema = $this->db->getSchema()->getTable($table);
        if ($schema === null || !isset($schema->columns[$field])) {
            return null;
        }
        // 交给 Yii 自己加引号：先在这里 quoteColumnName 再传进去，会被 select() 的
        // 「列名[ AS 别名]」解析再啃一遍，结果是一对空反引号。列名已经按上面的
        // schema 白名单过过一遍，这里传原名是安全的。
        $value = $this->db->createCommand()
            ->select($field)
            ->from($table)
            ->where('id = :id', [':id' => $responseId])
            ->queryScalar();

        return $value === false || $value === null ? null : (string) $value;
    }

    /**
     * 这一列的作答里有没有这个存储名。引擎写的是
     * `[{"title","comment","size","name","filename","ext"}]`，存储名在 `filename`。
     */
    private static function lists(string $value, string $storedName): bool
    {
        foreach (MjyQuestionExtensions::decodeUploadedFiles($value) as $file) {
            if (isset($file['filename']) && is_string($file['filename']) && $file['filename'] === $storedName) {
                return true;
            }
        }

        return false;
    }

    /** 归一化之后仍然在这份问卷的上传目录之内，且确实是一个普通文件。 */
    private function path(int $surveyId, string $storedName): ?string
    {
        $base = realpath($this->uploadDir . sprintf(self::FILES_SUBPATH, $surveyId));
        if ($base === false) {
            return null;
        }
        $path = realpath($base . '/' . $storedName);
        if ($path === false || strpos($path, $base . DIRECTORY_SEPARATOR) !== 0 || !is_file($path)) {
            return null;
        }

        return $path;
    }
}
