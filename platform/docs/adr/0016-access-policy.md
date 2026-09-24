# ADR 0016：访问与作答规则（WP-04.1 / 04.2）

- 状态：已实现（网关＋插件＋平台草稿），真引擎端到端验证见文末
- 日期：2026-09-22
- 关联：WP-04 切片 04.1（密码／邀请／验证码／限次）、04.2（时区窗口／时长／IP／地区）；
  需求 R04-01、R04-02（部分）、R04-03（部分）、R04-04、R04-05（部分）；
  前置 ADR 0005（发布）、0007（考试策略插件）、0009（发布网关）、0012（改版再发布）
- 契约：[`platform/contracts/survey-access-policy-v1.md`](../../contracts/survey-access-policy-v1.md)

## 背景

蓝图要求："商业逻辑一律由平台下发，插件只执行"，验收"改系统时间不延期；续答令牌不互用"。
引擎自带的相关能力与缺口：

| 需求 | 引擎现状 | 够不够 |
|---|---|---|
| 开放／截止 | `surveys.startdate/expires`，作答入口用 Web 进程 `gmdate()` 比较（`SurveyIndex.php:393/414`） | 服务端判定，但只认 Web 节点时钟、只有英文默认文案、没有时区概念 |
| 验证码 | `usecaptcha`（`X`＝只在进入问卷时），`isCaptchaEnabled()`（`common_helper.php:2813`） | 够；**缺 GD 扩展时静默关闭** |
| 邀请 | 参与者表 token ＋ `usesleft`；7.x 另有 `access_mode`（`O` 时有参与者表也照样放没 token 的人进来，`C` 才强制） | 够，但必须写 `access_mode=C` |
| 统一访问密码 | 无 | 需要插件 |
| 限次 | 只有 token `usesleft`；匿名问卷完全没有 | 需要插件 |
| 作答时长 | 题目级 `time_limit` 是纯前端 JS（ADR 0007） | 需要插件 |
| IP／地区 | 无；而且 `getIPAddress()`（`common_helper.php:5287`）**无条件信任客户端的 `Client-IP` / `X-Forwarded-For` 头** | 需要插件，且不能用引擎的取 IP 函数 |

## 决定

### 1. 定义里加一个可选的顶层 `policy` 块，不升定义版本

`policy` 在定义版本 1 和 2 里都可以出现，自带 `policyVersion: 1`。完整字段见契约。

不升 `definitionVersion` 的理由：策略块与题目结构（v1）和逻辑（v2）正交，升版本会让
"v2＋策略"变成第三个版本号，平台与其他车道（题型）都要改接受集合 `{1,2}`。
代价：**旧网关会忽略未知顶层键**——平台比网关新时策略会被静默丢掉。网关与平台同仓同版本
发布（ADR 0004），部署时必须一起升级；**平台侧核对网关回执里的 `policyDigest`**（决定 5）正是为了在
这件事没做到时把发布挡下来，而不是让一份"看着受保护、实际毫不设防"的问卷上线。

策略块里任何未知键、类型不对、取值越界都是 **422 `validate`**（`E_POLICY_*`），不是 400：
它是语义错误，作者需要一张完整清单；也绝不静默丢掉一条限制。

### 2. 引擎权威的交给引擎，其余交给插件；时间窗两边都守

| 规则 | 编译到 | 谁判定 | 说明 |
|---|---|---|---|
| 验证码 `access.captcha` | 原生 `usecaptcha=X` | **引擎** | 插件做不出比引擎更好的验证码；与 `settings.usecaptcha` 同时出现 → 422 |
| 邀请 `access.invitationRequired` | 原生参与者表（`participants`）＋ `access_mode=C` | **引擎** | 没有参与者 → 422；与 `settings.access_mode` 同时出现 → 422 |
| 开放／截止 `window` | 原生 `startdate/expires`（UTC）**＋**插件 | **两者** | 插件用数据库时钟＋中文提示；原生是插件失效时的兜底；两边取严 |
| 访问密码 `access.passwordHash` | 插件 | 插件 | 引擎没有问卷级密码 |
| 限次 `limits.responses[]` | 插件 | 插件 | 按 token／设备／IP 维度组合 |
| 作答时长 `limits.maxDurationSeconds` | 插件 | 插件 | 复用 ADR 0007 截止时刻表 |
| IP `network.allowIps/denyIps` | 插件 | 插件 | 自己取客户端 IP（见决定 7） |
| 地区 `network.allowRegions/denyRegions` | 插件 | 插件 | 可插拔数据源（见决定 8） |

