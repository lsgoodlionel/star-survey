<?php

/**
 * RemoteControl 写答卷接口的闸门（ADR 0021；P0 发现 12、21；ADR 0007「守住 API 面」）。
 *
 * 引擎的 RemoteControl 里有两个方法以**作答者的名义**写答卷，却完全不经过
 * `SurveyIndex`，因此绕开 `beforeSurveyPage` 这道唯一能"拒绝一次提交"的服务端闸门：
 *
 *  - `add_response`（`remotecontrol_handle.php:3374`）：访问规则、按身份限次、
 *    服务端考试计时、题型的服务端校验对它全部失效；
 *  - `update_response`（同文件 `:3474`）：直接 `SurveyDynamic::encryptSave()`，
 *    不跑 EM、不跑必答、不跑任何插件闸门。
 *
 * 平台侧的第一道防线是发布网关只发白名单里的方法（`pubgw/rpc.py`）——引擎管理员口令
 * 只存在网关侧，所以那已经关掉了平台这条路径。本类是**第二道**：任何持有引擎管理员
 * 凭据的人（运维、遗留账号、被翻出来的脚本）走 RemoteControl 也一样发不出来。
 *
 * 两类语义必须分开：
 *  - **作答者提交**永远走 `SurveyIndex` / `UploaderController`，闸门在那里；
 *  - **运维/管理员**用 RemoteControl 是特权通道，但"以作答者名义补一份答卷"不是
 *    运维动作的常态，默认拒绝。确实要做数据修正时，在**那一台实例**上显式打开
 *    `MJY_ALLOW_RPC_RESPONSE_WRITES=1`，每次调用都会留一条警告日志。
 *
 * 本类**没有任何引擎依赖**（不碰 Yii、不碰数据库），判定完全由入参决定，便于单元测试。
 * 与请求生命周期相关的部分（读 `php://input`、写应答、终止请求）留在
 * `MjyRuntimePolicy::beforeControllerAction()` 里。
 */
class MjyRpcGate
{
    /** 引擎控制器与动作：`<engine>/index.php/admin/remotecontrol`。 */
    public const RPC_CONTROLLER = 'admin';
    public const RPC_ACTION = 'remotecontrol';

    /** 以作答者名义写答卷、且绕开 beforeSurveyPage 的方法。 */
    public const BLOCKED_METHODS = ['add_response', 'update_response'];

    /** 打开它就放行（仅供一次性数据修正，按实例配置）。 */
    public const OVERRIDE_ENV = 'MJY_ALLOW_RPC_RESPONSE_WRITES';

    /** 拒绝时回给调用方的 JSON-RPC error 文案前缀；不回显任何参数。 */
    private const REFUSAL = 'Refused by MjyRuntimePolicy: writing responses through RemoteControl bypasses'
        . ' the server-side gates (access rules, attempt limits, exam timing, question validation).'
        . ' Respondent submissions must go through the survey runtime.';

    /** @var bool */
    private $isOverrideEnabled;

    public function __construct(bool $isOverrideEnabled = false)
    {
        $this->isOverrideEnabled = $isOverrideEnabled;
    }

    /**
     * 按环境变量构造。除了 "1"、"true"、"yes"（不分大小写）之外的一切值都视为关闭——
     * 把 "0" 或 "false" 当成"打开"是这类开关最常见的事故。
     */
    public static function fromEnvironment(?string $value): self
    {
        $normalised = strtolower(trim((string) $value));
        return new self(in_array($normalised, ['1', 'true', 'yes'], true));
    }

    public function isOverrideEnabled(): bool
    {
        return $this->isOverrideEnabled;
    }

    /** 这次请求打的是不是 RemoteControl 端点。 */
    public function isRemoteControl(?string $controller, ?string $action): bool
    {
        return strtolower((string) $controller) === self::RPC_CONTROLLER
            && strtolower((string) $action) === self::RPC_ACTION;
    }

