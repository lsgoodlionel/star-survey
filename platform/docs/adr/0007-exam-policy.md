# ADR 0007：考试计时与硬配额的服务端强制

- 状态：原型已实现，MariaDB 10.11 端到端验证通过；平台侧发号与网关待实现
- 日期：2026-09-20
- 关联：P0-00.7；需求 U-05、U-07；总方案 §4.4
- 配套证据：[考试与配额实测证据](../p0/exam-quota-evidence.md)、`platform/tests/e2e/exam_policy.php`

## 背景：引擎在"考试"这件事上给了什么、缺了什么

| 能力 | 引擎现状 | 源码位置 | 够不够 |
|---|---|---|---|
| 问卷到期 | 服务端判定，`gmdate()` 与 `surveys.expires` 比较，POST 也走同一条检查 | `application/controllers/survey/SurveyIndex.php:393` | **问卷级**，全体考生共享一个到期时刻，不是"每人一场" |
| 问卷开考 | 同上，与 `surveys.startdate` 比较 | 同文件 `:414` | 同上 |
| 题目限时 | 纯前端 JS 倒计时，属性写进页面 | `application/helpers/qanda_helper.php:394` 起 | **不是服务端约束**，关掉 JS／刷新即失效 |
| 答卷时间 | `startdate`、`datestamp`、`submitdate` 全由服务端 `gmdate()` 写 | `application/helpers/expressions/em_manager_helper.php:5435` | 时间戳不接受客户端输入（已实测） |
| 计时表 | `timings_<sid>` 只记录每题耗时，用于统计 | `application/libraries/Save.php`（`set_answer_time`） | 只是事后统计，不参与任何判定 |
| 硬配额 | `quota` / `quota_members` 两张表，命中即终止 | `application/models/services/Quotas.php:415` | **没有原子性**，见下 |

### 引擎配额的先查后写

```
Quotas::checkCompletedQuota()   Quotas.php:518   $iCompleted = $oQuota->completeCount;
  └─ Quota::getCompleteCount()  Quota.php:165    SurveyDynamic::model()->count($oCriteria)   ← 不加锁的全表 COUNT
                                Quotas.php:519   if ($iCompleted >= $oQuota->qlimit) → 终止
…（同一次请求稍后）
em_manager_helper.php:5425      再查一次配额
em_manager_helper.php:5435      Response::updateByPk(['submitdate' => …])                    ← 写入
```

三步之间没有事务、没有行锁、配额表里也没有计数列。**"数一下有几个人完成了"与
"把自己算成完成"之间存在窗口**，窗口宽度就是那条 COUNT 加上后续页面处理的耗时。
实测（证据文档场景 C）：答卷表 262,144 行时这条 COUNT 要 24 毫秒，8 个并发提交里
有 6 个同时拿到了最后一个名额。

### 会话 id 不是稳定身份

`resetAllSessionVariables()`（`application/helpers/frontend_helper.php:1406`）在
构建作答会话时调用 `regenerateID(true)`，同一个作答者的 GET 与 POST 会拿到不同的
PHP 会话 id。原型第一版用会话 id 当考场身份，一次完整作答直接领走了两张名额租约。

## 决定

### 1. 权威时刻只来自数据库

所有策略判定的"现在"由 `MjyServerClock::nowUtc()` 从数据库取
（MariaDB `UTC_TIMESTAMP()`，PostgreSQL `timezone('UTC', now())`）。

理由：单机上 Web 进程的 `gmdate()` 与数据库一致，但多台 Web 节点的时钟可以互相
偏移，而考试场景里偏移就是可利用的时间差。数据库是整套部署里唯一的单点，所以
把它定为唯一权威。请求里的任何时间字段都不是输入。

### 2. 截止时刻首次进场时一次写死，之后只读

`lime_mjyruntimepolicy_deadline`，自然键 `(engine_instance_id, survey_id, session_key)`，
写入用"先查、插入、唯一键冲突即重读"，并发首次进场只会留下一行（与 ADR 0003 的
代次表同样的理由：不能放进插件设置表，那里没有唯一约束）。

`start()` 是幂等的：已经有记录就原样返回，**请求里报多长的时长都不算数**。于是
刷新、断线重连、旧 Cookie 重放、换浏览器重开，命中的都是同一行。

### 3. 考场身份优先用准考证 token

`session_key` 的取值顺序：

