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
 *
 * WP-04（ADR 0016）在同一个闸门上加了访问规则：时间窗、IP／地区、访问密码、
 * 按身份限次、作答时长。规则由发布网关编进 LSS 的 plugin_settings，按 sid 各存一份；
 * `newDirectRequest` 的 policyStatus 让网关在激活前回读核对。
 */
class MjyRuntimePolicy extends \LimeSurvey\PluginManager\PluginBase
{
    public const DEFAULT_ENGINE_INSTANCE_ID = 'local-dev';
    private const ENGINE_INSTANCE_ENV = 'MJY_ENGINE_INSTANCE_ID';
    private const LOG_CATEGORY = 'plugin.MjyRuntimePolicy';
    private const DENY_TEMPLATE_TYPE = 'survey-notstart';
    private const ADMISSION_SESSION_KEY = 'mjyruntimepolicy_admission';
    private const UNLOCK_SESSION_KEY = 'mjyruntimepolicy_unlocked';
    private const PASSWORD_FAILURES_SESSION_KEY = 'mjyruntimepolicy_password_failures';
    /** 同一会话连续输错这么多次就锁住（清 Cookie 可重置，见 ADR 0016 绕过清单 4）。 */
    private const MAX_PASSWORD_FAILURES = 5;
    private const DEVICE_COOKIE = 'mjy_device';
    private const DEVICE_COOKIE_SECONDS = 31536000;
    private const DEVICE_PATTERN = '/\A[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\z/';
    private const STATUS_FUNCTION = 'policyStatus';

    protected $storage = 'DbStorage';
    protected static $description = 'MJY: server-side exam deadline and hard quota lease';
    protected static $name = 'MjyRuntimePolicy';

    /** @var string[] */
    public $allowedPublicMethods = [];

    /** @var MjyPolicyEngine|null */
    private $engine;

    /** @var bool */
    private $isSchemaReady = false;

    /** @var MjyAccessGate|null */
    private $accessGate;

    /** @var string|null 本次请求的设备号（首次访问时新发） */
    private $deviceId;

    public function init()
    {
        $this->subscribe('beforeActivate');
        $this->subscribe('beforeSurveyPage');
        $this->subscribe('afterSurveyComplete');
        $this->subscribe('cron');
        $this->subscribe('newDirectRequest');
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
        $access = $this->decideAccess($surveyId);
        if ($access !== null && $access[1]->reason() === MjyPolicyDecision::REASON_PASSWORD) {
            $this->askForPassword($surveyId, $access[0]);
            return;
        }
        if ($access !== null && !$access[1]->isAllowed()) {
            $this->deny($surveyId, $access[1]);
            return;
        }
        $decision = $this->decide($surveyId);
        if ($decision->isAllowed()) {
            return;
        }
        $this->deny($surveyId, $decision);
    }

