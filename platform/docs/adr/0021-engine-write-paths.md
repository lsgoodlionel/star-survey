# ADR 0021：引擎的写入面收口——RemoteControl 白名单、插件闸门、邀请码撤销

- 状态：已实施；三条都有先红后绿的复现证据
- 日期：2026-09-28
- 关联：P0 发现 12、21；[ADR 0007](0007-exam-policy.md)「平台无论如何都要自己承担的部分」第 3 条；
  [ADR 0016](0016-access-policy.md) 缺口「邀请码撤销的平台接口」；[ADR 0012](0012-republish-and-drift.md)
- 契约：[publish-gateway-v1.4](../../contracts/publish-gateway-v1.4.md)
- 证据：`platform/tests/e2e/rpc_gate.py`、`platform/tools/publish-gateway/tests/test_rpc_allowlist.py`、
  `platform/tools/publish-gateway/tests/test_revoke.py`、
  `platform/services/business/src/test/java/cn/mjy/platform/contacts/InvitationRevocationTest.java`

## 背景

平台对引擎只有两条写入路径：**发布网关经 RemoteControl**，以及**作答者经作答运行时**。
作答运行时那条已经有闸门（`beforeSurveyPage`，ADR 0007 决定 1、ADR 0016）。
RemoteControl 那条一直没有，而它上面有两个方法能**以作答者的名义写答卷**：

- `add_response`（`remotecontrol_handle.php:3374`）不经过 `SurveyIndex`，
  **完全绕开 `beforeSurveyPage`**（P0 发现 12）；
- `update_response`（同文件 `:3474`）直接 `SurveyDynamic::encryptSave()`，
  不跑 EM、不跑必答、不跑任何插件闸门（P0 发现 21）。

后果是访问规则、按身份限次、**服务端考试计时**、题型的服务端校验对这条路径一律失效。
`MjyPlatformBridge` 的补偿扫描只看答卷表状态、不区分来源，因此这样进来的答卷事后也认不出来。

ADR 0007 早就把责任划清楚了：「**守住 API 面**：RemoteControl `add_response` 绕开
`beforeSurveyPage`，引擎权限模型不参与，必须由平台网关拦。」——**但这件事一直没做**，
仓库里既没有实现也没有测试。本 ADR 补上，不是重新论证归属。

同一份 ADR 里还收一条同源的缺口：`ContactParticipationService.revoke()` 只写平台自己的
`revoked_at`、**从不调引擎**，于是被撤销的邀请码照样能打开问卷，而平台显示"已撤销"。
它与上面两条是同一个形状——**平台以为自己控制着引擎的写入面，实际上没有**。

## 决定

### 1. 网关的可调方法集是白名单（第一道，ADR 0007 指定的那道）

`pubgw/rpc.py` 的 `RemoteControlClient.call()` 对 `ALLOWED_METHODS` 做白名单校验，
不在名单里的方法在**构造请求体之前**就抛 `ForbiddenMethod`——一个字节都不到引擎。
`add_response` 与 `update_response` 另列在 `FORBIDDEN_METHODS` 里，并有模块级断言
钉住"它们永不出现在白名单中"。

为什么这一道就够关掉平台这条路径：契约 `publish-gateway-v1`「部署与信任边界」写明
**引擎管理员口令只存在网关侧**，平台从不持有也不转发。网关发不出这两个方法，
平台这条路径上它们就不存在。

`ForbiddenMethod` 继承 `RpcError`，因为发布编排只认 `RpcError`——换成别的异常会在
设置 `survey_id` 之前逃逸，连回滚都不会尝试，在引擎里留下孤儿问卷。

白名单是**白**不是黑：没想到的写接口（删答卷、装插件、建账号）同样发不出去。
加能力必须显式加名字，测试里有一条按源码扫描调用点、漏加立刻变红。

### 2. 引擎侧再加一道插件闸门（第二道）

第一道只约束网关自己。任何持有引擎管理员凭据的人——运维、遗留账号、被翻出来的脚本——
照样能直接打 RemoteControl。所以 `MjyRuntimePolicy` 订阅 `beforeControllerAction`
（`LSYii_Application.php:384`，RemoteControl 这条路上唯一早于 `RemoteControl::run()`
的插件落脚点），识别 `admin/remotecontrol`，解析 JSON-RPC / XML-RPC 的方法名，
命中就回 403 ＋ JSON-RPC error 并把 `run` 设成 false。

判定逻辑在 `MjyRpcGate`（无引擎依赖、可单元测试）；请求生命周期那半边留在插件里。

**两类语义分开**：
- **作答者提交**永远走 `SurveyIndex` / `UploaderController`，闸门在那里，本决定不碰；
- **运维/管理员**用 RemoteControl 是特权通道，但"以作答者名义补一份答卷"不是运维动作的常态，
  默认拒绝。确实要做数据修正时，在**那一台实例**上显式设 `MJY_ALLOW_RPC_RESPONSE_WRITES=1`，
  每次调用都留一条警告日志，用完撤掉。

解析不出结构、但正文里出现被禁方法名时**按拒处理**：我们的解析器与引擎的解析器不必完全一致，
读不懂的正文不能当成安全（失败即关闭）。反过来，方法名认出来了就只按名字判，
因此"问卷文案里恰好写了 add_response"这类正常调用不会被误伤。

### 3. 邀请码撤销要先删引擎里的参与者行

新增 `POST /v1/participants/revoke`（契约 v1.4）：`get_participant_properties` 按 token 查 `tid`，
`delete_participants` 删掉，再回读确认。平台侧 `ContactParticipationService.revoke()` 改成
**先引擎、后平台**：