1. `token:<准考证>`——换浏览器、清 Cookie、断线重连都还是同一个人；
2. `admission:<uuid>`——没有 token 时，插件自己在会话数据里发一张入场券。
   会话数据在 `regenerateID(true)` 时会被迁移，所以入场券跨请求稳定。

入场券只挡得住"换标签页"，挡不住"清 Cookie"。**结论：真正的考试必须由平台发
token（一人一码），匿名链接考试在任何方案下都守不住。**

### 4. 闸门挂在 `beforeSurveyPage`，它能拒绝一次提交

`beforeSurveyPage` 在 `SurveyIndex.php:228` 派发，而处理 POST 的
`SurveyRuntimeHelper::run()` 在同一个方法的 `:692` 才被调用。在钩子里调用
`renderExitMessage()`（`application/controllers/SurveyController.php:141`，
末尾是 `App()->end()`，同文件 `:195`）会终止整次请求——**被拒绝的提交连答案都
写不进答卷表**（实测：答卷行数不变、`submitdate` 仍为 NULL、POST 里的答案没有落库）。

对照：`afterSurveyQuota`（`Quotas.php:546`）派发时答案已经存过了，它只能改终止
页面的文案与跳转，不能把这次作答撤回。所以"最后一刻的策略"只能放
`beforeSurveyPage`，不能放 `afterSurveyQuota`。

两处限制：

- 文件上传入口用的是 `UploaderController`（同样派发 `beforeSurveyPage`，
  `application/controllers/UploaderController.php:389`），那里没有
  `renderExitMessage()`，只能抛 `CHttpException(403)`；
- RemoteControl 的 `add_response` 不经过 `SurveyIndex`，**完全绕开这个闸门**。
  API 面必须由平台网关自己守。

### 5. 名额用"进场前预留租约"，引擎配额降为第二道保险

`lime_mjyruntimepolicy_quota`（配额锚点行）＋ `lime_mjyruntimepolicy_lease`（租约）。
发放租约的事务：

```
BEGIN
  SELECT slot_limit FROM …_quota WHERE quota_key = ? FOR UPDATE     ← 串行化点
  已有本人有效租约？ → 复用（幂等，每翻一页都会再进来一次）
  已用名额 >= slot_limit？ → 拒绝
  INSERT INTO …_lease (state='held', expires_at = now + ttl)
COMMIT
```

已用名额 = 已确认的租约 + 未过期的持有中租约。于是：

- **只占位不交卷的人到点自动归还名额**，不依赖任何清理任务（`reap()` 只是把状态
  写成 `expired` 方便审计，cron 调用）；
- 交卷时 `afterSurveyComplete` 把租约转成 `confirmed`，之后 TTL 到点也不归还；
  只有仍在持有中的租约能被确认——已过期的租约名额可能已经给了别人，再确认回来
  就是超发。**因此租约 TTL 必须大于单场考试的最长时长**；
- 引擎自带的配额继续开着，但它从头到尾没机会发挥作用：人数在进场阶段就定死了。

租约在**进场时**领，不是交卷时领——这正是与引擎配额的根本差别。引擎是"让所有人
答完再挑，挑剩的白答"，租约是"进不去就别答"。

### 6. 闸门 fail closed，记账 fail open

与 `MjyPlatformBridge` 刻意不同（ADR 0003 决定 5 是"异常只写日志、不影响作答者"）：

- **闸门本身**（截止时刻、名额）查不下去时**拒绝**并给出提示页。一个失败即放行的
  策略插件等于没有策略，考试场景不接受这种默认。
- **记账动作**（交卷时确认租约、cron 回收过期租约）失败只写日志，不拦作答者：
  最坏结果是一个名额晚一点归还，不是有人白答一场。

### 7. 商业逻辑不进插件

插件里只有机制：时钟、截止时刻表、租约表、判定、拒绝页。**考多久、几个名额、
谁能进场，全部由平台通过 `applyPolicy()` 下发**，存在
`lime_mjyruntimepolicy_survey_policy`（`survey_id` 主键）。

这条是许可分析结论 7 的直接要求：插件继承 `PluginBase`、共享引擎进程与数据库连接，
按衍生作品处理，商业逻辑必须留在平台侧。

## 插件表面（最小集）

