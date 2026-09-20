<?php

/**
 * MJY 题型扩展（P0-00.3 原型，WP-02）。
 *
 * 引擎没有「自增表格」题型，也没有任何可以**否决一次作答提交**的插件事件。
 * 本插件把平台需要的三件事接到引擎上：
 *  - newQuestionAttributes：自增表格的列定义、最少/最多行数；
 *  - beforeSurveyPage：在引擎处理 POST 之前重新校验并归一化结构化作答，
 *    不合法时把值清空，借引擎自己的必答校验把作答者留在本页（见 ADR 0006）；
 *  - afterResponseSave / afterSurveyDynamicSave：把结构化作答投影进副表；
 *  - afterResponseDelete / afterSurveyDynamicDelete：清理副表；
 *  - beforeProcessFileUpload：登记平台侧上传会话。
 *
 * 所有异常只写日志，绝不打断作答者或管理员的请求（与 MjyPlatformBridge 一致）。
 */
class MjyQuestionExtensions extends \LimeSurvey\PluginManager\PluginBase
{
    public const DEFAULT_ENGINE_INSTANCE_ID = 'local-dev';

    private const ENGINE_INSTANCE_ENV = 'MJY_ENGINE_INSTANCE_ID';
    private const LOG_CATEGORY = 'plugin.MjyQuestionExtensions';

    protected $storage = 'DbStorage';
    protected static $description = 'MJY: 自增表格与上传会话的题型扩展';
    protected static $name = 'MjyQuestionExtensions';

    /** @var string[] */
    public $allowedPublicMethods = [];

    /** @var array<string, string> 本次请求里被拒绝的结构化作答：字段名 => 错误文案 */
    private $pageErrors = [];

    /** @var MjyStructuredAnswerStore|null */
    private $structuredAnswers;

    /** @var MjyUploadSessionStore|null */
    private $uploadSessions;

    /** @var MjyGenerationRef|null */
    private $generations;

    /** @var bool */
    private $isSchemaReady = false;

    /** @var array<int, MjyThemedQuestionMap> 每个请求内按问卷缓存，避免重复扫题目表 */
    private $questionMaps = [];

    /** @var array<int, string> 每个请求内按问卷缓存代次 */
    private $generationCache = [];

    public function init()
    {
        $this->subscribe('beforeActivate');
        $this->subscribe('newQuestionAttributes');
        $this->subscribe('beforeSurveyPage');
        $this->subscribe('beforeQuestionRender');
        $this->subscribe('beforeProcessFileUpload');
        $this->subscribe('afterResponseSave');
        $this->subscribe('afterSurveyDynamicSave');
        $this->subscribe('afterResponseDelete');
        $this->subscribe('afterSurveyDynamicDelete');
    }

    public static function engineInstanceId(): string
    {
        $configured = getenv(self::ENGINE_INSTANCE_ENV);
        return ($configured === false || $configured === '') ? self::DEFAULT_ENGINE_INSTANCE_ID : $configured;
    }

    public function beforeActivate()
    {
        $this->safely(function () {
            $this->ensureSchema();
        });
    }

    public function ensureSchema(): void
    {
        $this->structuredAnswers()->ensureSchema();
        $this->uploadSessions()->ensureSchema();
        $this->generations()->ensureSchema();
        $this->isSchemaReady = true;
    }

    /**
     * 忘掉本请求内缓存的一切（建表状态、题目映射、代次）。
     * 测试会在同一个插件实例上改结构，必须能让缓存作废。
     */
    public function resetRequestState(): void
    {
        $this->isSchemaReady = false;
        $this->questionMaps = [];
        $this->generationCache = [];
    }

    public function newQuestionAttributes()
    {
        $this->getEvent()->append('questionAttributes', MjyQuestionAttributeDefinitions::all());
    }

    /**
     * 引擎处理 POST 之前的唯一落脚点：这里重写 $_POST，后面 EM 读到的就是归一化后的值
     * （`application/helpers/expressions/em_manager_helper.php:8750` 直接读 $_POST）。
     */
    public function beforeSurveyPage()
    {
        $this->safely(function () {
            $surveyId = (int) $this->getEvent()->get('surveyId');
            if ($surveyId <= 0 || $_POST === []) {
                return;
            }
            $this->gatePostedAnswers($surveyId);
        });
    }

    /**
     * 对本次 POST 里出现的每一道自增表格题做服务端校验。
     *
     * 每道题单独兜异常：一道题的列定义坏掉**不能**让其余题目的校验被跳过，
     * 那是最糟的失败方式（失败开放）。
     *
     * @return array<string, string> 字段名 => 拒绝理由
     */
    public function gatePostedAnswers(int $surveyId): array
    {
        $this->pageErrors = [];
        foreach ($this->questionMap($surveyId)->repeatingTables() as $question) {
            try {
                $this->gateAnswer($surveyId, $question);
            } catch (\Throwable $exception) {
                // 题目配置坏掉时同样拦下这道题，不放行未经校验的内容。
                $_POST[$question['fieldName']] = '';
                $this->pageErrors[$question['fieldName']] = '题目配置不可用，请联系管理员';
                Yii::log(
                    sprintf('survey %d %s config error: %s', $surveyId, $question['code'], $exception->getMessage()),
                    CLogger::LEVEL_ERROR,
                    self::LOG_CATEGORY
                );
            }
        }
        return $this->pageErrors;
    }

