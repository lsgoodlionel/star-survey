<?php

/**
 * MJY 运行时策略插件（P0-00.7 原型，WP-23 / R23-06）。
 *
 * 引擎缺的两件事：
 *  1. 没有"每场考试各自倒计时"的服务端概念。问卷级 `expires` 是全局到期时刻
 *     （SurveyIndex.php:387），题目级 `time_limit` 只是前端 JS 倒计时
 *     （qanda_helper.php:394 起）；刷新、重连、换浏览器都不受约束。
 *  2. 自带配额是"不加锁 COUNT → 比较 qlimit → 事后写 submitdate"，中间没有
 *     事务（Quota.php:131、Quotas.php:518、em_manager_helper.php:5435），
 *     并发下最后一个名额会同时发给多个人。
 *
 * 本插件只做两件事，都挂在 `beforeSurveyPage` 上——它在 SurveyIndex.php:228
 * 派发，早于 SurveyRuntimeHelper::run()（同文件 :688）处理 POST，所以在这里
 * 终止请求可以让整次提交连答案都写不进去：
 *  - 按服务端时钟定死每场考试的截止时刻，超时即拒绝；
 *  - 进入考场之前先领一张名额租约，领不到即拒绝。
 *
 * 失败语义与 MjyPlatformBridge 不同，这里是刻意的：
 *  - 记账类失败（交卷时确认租约、cron 回收过期租约）只写日志，不影响作答者；
 *  - 闸门本身查不下去时**拒绝**（fail closed）。一个失败即放行的策略插件等于
 *    没有策略，考试场景不接受这种默认。
 *
 * 商业逻辑（考多久、几个名额、谁能进场）一律由平台下发，插件只执行
 * （许可分析结论 7：插件按衍生作品处理，不放商业逻辑）。
 */
class MjyRuntimePolicy extends \LimeSurvey\PluginManager\PluginBase
{
    public const DEFAULT_ENGINE_INSTANCE_ID = 'local-dev';
    private const ENGINE_INSTANCE_ENV = 'MJY_ENGINE_INSTANCE_ID';
    private const LOG_CATEGORY = 'plugin.MjyRuntimePolicy';
    private const DENY_TEMPLATE_TYPE = 'survey-notstart';
    private const ADMISSION_SESSION_KEY = 'mjyruntimepolicy_admission';

    protected $storage = 'DbStorage';
    protected static $description = 'MJY: server-side exam deadline and hard quota lease';
    protected static $name = 'MjyRuntimePolicy';

    /** @var string[] */
    public $allowedPublicMethods = [];

    /** @var MjyPolicyEngine|null */
    private $engine;

    /** @var bool */
    private $isSchemaReady = false;

    public function init()
    {
        $this->subscribe('beforeActivate');
        $this->subscribe('beforeSurveyPage');
        $this->subscribe('afterSurveyComplete');
        $this->subscribe('cron');
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
        $this->engine()->ensureSchema();
        $this->isSchemaReady = true;
    }

    /**
     * 忘掉"本次请求已确认建过表"（测试会删表）。
     */
    public function resetSchemaState(): void
    {
        $this->isSchemaReady = false;
    }

    public function engine(): MjyPolicyEngine
    {
        if ($this->engine === null) {
            $this->engine = new MjyPolicyEngine(App()->getDb(), self::engineInstanceId());
        }
        return $this->engine;
    }

    /**
     * 平台下发策略的入口（原型阶段直接调用，正式版走平台网关）。
     */
    public function applyPolicy(int $surveyId, int $examDurationSeconds, int $quotaSlotLimit, int $leaseTtlSeconds): void
    {
        $this->engine()->applyPolicy($surveyId, $examDurationSeconds, $quotaSlotLimit, $leaseTtlSeconds);
        $this->isSchemaReady = true;
    }

    /**
     * 唯一的闸门。判定放在 try 里（基础设施异常 → 拒绝），拒绝动作放在 try 外，
     * 否则 renderExitMessage 抛出的控制流会被自己的 catch 吞掉。
     */
    public function beforeSurveyPage()
    {
        $surveyId = (int) $this->getEvent()->get('surveyId');
        if ($surveyId <= 0) {
            return;
        }
        $decision = $this->decide($surveyId);
        if ($decision->isAllowed()) {
            return;
        }
        $this->deny($surveyId, $decision);
    }

    /**
     * 交卷时把名额落定：之后租约 TTL 到点也不再归还。
     */
    public function afterSurveyComplete()
    {
        $this->safely(function () {
            $surveyId = (int) $this->getEvent()->get('surveyId');
            if ($surveyId <= 0) {
                return;
            }
            $this->readyEngine()->confirmEntry($surveyId, $this->sessionKey($surveyId));
        });
    }