### 3. 时间：作者写本地时刻＋时区，网关换算成 UTC，运行时只比 UTC

- 作者写 `"opensAt": "2026-10-01T09:00"`＋`"timezone": "Asia/Shanghai"`（IANA 名）。
  平台存草稿时若没写时区，填入默认 `Asia/Shanghai`（租户级时区配置尚不存在，见缺口）。
- 网关用 `zoneinfo` 换算成 UTC；**夏令时缺口里不存在的时刻、回拨重叠里有歧义的时刻一律 422**
  （`E_POLICY_LOCAL_TIME`），不替作者猜。中国默认时区没有夏令时，但 `America/New_York`
  等必须正确——单元测试覆盖了两种边界。
- 插件与原生都只比较 UTC：插件用 `MjyServerClock`（数据库 `UTC_TIMESTAMP()`），
  原生用 Web 进程 `gmdate()`。**请求里的任何时间（`Date` 头、表单字段、Cookie）都不是输入**，
  所以改客户端系统时间不会延期（端到端场景 W2 实测）。
- 开放区间是 `[opensAt, closesAt)`。本地时刻原文与时区一并下发，只用于拒绝页的中文提示。

### 4. 下发：编进 LSS 的 `plugin_settings`，每个 sid 一份，发布后回读核对

网关把策略编译成一段规范 JSON（`schema: "mjy-access-policy/1"`，键排序、纯 ASCII），
作为 `plugin_settings` 行（插件 `MjyRuntimePolicy`，键 `mjy_access_policy`）写进 LSS。
引擎导入时把它写进 `lime_plugin_settings`（`model='Survey', model_id=<新 sid>`，
`import_helper.php:3447`）。于是：

- **按已发布 sid 版本化**：改版再发布是新 sid（ADR 0012），新旧版本各有各的策略，互不覆盖；
- **幂等**：同一 sid 只写一次，导入之外没有第二条写路径，不存在 ADR 0003 里那种并发重复行；
- **回滚一致**：发布失败时 `delete_survey` 连同插件设置一起删（`Survey.php:338`）；
- **不需要新的凭据通道**：网关已有的 RemoteControl 导入就是下发。

导入端有一个静默坑：插件没在 `lime_plugins` 注册时只写一条导入警告，而 RemoteControl 的
`import_survey` 不返回警告——策略会**静默丢失**。所以网关在 `apply` 阶段（激活之前）读插件的
状态端点 `GET index.php/plugins/direct?plugin=MjyRuntimePolicy&function=policyStatus&sid=<sid>`，
要求插件**已激活**、存的正好是一份、能解析、且 SHA-256 与网关编译出的摘要一致；
否则 `apply` 失败并回滚（`E_POLICY_NOT_ENFORCED`）。端点只回
`{surveyId, policyDigest, valid, rows}`，不回策略内容（密码哈希不出引擎）。
没有配置回读通道时，带策略的定义一律不发布（`E_POLICY_UNVERIFIED`）。

发布结果里带 `policyDigest`，平台可以存档比对。

### 5. 密码：平台哈希，插件校验，明文不出平台

- 作者在草稿里写 `access.password`（明文，只写不读）；平台保存草稿时换成
  `access.passwordHash`（`pbkdf2-sha256$<迭代>$<salt>$<hash>`，16 字节随机盐、600,000 次
  （OWASP 2023 建议值）、32 字节输出，base64），明文不落库、不进日志、不进发给网关的请求。