    /**
     * man_message 会被问卷模板以 raw 渲染，所以文案在这里统一转义。
     */
    public static function renderRejection(string $message): string
    {
        return CHtml::tag('div', ['class' => 'ls-questionhelp text-danger'], CHtml::encode($message));
    }

    /**
     * 把服务端的拒绝理由显示出来。清空值只会触发引擎通用的「必答」提示，
     * 作答者看不出到底哪一行不对。
     */
    public function beforeQuestionRender()
    {
        $this->safely(function () {
            $fieldName = 'Q' . (int) $this->getEvent()->get('qid');
            if (!isset($this->pageErrors[$fieldName])) {
                return;
            }
            $this->getEvent()->set('man_message', self::renderRejection($this->pageErrors[$fieldName]));
        });
    }

    public function beforeProcessFileUpload()
    {
        $this->safely(function () {
            $event = $this->getEvent();
            $surveyId = (int) $event->get('surveyId');
            $questionCode = $this->questionMap($surveyId)->uploadCodeForQuestionId((int) $event->get('qid'));
            if ($questionCode === null) {
                return;
            }
            $responseId = $event->get('responseId');
            $this->openUploadSession($surveyId, $responseId === null ? null : (int) $responseId, $questionCode, [
                'fieldname' => $event->get('fieldname'),
                'filename' => $event->get('filename'),
                'randfilename' => $event->get('randfilename'),
                'ext' => $event->get('ext'),
                'size' => $event->get('size'),
            ]);
        });
    }

    public function afterResponseSave()
    {
        $this->safely(function () {
            $this->projectModel($this->getEvent()->get('dynamicId'), $this->getEvent()->get('model'));
        });
    }

    public function afterSurveyDynamicSave()
    {
        $this->safely(function () {
            $this->projectModel($this->getEvent()->get('surveyId'), $this->getEvent()->get('model'));
        });
    }

    public function afterResponseDelete()
    {
        $this->safely(function () {
            $this->purgeModel($this->getEvent()->get('dynamicId'), $this->getEvent()->get('model'));
        });
    }

    public function afterSurveyDynamicDelete()
    {
        $this->safely(function () {
            $this->purgeModel($this->getEvent()->get('surveyId'), $this->getEvent()->get('model'));
        });
    }

    /**
     * 重新读取答卷行，把所有自增表格题投影进副表。保存钩子与后台补投都走这一条路径。
     *
     * @return array<string, MjyValidationResult> 题目代码 => 校验结果
     */
    public function projectResponse(int $surveyId, int $responseId): array
    {
        $map = $this->questionMap($surveyId);
        if ($map->repeatingTables() === [] && $map->uploads() === []) {
            return [];
        }
        $row = $this->responseRow($surveyId, $responseId);
        if ($row === null) {
            return [];
        }
        $generation = $this->currentGeneration($surveyId);

        $results = [];
        foreach ($map->repeatingTables() as $code => $question) {
            $result = $this->validatorFor($question)->validate($row[$question['fieldName']] ?? null);
            $this->structuredAnswers()->recordAnswer($surveyId, $generation, $responseId, $code, $result);
            $results[$code] = $result;
        }
        foreach ($map->uploads() as $code => $question) {
            $this->bindUploadSessions(
                $surveyId,
                $responseId,
                $code,
                self::decodeUploadedFiles($row[$question['fieldName']] ?? null)
            );
        }
        return $results;
    }

    /**
     * @param array<string, mixed> $file
     * @return string 平台侧资产 id
     */
    public function openUploadSession(int $surveyId, ?int $responseId, string $questionCode, array $file): string
    {
        return $this->uploadSessions()->open(
            $surveyId,
            $this->currentGeneration($surveyId),
            $responseId,
            $questionCode,
            $file
        );
    }

    /**
     * @param array<int, array<string, mixed>> $files
     * @return int 完成绑定的会话数
     */
    public function bindUploadSessions(int $surveyId, int $responseId, string $questionCode, array $files): int
    {
        if ($files === []) {
            return 0;
        }
        return $this->uploadSessions()->bind(
            $surveyId,
            $this->currentGeneration($surveyId),
            $responseId,
            $questionCode,
            $files
        );
    }

    public function currentGeneration(int $surveyId): string
    {
        if (!isset($this->generationCache[$surveyId])) {
            $this->ensureSchemaOnce();
            $this->generationCache[$surveyId] = $this->generations()->current($surveyId);
        }
        return $this->generationCache[$surveyId];
    }

    public function structuredAnswers(): MjyStructuredAnswerStore
    {
        if ($this->structuredAnswers === null) {
            $this->structuredAnswers = new MjyStructuredAnswerStore(App()->getDb(), self::engineInstanceId());
        }
        return $this->structuredAnswers;
    }