    /**
     * 周期性把过期租约标成 expired，让状态可审计（名额本来就已经不再计入）。
     */
    public function cron()
    {
        $this->safely(function () {
            $this->reapExpiredLeases();
        });
    }

    /**
     * @return int 本轮回收的过期租约数
     */
    public function reapExpiredLeases(): int
    {
        // 插件可能是被直接改数据库启用的（没走 beforeActivate），先确保表存在。
        $this->readyEngine();
        $reaped = 0;
        foreach (Survey::model()->findAllByAttributes(['active' => 'Y']) as $survey) {
            $this->safely(function () use ($survey, &$reaped) {
                $reaped += $this->readyEngine()->reapExpiredLeases((int) $survey->sid);
            });
        }
        return $reaped;
    }

    /**
     * 考场身份。
     *
     * 优先用准考证 token：换浏览器、清 Cookie、断线重连都还是同一个人，这是
     * 考试唯一可靠的身份。
     *
     * 没有 token 时退化为一张放在会话数据里的入场券，**不能用 PHP 会话 id**：
     * 引擎在 `resetAllSessionVariables()`（frontend_helper.php:1406）里调
     * `regenerateID(true)`，同一个作答者的 GET 与 POST 会拿到不同的会话 id，
     * 用它当键会让一个人领走多张租约（实测：一次完整作答产生两张）。
     * `regenerateID(true)` 会迁移会话数据，所以入场券本身是稳定的。
     *
     * 入场券只挡得住"换标签页"，挡不住"清 Cookie"——所以真正的考试必须发 token。
     */
    public function sessionKey(int $surveyId): string
    {
        $token = $_SESSION['responses_' . $surveyId]['token'] ?? null;
        if (empty($token)) {
            $token = App()->getRequest()->getParam('token');
        }
        if (!empty($token) && is_string($token)) {
            return 'token:' . $token;
        }
        return 'admission:' . $this->admissionTicket($surveyId);
    }

    private function admissionTicket(int $surveyId): string
    {
        $existing = $_SESSION[self::ADMISSION_SESSION_KEY][$surveyId] ?? null;
        if (!empty($existing) && is_string($existing)) {
            return $existing;
        }
        $ticket = MjyQuotaLeaseStore::uuidV4();
        $_SESSION[self::ADMISSION_SESSION_KEY][$surveyId] = $ticket;
        return $ticket;
    }

    private function decide(int $surveyId): MjyPolicyDecision
    {
        try {
            return $this->readyEngine()->evaluateEntry($surveyId, $this->sessionKey($surveyId));
        } catch (\Throwable $exception) {
            $this->logFailure($exception);
            return MjyPolicyDecision::denyUnavailable();
        }
    }

    /**
     * 终止整次请求。`renderExitMessage` 末尾是 `App()->end()`
     * （SurveyController.php:193），而 `beforeSurveyPage` 早于 POST 处理，
     * 所以被拒绝的提交连答案都不会落库。
     */
    private function deny(int $surveyId, MjyPolicyDecision $decision): void
    {
        Yii::log(
            sprintf('denied survey %d: %s', $surveyId, $decision->reason()),
            CLogger::LEVEL_INFO,
            self::LOG_CATEGORY
        );
        $controller = App()->getController();
        if (method_exists($controller, 'renderExitMessage')) {
            $controller->renderExitMessage(
                $surveyId,
                self::DENY_TEMPLATE_TYPE,
                [$decision->message()],
                null,
                [$this->denyTitle($decision)]
            );
            return;
        }
        // 文件上传等入口用的不是 SurveyController（UploaderController.php:389
        // 同样派发 beforeSurveyPage），那里只能用 HTTP 状态码拒绝。
        throw new CHttpException(403, $decision->message());
    }

    private function denyTitle(MjyPolicyDecision $decision): string
    {
        if ($decision->reason() === MjyPolicyDecision::REASON_DEADLINE) {
            return '考试时间已结束';
        }
        if ($decision->reason() === MjyPolicyDecision::REASON_QUOTA) {
            return '名额已满';
        }
        return '暂时无法作答';
    }

    private function readyEngine(): MjyPolicyEngine
    {
        if (!$this->isSchemaReady) {
            $this->ensureSchema();
        }
        return $this->engine();
    }

    /**
     * 记账类失败只写日志：不能因为一次 cron 或一次确认失败就拦住作答者。
     */
    private function safely(callable $handler): void
    {
        try {
            $handler();
        } catch (\Throwable $exception) {
            $this->logFailure($exception);
        }
    }

    private function logFailure(\Throwable $exception): void
    {
        Yii::log(
            sprintf('%s: %s', get_class($exception), $exception->getMessage()),
            CLogger::LEVEL_ERROR,
            self::LOG_CATEGORY
        );
    }
}