- 网关见到 `access.password` 直接 422（`E_POLICY_PLAINTEXT_PASSWORD`）；哈希格式不对或迭代
  次数低于 100,000 也 422。
- 插件在 `beforeSurveyPage` 里先查时间窗／IP／地区，再要密码：渲染一张独立的中文密码页
  （带引擎 CSRF 令牌）；`hash_pbkdf2`＋`hash_equals` 校验；通过后在会话里记下
  "sid＋策略摘要已解锁"，重定向回原地址（GET）。**解锁按 sid 记**：A 卷解锁不等于 B 卷解锁，
  改版再发布（新 sid）要重新输入。
- 选 PBKDF2 而不是 bcrypt：JDK、PHP、Python 标准库都有，不引新依赖。

### 6. 限次与时长：按"身份维度 × 第几次作答"记账，复用 ADR 0007 的两张表

`limits.responses: [{by, max}]`，`by ∈ {token, device, ip}`，多条同时生效（R04-03 "组合"）：

| 维度 | 身份键 | 可被绕过的方式 |
|---|---|---|
| `token` | 引擎参与者 token（平台发的一人一码） | 码本身被转交；**这是唯一可靠的身份** |
| `device` | 插件发的长效 Cookie `mjy_device`（HttpOnly，一年） | 清 Cookie、换浏览器、无痕窗口 |
| `ip` | 客户端 IP（决定 7） | 换网络／代理；同一出口 NAT 下的多人会被误伤 |

设备与 IP 只是风险信号（蓝图 §7.1），拒绝页说明可联系发布方申诉。

- 主身份＝有有效 token 用 token，否则设备；"第几次"＝主身份已确认的次数＋1；
  一次作答的持有者是 `<主身份>#<第几次>`。
- 每条规则一个名额锚点 `person:<sid>:<维度身份>`（ADR 0007 的租约表，上限 `max`）；主身份即使没有
  规则也有一个锚点（不限量），只用来记"交过几次"。同一个人刷新、换标签页、断线重连命中同一张
  租约；同一 IP 下的不同设备是不同持有者，各占 IP 锚点的一个名额。交卷（`afterSurveyComplete`）
  把各锚点上的租约确认，下一次进场就是"第 n+1 次"。多个维度按 token → device → ip 依次预留，
  任何一个满了就把本次新领的租约释放掉再拒绝。
- 放弃的作答：租约到期（时长＋5 分钟，未设时长则 24 小时）后名额归还。
- **时长**按"最强身份（有 token 用 token，否则设备）#第几次"在截止时刻表里一次写死，
  从首次进场起算；清 Cookie 对 token 身份无效，对设备身份等于换了一个人（已记录）。
  超时的提交在 `beforeSurveyPage` 被拒，答案不落库（ADR 0007 决定 4）。
- `token` 维度要求 `access.invitationRequired=true`（否则 422）：开放访问（`access_mode=O`）的问卷
  没有 token 也能答，按 token 限次形同虚设（端到端实测：`O` 模式下随手编的 token 能打开问卷，
  交卷时才 403）。插件只认参与者表里真实存在的 token；请求里还没有有效 token 时，闭合访问就交给
  引擎的 token 入口页，否则拒绝（fail closed）。

### 7. 客户端 IP：只信 `REMOTE_ADDR`，代理必须显式声明

引擎的 `getIPAddress()` 先看 `HTTP_CLIENT_IP`、再看 `X-Forwarded-For`——任何客户端都能伪造。
插件自己取：默认只用 `REMOTE_ADDR`；只有 `REMOTE_ADDR` 落在环境变量
`MJY_TRUSTED_PROXIES`（逗号分隔的 CIDR）里时，才从 `X-Forwarded-For` 右往左取第一个
不可信的地址。IPv4／IPv6 CIDR 都支持。