    public function uploadSessions(): MjyUploadSessionStore
    {
        if ($this->uploadSessions === null) {
            $this->uploadSessions = new MjyUploadSessionStore(App()->getDb(), self::engineInstanceId());
        }
        return $this->uploadSessions;
    }

    /**
     * 引擎答卷字段里的文件列表。
     *
     * @param string|null $rawValue
     * @return array<int, array<string, mixed>>
     */
    public static function decodeUploadedFiles($rawValue): array
    {
        $decoded = json_decode((string) $rawValue, true);
        if (!is_array($decoded)) {
            return [];
        }
        return array_values(array_filter($decoded, 'is_array'));
    }

    // ------------------------------------------------------------- internals

    /**
     * @param array<string, mixed> $question
     */
    private function gateAnswer(int $surveyId, array $question): void
    {
        $fieldName = $question['fieldName'];
        if (!array_key_exists($fieldName, $_POST)) {
            return;
        }
        // 作答者可以把字段发成数组（Qxxx[]=…），直接转字符串会产生警告。
        $posted = is_array($_POST[$fieldName]) ? null : (string) $_POST[$fieldName];
        $result = $this->validatorFor($question)->validate($posted);
        if ($result->isValid()) {
            // 服务端归一化：入库的永远是重新编码过的信封，不是浏览器发来的原文。
            $_POST[$fieldName] = $result->normalisedJson();
            return;
        }
        // 引擎没有「否决提交」的事件，只能把值清空，让必答校验把人留在本页。
        $_POST[$fieldName] = '';
        $this->pageErrors[$fieldName] = $result->errorText();
        Yii::log(
            sprintf('survey %d %s rejected: %s', $surveyId, $fieldName, $result->errorText()),
            CLogger::LEVEL_INFO,
            self::LOG_CATEGORY
        );
    }

    /**
     * @param array<string, mixed> $question
     */
    private function validatorFor(array $question): MjyRepeatingTableValidator
    {
        $attributes = $this->questionAttributes((int) $question['qid']);
        return new MjyRepeatingTableValidator(
            MjyTableColumnSpec::fromJson($attributes[MjyQuestionAttributeDefinitions::COLUMNS] ?? null),
            (int) ($attributes[MjyQuestionAttributeDefinitions::MIN_ROWS] ?? 0),
            (int) ($attributes[MjyQuestionAttributeDefinitions::MAX_ROWS] ?? 0)
        );
    }

    /**
     * @return array<string, string>
     */
    private function questionAttributes(int $qid): array
    {
        $db = App()->getDb();
        $rows = $db->createCommand()
            ->select('attribute, value')
            ->from($db->tablePrefix . 'question_attributes')
            ->where('qid = :qid', [':qid' => $qid])
            ->queryAll();
        return array_column($rows, 'value', 'attribute');
    }

    /**
     * @return array<string, mixed>|null
     */
    private function responseRow(int $surveyId, int $responseId): ?array
    {
        $db = App()->getDb();
        $row = $db->createCommand()
            ->select('*')
            ->from($db->tablePrefix . 'responses_' . $surveyId)
            ->where('id = :id', [':id' => $responseId])
            ->queryRow();
        return $row === false ? null : $row;
    }

    private function questionMap(int $surveyId): MjyThemedQuestionMap
    {
        if (!isset($this->questionMaps[$surveyId])) {
            $this->questionMaps[$surveyId] = MjyThemedQuestionMap::forSurvey(App()->getDb(), $surveyId);
        }
        return $this->questionMaps[$surveyId];
    }

    /**
     * @param int|string|null $surveyId
     * @param CActiveRecord|null $model
     */
    private function projectModel($surveyId, $model): void
    {
        if (empty($surveyId) || $model === null || empty($model->id)) {
            return;
        }
        $this->projectResponse((int) $surveyId, (int) $model->id);
    }

    /**
     * @param int|string|null $surveyId
     * @param CActiveRecord|null $model
     */
    private function purgeModel($surveyId, $model): void
    {
        if (empty($surveyId) || $model === null || empty($model->id)) {
            return;
        }
        $generation = $this->currentGeneration((int) $surveyId);
        $this->structuredAnswers()->purgeResponse((int) $surveyId, $generation, (int) $model->id);
        $this->uploadSessions()->purgeResponse((int) $surveyId, $generation, (int) $model->id);
    }

    private function ensureSchemaOnce(): void
    {
        if (!$this->isSchemaReady) {
            $this->ensureSchema();
        }
    }

    private function generations(): MjyGenerationRef
    {
        if ($this->generations === null) {
            $this->generations = new MjyGenerationRef(App()->getDb());
        }
        return $this->generations;
    }

    /**
     * 扩展题型出问题不能让作答者填不下去；副表缺行由平台的对账流程兜底。
     */
    private function safely(callable $handler): void
    {
        try {
            $handler();
        } catch (\Throwable $exception) {
            Yii::log(
                sprintf('%s: %s', get_class($exception), $exception->getMessage()),
                CLogger::LEVEL_ERROR,
                self::LOG_CATEGORY
            );
        }
    }
}