1. 在租户作用域内判权限、读出这条映射；
2. 出了作用域调网关删引擎参与者行；
3. 确认删掉之后才写 `revoked_at`。

顺序不可交换。反过来（先写库再调引擎）就是这个缺口的原状：网关那一步失败时平台已经
记成"已撤销"，而码照样能进，**没人会再发现**。网关没确认时抛
`InvitationRevocationFailedException`，对外 503 `revocation_not_applied`，一个字节都不写。

**撤销作用在这条映射自己的 `(实例, sid)` 上，不是"当前在线版本"**。按 ADR 0012，
新版本是引擎里另一份问卷、另一套参与者表，令牌活在签发它的那一份上；`contact_participation`
每行都带着 `engine_instance_id` 与 `engine_sid`，照着删即可。旧版本的映射因此同样撤得掉——
那一版即使已被 `/v1/close` 收口，收口也只挡新答卷，续答与已开启的会话不受它约束，
所以旧码仍然值得撤。`ContactParticipationService` 原有的两条约束（一个令牌同时只属于一个联系人、
令牌落在当前在线的已发布版本上）都只约束**登记**，不约束撤销，因此不受影响。

为什么是删而不是停用：引擎的作答入口按参与者表里有没有这一行放人，行没了就立刻进不去，
不用等缓存或会话过期。`usesleft` / `validuntil` 也能拦人，但语义是"用完了 / 过期了"，
而且 `validuntil` 与问卷时区、引擎时钟纠缠在一起——撤销不该依赖时钟。答卷不受影响：
删的是参与者行，已经收到的答卷与答卷表原样保留。

## 不破坏发布网关的既有功能

这是本 ADR 最容易做砸的一点，因此有专门的证据而不是保证：

- 白名单里逐条列着发布七阶段、回读核对、收口、答卷导出、参与者查询与删除用到的每个方法，
  `test_rpc_allowlist.py` 有一条**按源码扫描**所有调用点、比对白名单的用例，漏加即红；
- `MjyRpcGateTest` 用数据驱动把这 14 个方法逐个跑一遍，断言闸门**不拦**它们；
- 端到端 `rpc_gate.py` 的场景 E 在闸门装着的真引擎上跑完：发布七阶段（含参与者与邀请码回读）、
  `get_fieldmap`、`list_questions`、`get_survey_properties`、`get_participant_properties`、
  收口（设过期）、`export_responses`、插件通道（`newDirectRequest` 的 policyStatus）——全部照旧。

## 代价与残留

- **闸门是按方法名的**。引擎将来新增别的"以作答者名义写答卷"的接口（或 REST 控制器里的同类入口），
  这份名单不会自动跟上。REST 面（`RestController`）**本次未覆盖**：它有自己的认证，
  且平台不经它写答卷，但它同样是一条待审的写入面。
- **运维开关是实例级的**，打开期间该实例上的 `add_response` / `update_response` 全部放行，
  没有按问卷、按时间窗细分。这是刻意的简化：细分会让"现在到底开着没有"更难判断。
  它只该在人工操作窗口里存在。
- **`beforeControllerAction` 要读 `php://input`**。RemoteControl 用 `application/json` /
  `text/xml`，这类正文可以重复读取，引擎随后自己再读一遍不受影响；
  `multipart/form-data` 才是不可重读的那种，RemoteControl 不用它。
- **撤销的"问卷不存在"算失败**（契约 v1.4 有专节说明代价）：一份在引擎里被人工删掉的问卷，
  它的映射会一直撤不掉，需要运维处置。方向是刻意选的——撤销失败看得见，撤销假成功看不见。
- **网关调用不在数据库事务里**：授权与读取在一个租户作用域内，写 `revoked_at` 在另一个。
  中间并发的第二次撤销无害（UPDATE 带 `revoked_at IS NULL`，重复撤销返回 false、不重复记审计），
  但"引擎已删、平台没写成"这种窗口仍然存在——方向是安全的那一边（码已经失效），
  重试一次即可收敛。
- **补偿扫描仍然不区分来源**：`MjyPlatformBridge` 的完成扫描只看答卷表状态。
  运维开关打开期间写进来的答卷，事后同样认不出来源。本次未改。
- **未覆盖**：PostgreSQL 下的 `rpc_gate.py` 端到端（只跑了 MariaDB）；
  撤销的端到端（平台 → 真网关 → 真引擎）只到网关这一层，平台那一层用的是 `FakePublishGateway`。

## 复现与验证

```
# 缺口 1、2（RemoteControl 旁路）——先看红：
#   把 MjyRuntimePolicy::init() 里 subscribe('beforeControllerAction') 那行去掉，然后
RPC_GATE_ONLY_BYPASS=1 platform/deploy/test/run-rpc-gate.sh
#   B/C 会报 add_response 写进了一份已提交答卷、update_response 改掉了库里的答案。
# 再看绿（把那行加回去）：
platform/deploy/test/run-rpc-gate.sh

# 网关白名单单测：
cd platform/tools/publish-gateway && python3 -m unittest discover -s tests -t . -q

# 缺口 3（邀请码撤销）——先看红：
#   把 ContactParticipationService.revoke() 换回"只写 revoked_at"的旧实现，然后
PLATFORM_DB_NAME=platform_q2 platform/deploy/test/run-platform-tests.sh -- -Dtest=InvitationRevocationTest
#   6 条里 4 条失败，含「撤销必须让引擎删掉那一行参与者」。
```
