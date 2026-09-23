<?php

/**
 * MJY 题型扩展（P0-00.3 原型，WP-02）。
 *
 * 引擎没有「自增表格」题型，也没有任何可以**否决一次作答提交**的插件事件。
 * 本插件把平台需要的三件事接到引擎上：
 *  - newQuestionAttributes：结构化题型的列定义、行数上下限、副表结构版本；
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
    /** 平台为本实例签发的密钥（与 MjyPlatformBridge 同一个变量，ADR 0003）。 */
    private const EVENTS_SECRET_ENV = 'MJY_PLATFORM_EVENTS_SECRET';
    /** 轮换期的上一代实例密钥；轮换结束后删除（ADR 0018 决定 3）。 */
    private const EVENTS_SECRET_PREVIOUS_ENV = 'MJY_PLATFORM_EVENTS_SECRET_PREVIOUS';

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

    /** @var MjyChannelRateLimit|null */
    private $channelRateLimit;

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
        $this->subscribe('newDirectRequest');
    }

    /**
     * 网关↔插件鉴权通道（ADR 0018，契约 plugin-channel-v1）：
     * `GET index.php/plugins/direct?plugin=MjyQuestionExtensions&function=extensionAnswers&…&sig=…`
     *
     * 副表作答是个人数据，所以这条端点与 MjyRuntimePolicy 的 policyStatus 不同——
     * 它**必须验签**。验签之前不碰数据库，验签之前的一切拒绝共用同一个 401 与同一段
     * 响应体，原因只进日志。
     */
    public function newDirectRequest()
    {
        $event = $this->getEvent();
        if ($event->get('target') !== self::$name
            || $event->get('function') !== MjyExtensionAnswerEndpoint::FUNCTION_NAME) {
            return;
        }
        $response = $this->answerChannel()->handle($this->channelQuery(), time());
        if ($response->reason() !== '') {
            // 稳定原因码；绝不带密钥、签名或作答值。
            Yii::log(
                sprintf('extension answer channel refused a request: %s', $response->reason()),
                CLogger::LEVEL_WARNING,
                self::LOG_CATEGORY
            );
        }
        header('Content-Type: application/json; charset=utf-8', true, $response->status());
        header('Cache-Control: no-store');
        echo $response->body();
        App()->end();
    }

    /**
     * 本次请求的查询参数原样交给端点判定。刻意读 $_GET 而不是逐个 getParam()：
     * 端点要能看见**多余的参数**并据此拒绝（封闭白名单）。
     *
     * @return array<string, mixed>
     */
    private function channelQuery(): array
    {
        return is_array($_GET) ? $_GET : [];
    }

    /**
     * 装配端点。**这里一律不碰数据库**：它在验签之前就被构造，任何在此建表或查表的动作
     * 都会让未签名的请求逼出一次真实查询（独立安全审查发现过一次：
     * 急着 ensureSchema() 让匿名洪水每次都跑一遍 schema 查询）。
     * 额度表由 ensureSchema()（激活时）与首次 allow()（验签之后）负责。
     */
    public function answerChannel(): MjyExtensionAnswerEndpoint
    {
        $instanceId = self::engineInstanceId();

        return new MjyExtensionAnswerEndpoint(
            new MjyChannelAuth(self::instanceSecrets(), $instanceId),
            new MjyExtensionAnswerReader(App()->getDb(), $this->structuredAnswers()),
            $this->channelRateLimit(),
            $instanceId
        );
    }

    public function channelRateLimit(): MjyChannelRateLimit
    {
        if ($this->channelRateLimit === null) {
            $this->channelRateLimit = new MjyChannelRateLimit(App()->getDb(), self::engineInstanceId());
        }
        return $this->channelRateLimit;
    }

    /**
     * 当前实例密钥，以及轮换期可选的上一代（ADR 0018 决定 3）。
     * 通道密钥由 MjyChannelAuth 再派生一层，所以引擎侧不需要新的环境变量。
     *
     * @return string[]
     */
    private static function instanceSecrets(): array
    {
        $secrets = [];
        foreach ([self::EVENTS_SECRET_ENV, self::EVENTS_SECRET_PREVIOUS_ENV] as $name) {
            $value = getenv($name);
            if ($value !== false && $value !== '') {
                $secrets[] = (string) $value;
            }
        }

        return $secrets;
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
        // 额度表在激活时就建好，运行时那条路径（首次 allow()）因此几乎永远只是一次命中。
        $this->channelRateLimit()->ensureSchema();
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
        $this->channelRateLimit = null;
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
     * 对本次 POST 里出现的每一道结构化题型做服务端校验。
     *
     * 每道题单独兜异常：一道题的列定义坏掉**不能**让其余题目的校验被跳过，
     * 那是最糟的失败方式（失败开放）。
     *
     * @return array<string, string> 字段名 => 拒绝理由
     */
    public function gatePostedAnswers(int $surveyId): array
    {
        $this->pageErrors = [];
        foreach ($this->questionMap($surveyId)->structuredQuestions() as $question) {
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
     * 重新读取答卷行，把所有结构化题型（自增表格、热力图…）投影进副表。保存钩子与后台补投都走这一条路径。
     *
     * @return array<string, MjyValidationResult> 题目代码 => 校验结果
     */
    public function projectResponse(int $surveyId, int $responseId): array
    {
        $map = $this->questionMap($surveyId);
        if ($map->structuredQuestions() === [] && $map->uploads() === []) {
            return [];
        }
        $row = $this->responseRow($surveyId, $responseId);
        if ($row === null) {
            return [];
        }
        $generation = $this->currentGeneration($surveyId);

        $results = [];
        foreach ($map->structuredQuestions() as $code => $question) {
            $result = $this->validatorFor($question)->validate($row[$question['fieldName']] ?? null);
            $this->structuredAnswers()->recordAnswer(
                $surveyId,
                $generation,
                $responseId,
                $code,
                $this->structureVersionFor($question),
                $result
            );
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
     * 副表结构版本：平台在 .lss 里声明的那一个，消毒后使用。
     * 属性缺失或被改坏时回落到「不知道是哪一版」，而不是假装是第 1 版。
     *
     * @param array<string, mixed> $question
     */
    public function structureVersionFor(array $question): string
    {
        $attributes = $this->questionAttributes((int) $question['qid']);
        return MjyStructuredAnswerStore::normaliseStructureVersion(
            $attributes[MjyQuestionAttributeDefinitions::STRUCTURE_VERSION] ?? null
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