### 8. 地区：可插拔数据源，没有数据按配置拒绝或放行

仓库里**没有**附带任何 GeoIP 数据集，本 ADR 也没有下载任何数据集。插件定义了
`MjyRegionResolver` 接口，内置一个离线 CSV 实现（`MJY_GEOIP_CSV` 指向
`起始IP,结束IP,地区码` 形式的文件，兼容 DB-IP Lite（CC BY 4.0）与 IP2Location LITE
（CC BY-SA 4.0）导出的国家级 CSV；选哪一份、署名义务，由交付方按许可决定）。
没有配置数据源、或查不到时，按策略里的 `network.regionUnknown`（`deny` 缺省／`allow`）处理
（R04-05 "无法定位按配置拒绝或回退"）。⏳ 省级地区、授权定位（浏览器／微信定位）未做。

### 9. 失败语义沿用 ADR 0007

闸门查不下去（策略读不出、有多份、解析失败、数据库异常）一律拒绝；记账（确认租约）失败只写日志。
插件日志只写 sid 与拒绝原因，永不写密码、token、IP 原文以外的请求内容。

## 仍然可以绕过的（诚实清单）

1. RemoteControl `add_response` / `update_response` 完全不经过 `beforeSurveyPage`（ADR 0007、progress 21）。
2. 设备维度：清 Cookie 即新身份；IP 维度：换网络即新身份。只有 token 是可靠身份。
3. 同一身份两个会话在**同一瞬间**交卷，两份答卷都会落库（确认是"持有中才能确认"，第二次确认失败，
   名额计数不超，但答卷已写）。插件没有在交卷前再加一次行锁。
4. 密码错误次数只按会话计（5 次后该会话锁定）；清 Cookie 可以重置，靠 PBKDF2 成本减速，
   按 IP 的全局限流未做。
5. 验证码依赖引擎的 GD 扩展，缺扩展时引擎静默关闭验证码；部署检查必须包含 GD。
6. 原生时间窗用 Web 节点时钟，插件用数据库时钟，两者取严；Web 节点时钟**慢**时由插件兜住，
   插件被停用时只剩原生（仍是服务端时钟）。

## 验证

| 层 | 位置 | 结果 |
|---|---|---|
| 网关单元 | `platform/tools/publish-gateway/tests/test_policy_*.py` | 69 个新用例，全套 448 通过 |
| 插件 | `run-tests.sh -c platform/phpunit-runtime-policy.xml` | 46 个新用例（先确认 RED：36 错误 10 失败），全套 70 通过，MariaDB 与 PostgreSQL |
| 平台 | `SurveyAccessPoliciesTest` | 9 个新用例（先确认 RED） |
| 真引擎端到端 | `platform/deploy/test/run-access-policy.sh` | 38 项检查，MariaDB 与 PostgreSQL 全过 |

端到端覆盖：插件未激活时发布回滚；开放前拒绝（提示带本地时刻与时区，原生 `startdate` 为 UTC）；
截止后拒绝——纽约时区、清掉原生 `expires`、伪造 `Date` 头，插件照样拒绝且不落库；
开始时在窗口内、交卷时已截止，这次提交被拒；密码错拒对进、解锁只对本 sid 有效、库里只有哈希、
状态端点不泄漏哈希；按设备限 1 次（新浏览器可绕过，已记录）；按 token 限 1 次（换浏览器仍拒）；
限时 60 秒到点拒绝提交、同一 token 换浏览器不能重新计时；验证码；IP 拒绝名单不受伪造
`X-Forwarded-For` 影响。

### 5. 平台自己重算一遍摘要再比对

网关的回读（决定 4）证明"载荷到了插件"，但它是网关的自证：一个忽略 `policy` 块的旧网关根本不会
回读，回执里也就没有 `policyDigest`。因此收尾时（`PublishSettlement`）平台把**自己发出去的那份策略**
按契约 §4 重新编译成载荷、算 SHA-256，与回执里的摘要逐字比对：