    /**
     * 发布网关回读：`GET index.php/plugins/direct?plugin=MjyRuntimePolicy&function=policyStatus&sid=N`。
     * 只回份数、能否解析与摘要，不回策略内容（密码哈希不出引擎）。
     */
    public function newDirectRequest()
    {
        $event = $this->getEvent();
        if ($event->get('target') !== self::$name || $event->get('function') !== self::STATUS_FUNCTION) {
            return;
        }
        $surveyId = (int) App()->getRequest()->getParam('sid');
        try {
            $status = ['plugin' => self::$name, 'active' => true]
                + $this->accessPolicies()->status($surveyId);
        } catch (\Throwable $exception) {
            $this->logFailure($exception);
            $status = ['plugin' => self::$name, 'active' => true, 'surveyId' => $surveyId, 'error' => 'unavailable'];
        }
        header('Content-Type: application/json; charset=utf-8');
        header('Cache-Control: no-store');
        echo json_encode($status);
        App()->end();
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
            $policy = $this->accessPolicies()->find($surveyId);
            if ($policy !== null) {
                $now = $this->engine()->clock()->nowUtc();
                $this->accessGate()->confirm($policy, $this->accessRequest($surveyId, $policy), $now);
            }
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

    /**
     * 访问规则判定。没有策略返回 null；策略读不出或判定出错一律拒绝（fail closed）。
     *
     * @return array{0: MjyAccessPolicy|null, 1: MjyPolicyDecision}|null
     */
    private function decideAccess(int $surveyId): ?array
    {
        try {
            $policy = $this->accessPolicies()->find($surveyId);
            if ($policy === null) {
                return null;
            }
            $this->readyEngine();
            $request = $this->accessRequest($surveyId, $policy);
            return [$policy, $this->accessGate()->evaluate($policy, $request, $this->engine()->clock()->nowUtc())];
        } catch (\Throwable $exception) {
            $this->logFailure($exception);
            return [null, MjyPolicyDecision::denyUnavailable()];
        }
    }

    private function accessPolicies(): MjyAccessPolicyStore
    {
        return new MjyAccessPolicyStore(App()->getDb(), (int) $this->id);
    }

    private function accessGate(): MjyAccessGate
    {
        if ($this->accessGate === null) {
            $this->accessGate = new MjyAccessGate(
                App()->getDb(),
                self::engineInstanceId(),
                MjyCsvRegionResolver::fromEnvironment()
            );
        }
        return $this->accessGate;
    }

    private function accessRequest(int $surveyId, MjyAccessPolicy $policy): MjyAccessRequest
    {
        $survey = Survey::model()->findByPk($surveyId);
        $hasTokenTable = $survey !== null && $survey->hasTokensTable;
        return new MjyAccessRequest(
            $surveyId,
            MjyIpRules::clientIp($_SERVER, MjyIpRules::trustedProxiesFromEnvironment()),
            $hasTokenTable ? $this->validToken($surveyId) : null,
            $policy->tracksAttempts() ? $this->deviceId() : '',
            ($_SESSION[self::UNLOCK_SESSION_KEY][$surveyId] ?? null) === $policy->digest(),
            $hasTokenTable
        );
    }

    /**
     * 只认参与者表里真实存在的 token：随手编一个 token 参数不能换来一个新身份。
     */
    private function validToken(int $surveyId): ?string
    {
        $token = $_SESSION['responses_' . $surveyId]['token'] ?? App()->getRequest()->getParam('token');
        if (!is_string($token) || $token === '') {
            return null;
        }
        return Token::model($surveyId)->findByToken($token) === null ? null : $token;
    }

    /**
     * 设备号：插件自己发的长效 HttpOnly Cookie。只是风险信号——清 Cookie 就是新设备。
     */
    private function deviceId(): string
    {
        if ($this->deviceId !== null) {
            return $this->deviceId;
        }
        $existing = $_COOKIE[self::DEVICE_COOKIE] ?? null;
        if (is_string($existing) && preg_match(self::DEVICE_PATTERN, $existing) === 1) {
            return $this->deviceId = $existing;
        }
        $this->deviceId = MjyQuotaLeaseStore::uuidV4();
        if (!headers_sent()) {
            setcookie(self::DEVICE_COOKIE, $this->deviceId, [
                'expires' => time() + self::DEVICE_COOKIE_SECONDS,
                'path' => '/',
                'secure' => App()->getRequest()->getIsSecureConnection(),
                'httponly' => true,
                'samesite' => 'Lax',
            ]);
        }
        return $this->deviceId;
    }

    /**
     * 密码页：校验 POST 过来的密码；通过则记"本 sid 本策略已解锁"并重定向回原地址（GET），
     * 否则渲染密码页。密码只在内存里比对，从不写日志。
     */
    private function askForPassword(int $surveyId, MjyAccessPolicy $policy): void
    {
        $request = App()->getRequest();
        $submitted = $request->getIsPostRequest() ? $request->getPost(MjyAccessPage::PASSWORD_FIELD) : null;
        $failures = (int) ($_SESSION[self::PASSWORD_FAILURES_SESSION_KEY][$surveyId] ?? 0);
        if ($failures >= self::MAX_PASSWORD_FAILURES) {
            $this->deny(
                $surveyId,
                MjyPolicyDecision::denyUnavailable(),
                '密码错误次数过多',
                '访问密码错误次数过多，请稍后再试或联系问卷发布方。'
            );
            return;
        }
        $error = null;
        if (is_string($submitted)) {
            if (MjyPasswordHash::verify($submitted, (string) $policy->passwordHash())) {
                $_SESSION[self::UNLOCK_SESSION_KEY][$surveyId] = $policy->digest();
                unset($_SESSION[self::PASSWORD_FAILURES_SESSION_KEY][$surveyId]);
                App()->getController()->redirect($request->getUrl());
                return;
            }
            $_SESSION[self::PASSWORD_FAILURES_SESSION_KEY][$surveyId] = $failures + 1;
            $error = '访问密码不正确，请重试。';
            Yii::log(sprintf('survey %d: wrong access password', $surveyId), CLogger::LEVEL_INFO, self::LOG_CATEGORY);
        }
        header('Content-Type: text/html; charset=utf-8');
        header('Cache-Control: no-store');
        echo MjyAccessPage::render($request->getUrl(), $request->csrfTokenName, $request->getCsrfToken(), $error);
        App()->end();
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
    private function deny(int $surveyId, MjyPolicyDecision $decision, ?string $title = null, ?string $message = null): void
    {
        $title = $title ?? $this->denyTitle($decision);
        $message = $message ?? $decision->message();
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
                [$message],
                null,
                [$title]
            );
            return;
        }
        // 文件上传等入口用的不是 SurveyController（UploaderController.php:389
        // 同样派发 beforeSurveyPage），那里只能用 HTTP 状态码拒绝。
        throw new CHttpException(403, $message);
    }

    private function denyTitle(MjyPolicyDecision $decision): string
    {
        $titles = [
            MjyPolicyDecision::REASON_DEADLINE => '考试时间已结束',
            MjyPolicyDecision::REASON_QUOTA => '名额已满',
            MjyPolicyDecision::REASON_NOT_OPEN => '问卷尚未开放',
            MjyPolicyDecision::REASON_CLOSED => '问卷已截止',
            MjyPolicyDecision::REASON_NETWORK => '当前网络不可作答',
            MjyPolicyDecision::REASON_REGION => '当前地区不可作答',
            MjyPolicyDecision::REASON_REGION_UNKNOWN => '无法确认所在地区',
            MjyPolicyDecision::REASON_TOKEN_REQUIRED => '需要邀请码',
            MjyPolicyDecision::REASON_ATTEMPTS => '已达作答次数上限',
            MjyPolicyDecision::REASON_DURATION => '作答时间已到',
        ];
        return $titles[$decision->reason()] ?? '暂时无法作答';
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