| 订阅的事件 | 干什么 |
|---|---|
| `beforeSurveyPage` | 唯一的闸门：定死／校验截止时刻，领取／校验名额租约，不通过就终止请求 |
| `afterSurveyComplete` | 把租约转成已确认 |
| `beforeActivate` | 懒建表 |
| `cron` | 回收过期租约（只为审计，名额本来就已经不计入） |

七个类，全部在插件根目录、全局命名空间（引擎插件加载机制要求），沿用
`MjyPlatformBridge` 的写法：

| 类 | 职责 |
|---|---|
| `MjyRuntimePolicy` | 插件本体：订阅、会话身份、拒绝路径、失败语义 |
| `MjyPolicyEngine` | 判定核心，可脱离 HTTP 单测 |
| `MjyServerClock` | 数据库 UTC 时刻，驱动相关 |
| `MjyExamDeadlineStore` | 截止时刻表，幂等写入 |
| `MjyQuotaLeaseStore` | 配额锚点＋租约表，行锁发放 |
| `MjySurveyPolicyStore` | 平台下发的问卷策略 |
| `MjyPolicyDecision` | 不可变判定结果（允许／超时／名额满／不可用） |

## 验证

单元／集成：`plugins/MjyRuntimePolicy/tests/**`，24 个用例在 MariaDB 10.11 上全部
通过（先确认 RED：24 个用例、24 个错误）。

```bash
platform/deploy/exam/setup.sh
docker exec survey-exam-web php -d memory_limit=2G vendor/bin/phpunit \
  -c platform/phpunit-runtime-policy.xml
```

端到端（独立的 `survey-exam` 栈，8093 端口，自带数据库与 tmp 卷，不干扰其他栈）：

```bash
EXAM_CONCURRENCY=100 platform/deploy/exam/run-exam-policy.sh
```

十个场景全部通过，逐条数据见[证据文档](../p0/exam-quota-evidence.md)。关键结论：

| 问题 | 实测答案 |
|---|---|
| 迟到请求／旧会话重放／换浏览器重开／伪造表单字段能延长考试吗 | 都不能，且被拒绝的提交不落库 |
| `beforeSurveyPage` 能拒绝一次提交吗 | 能，答案与 `submitdate` 都没写进去 |
| 引擎配额并发下最后一个名额给了几个人 | 常规负载偶发 2 人；答卷表 262,144 行时 8 并发里 **6 人** |
| 名额租约并发下最后一个名额给了几个人 | 3 轮 × 100 并发，每轮**恰好 1 人**，其余 99 人看到"名额已满" |

## 平台无论如何都要自己承担的部分

1. **发准考证**：一人一码的 token 是唯一守得住的考场身份，插件发不了也不该发。
2. **下发策略**：考试时长、名额上限、租约 TTL 由平台写入，插件不做任何业务判断。
3. **守住 API 面**：RemoteControl `add_response` 绕开 `beforeSurveyPage`，
   引擎权限模型不参与（ADR 0002、0005 已有同样结论），必须由平台网关拦。
4. **跨实例的名额**：租约表在引擎库里，只保证单实例内的互斥。同一场考试分布在
   多个租户实例上时，名额必须由平台侧的单一数据源发放，引擎侧租约退化为本地缓存。
5. **时钟运维**：权威时刻来自数据库，因此数据库主机的 NTP 是考试正确性的一部分；
   主从切换、跨机房部署时要保证时钟单调。

## 已知限制与后续

- [ ] 只在 MariaDB 10.11 上跑过。`SELECT … FOR UPDATE` 与 UTC 表达式都写了
      PostgreSQL 分支，但没实测；正式版要补 PostgreSQL 回归（参照 ADR 0003 的做法）。
- [ ] `ensureSchema()` 目前只建表不升级，与 ADR 0003 同一个待办：需要带版本号的迁移。
- [ ] 租约表没有清理策略，长期会无限增长；需要按问卷归档。
- [ ] 截止时刻只有"到点即拒"，没有"到点自动交卷"。自动交卷要在服务端把
      `submitdate` 写进去，属于平台侧的收卷作业，本原型不涉及。
- [ ] 断线重连的作答者会看到拒绝页而不是"剩余时间"；正式版应把剩余秒数下发给前端
      做倒计时展示（展示归展示，判定仍在服务端）。
- [ ] 高答卷量下引擎那条配额 COUNT 本身就是性能问题（262,144 行要 24 毫秒，
      每次提交都跑），平台侧若继续挂引擎配额需要评估。