    /**
     * 从请求正文里取 JSON-RPC / XML-RPC 的方法名。
     *
     * 认不出来返回 null——那不等于"安全"，见 {@see isBlocked()} 的兜底。
     */
    public function methodOf(string $body): ?string
    {
        $decoded = json_decode($body, true);
        if (is_array($decoded) && isset($decoded['method']) && is_string($decoded['method'])) {
            return $decoded['method'];
        }
        // XML-RPC：RPCInterface 设成 'xml' 时走 Zend_XmlRpc_Server。
        if (preg_match('#<methodName>\s*([A-Za-z0-9_.:/]+)\s*</methodName>#', $body, $matches) === 1) {
            return $matches[1];
        }
        return null;
    }

    /**
     * 这次请求要不要拒。按正文形态分三层，每一层都只往"更容易拒"的方向偏：
     *
     *  1. **JSON**：用 `json_decode` 取 `method`，与引擎的 `LSjsonRPCServer::handle()`
     *     是同一个函数，重复键、转义写法的取舍完全一致，不存在解析差。
     *     因此可以放心地"只按这一个名字判"，不会误伤把 "add_response" 写进问卷文案的正常调用。
     *  2. **XML-RPC**：这里没法复用引擎的 `Zend_XmlRpc_Server`，所以**每一个**
     *     `<methodName>` 都要看，任意一个命中就拒。只看第一个会被"诱饵"劫持——
     *     注释或前置元素里塞一个无害的名字，真正的 `methodCall` 排在后面，
     *     引擎按 XML 语法读到的是后者，只看第一个的闸门读到的却是前者。
     *     代价是注释里出现被禁方法名也会被拒，那是安全的那一边。
     *  3. **两种都解析不出来**：正文里出现被禁方法名就按拒处理（失败即关闭）。
     */
    public function isBlocked(string $body): bool
    {
        if ($this->isOverrideEnabled) {
            return false;
        }
        // JSON：与引擎用的是同一个 json_decode，重复键、转义写法的取舍完全一致，
        // 不存在"我们读到 A、引擎读到 B"的解析差。
        $decoded = json_decode($body, true);
        if (is_array($decoded) && isset($decoded['method']) && is_string($decoded['method'])) {
            return $this->hasBlockedName([$decoded['method']]);
        }
        // XML-RPC：这里没法复用引擎的解析器，所以**每一个** <methodName> 都要看。
        // 只看第一个的话，`<x><methodName>get_fieldmap</methodName></x>
        // <methodCall><methodName>add_response</methodName>…` 就能绕过去。
        if (preg_match_all('#<methodName>\s*([A-Za-z0-9_.:/]+)\s*</methodName>#', $body, $matches) > 0) {
            return $this->hasBlockedName($matches[1]);
        }
        // 两种都解析不出来：正文里出现被禁方法名就按拒处理（失败即关闭）。
        foreach (self::BLOCKED_METHODS as $blocked) {
            if (stripos($body, $blocked) !== false) {
                return true;
            }
        }
        return false;
    }

    /** 方法名比较一律小写去空白：PHP 的方法调用本身就不区分大小写。 */
    private function hasBlockedName(array $names): bool
    {
        foreach ($names as $name) {
            if (in_array(strtolower(trim((string) $name)), self::BLOCKED_METHODS, true)) {
                return true;
            }
        }
        return false;
    }

    /**
     * 拒绝时回给调用方的 JSON-RPC 应答正文。
     *
     * 形状与引擎自己的失败应答一致（`LSjsonRPCServer::handle`：`error` 是字符串），
     * 这样现成的客户端不用改就能识别。**不回显请求参数**：里面有会话 key 与答案值。
     */
    public function refusalBody(string $body): string
    {
        $decoded = json_decode($body, true);
        $id = is_array($decoded) && array_key_exists('id', $decoded) && (is_int($decoded['id'])
            || is_string($decoded['id'])) ? $decoded['id'] : null;
        return (string) json_encode(['id' => $id, 'result' => null, 'error' => self::REFUSAL]);
    }

    /** 记进日志的一行；只有方法名，没有参数。 */
    public function logLine(string $body, string $verdict): string
    {
        $method = $this->methodOf($body);
        return sprintf('RemoteControl %s: %s', $method === null ? '<unparsable body>' : $method, $verdict);
    }
}