| 情形 | 结局 |
|---|---|
| 定义需要插件、回执没有摘要 | 发布失败（旧网关或丢了策略） |
| 摘要不一致 | 发布失败（网关执行的是另一份策略） |
| 平台算不出摘要（时刻在夏令时缺口里、IP 写法看不懂） | 发布失败（这些定义网关本该在校验阶段就拒掉） |
| 定义不需要插件（无策略，或只有验证码／邀请码），回执却带摘要 | 发布失败 |

失败＝不登记路由、不写已发布版本、问卷回到可再次发布，引擎里残留的那份问卷按孤儿记录（需人工清理）。
刻意不判"结果未知"：重发只会再得到同一份不设防的问卷。

代价是同一套规范化规则有了两份实现（网关 `pubgw/policy/compile.py`、平台 `AccessPolicyDigest`），
分歧会**误挡**正常发布。两边因此读同一张向量表
`platform/services/business/src/test/resources/policy/digest-vectors.json`（夏令时两侧的本地时刻、
乱序限次、IPv6 压缩与前导零、CIDR 主机位、不需要插件因而没有摘要的策略），改一端不改另一端必有一边红。

## 缺口

- ⏳ 地区数据集未随仓库交付；省级地区与授权定位未做。
- ⏳ 租户级时区配置（目前是草稿里的 `window.timezone`，缺省 `Asia/Shanghai`）。
- ⏳ 微信 openid、手机号、账户维度的限次；邀请码撤销的平台接口（引擎侧删参与者即时生效）。
- ⏳ 按日循环的时间窗（如每天 9–17 点）。
- ✅ 网关 `add_participants` 总是让引擎生成 token（`create_token=true`），定义里写的 token 会被替换；
  平台要发的邀请码现在随发布回执一并返回（`invitations[]`，按定义顺序，平台可给每条一个不透明
  `ref`），见契约 publish-gateway-v1「邀请码回读」。配不齐就发布失败并回滚。
  存档：回执按 `requestId` 存档，所以邀请码明文会落到网关状态目录。**带码的回执因此只留 24 小时**
  （`PUBGW_INVITATION_TTL_SECONDS`，[ADR 0012](0012-republish-and-drift.md) 决定 8），到点整份回执
  过期成不含正文的墓碑，重放是 410 `result_expired`。
- ✅ 上游也通了：平台现在会在发布时把问卷受众物化成 `participants`（每条只带 `ref`＝联系人 id，
  个人信息不出平台），发布成功后按 `ref` 自动登记"哪个码发给了谁"（ADR 0017 决定 8）。
  只有 `access.invitationRequired` 为真才发这个键。邀请码在平台侧只落
  `contact_participation.participant_token` 一处，不进审计、日志与定义快照——网关的存档遗留不再多一处。
- ✅ 「一份答卷属于哪个邀请码」：读端点新增 `includeRespondent`（契约 response-read-v1「参与者令牌」），
  平台 `GatewayRespondentIdentityResolver` 据此实现 `RespondentIdentityResolver`，催答对账按人生效。
  **匿名问卷永不给令牌**（按问卷当前 `anonymized` 判定，不按列在不在；判定不出来也不给）；
  `token` 不能当普通列请求（400）。令牌不落库，每轮对账现读现用。
  网关未配置时不注册该实现，对账照旧空转——读不到绝不解释成"没人答完"，否则会朝已答完的人发催答。
- ⏳ 离线 CSV 地区数据源逐行扫描，全量库需要预排序＋二分或缓存。
- ⏳ 平台重算摘要用的是自己发出去的定义快照：网关若改了规范化规则而平台没跟上，正常发布会被误挡
  （向量表会先红，属于部署前能发现的问题）。
- ⏳ 超时的作答不会自动算作"用掉一次"：同一身份在该问卷里就停在超时状态（设备身份可换设备绕过）。
