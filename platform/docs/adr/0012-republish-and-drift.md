# ADR 0012：重新发布（新版本＋路由切换）与漂移检测

- 状态：已实现（WP-01 01.3、01.4，旧版恢复见决定 7），单元／集成测试与真引擎端到端通过
- 日期：2026-09-22
- 关联：[ADR 0005](0005-publishing.md)、[ADR 0009](0009-publish-gateway.md)（已知限制 2：没有重新发布）、
  [ADR 0011](0011-resource-tree-and-publish-approval.md)（审批绑定草稿版本）；
  契约 [publish-gateway-v1.2](../../contracts/publish-gateway-v1.2.md)
- 蓝图约束：激活失败仍访问旧版；并发保存不无声覆盖；每个发布版本是独立的引擎问卷（sid），答卷不得跨版本混淆

## 背景

P1 之前，问卷一旦发布就是终态（`already_published`）。引擎在激活后禁止增删题目，现实的改法只有
"停用 → 改 → 重新激活"，而停用会把答卷表改名归档、重新激活会重建答卷表并让答卷 id 从头开始——
同一个 sid 前后两批答卷的列与 id 都对不上。网关现成的回滚动作 `delete_survey` 又会连答卷一起删。
所以"原地升级"不可取。

## 决定

### 1. 每个发布版本是一份新的引擎问卷

重新发布第 N+1 版＝用**同一个 `definition.uuid`、新的 `requestId`** 再调一次 `POST /v1/publish`：
网关照常走完七个阶段，在引擎里新建一份问卷（新 sid）。旧 sid 在这个过程中**完全不被触碰**；
网关失败时的回滚只删本次新建、尚未收过答卷的 sid，因此对重新发布同样安全。发布接口本身不需要任何改动。

已发布版本（`survey_published_version`）与题目映射仍然只追加、不可改；每个版本记着自己的 (实例, sid)，
答卷投影按 (实例, sid, 答卷 id) 归属，答卷 id 在新 sid 里从 1 重新开始也不会与旧版混淆。

### 2. 只有新版落库之后才切换公开路由，而且是原子的

发布收尾事务（持问卷行锁）里，已有在线版本时不再"登记"路由而是**切换**：

```
写入版本 N+1 与映射 → survey_route：旧行 superseded_at = now()，插入新行（同一公开 UUID）
→ 旧版本记入 survey_version_retirement → 问卷 published_version = N+1 → 审批转 published → 审计
```

全部在一个事务里：路由要么仍指向旧版，要么已指向**完整落库**的新版。绑定与请求不符、切换冲突时
整笔回滚并转为待核对——旧版照旧在线、照旧被路由。

`survey_route` 的形状因此改为（V530，见下"模块边界"）：主键 `(engine_instance_id, engine_sid)`，
每个公开 UUID 可以有多条路由，`superseded_at IS NULL` 的**恰一条**是当前路由（部分唯一索引）。

- 正向查找（公开 UUID → 实例、sid）只看当前路由：新作答者进入新版；
- 反向查找（实例、sid → 公开 UUID）包括被取代的路由：旧 sid 上的答卷仍认得回同一份问卷；
- 运行期账号只多了 `UPDATE (superseded_at)` 一列权限；触发器保证被取代的路由不能"复活"、取代时刻不能改；
  路由指向本身仍不可改写、不可删除。

### 3. 旧版切换之后收口：设过期，不停用、不删除

路由切走之后，旧的引擎问卷仍能被直接链接作答，所以要收口。选项：

| 做法 | 结果 | 结论 |
|---|---|---|
| `delete_survey` | 连答卷一起删 | 绝不 |
| 停用（引擎管理端动作，RemoteControl 没有接口） | 答卷表改名归档为 `lime_old_survey_<sid>_<时间>`，原表消失；平台投影、导出按原表名找不到数据；再激活会建新表、答卷 id 重置 | 不可取 |
| **设过期时间 `expires`** | 只改一列设置；答卷表、参与者表、结构全部原样；作答入口按 `gmdate() > expires` 拒绝新会话（`SurveyIndex.php`） | **采用** |

网关新接口 `POST /v1/close` 把 `expires` 写成网关 UTC 当下**回拨 24 小时**：作答入口用 UTC 比较，
管理端按时区偏移显示，主机之间还可能有时钟差——回拨一天保证写入即生效。平台自己记录精确的收口时刻
（`survey_version_retirement.engine_closed_at`）。已过期的不再改写，所以收口天然幂等，不进 requestId 存档。

收口在发布收尾事务**提交之后**才调用；失败不影响发布结论（新版已在线），记下原因与退避时间，
由后台任务（随发布核对的定时任务一起跑）逐租户重试，按核对的退避参数退避、不设上限。
收口前在所属租户作用域内复核：绝不收口当前在线的版本。

代价：切换时正在旧版里答到一半的作答者，下一次翻页会看到"问卷已过期"。这是有意的取舍——
允许旧版继续收答卷会让"哪一版的答卷算数"变得含糊；需要平滑过渡的场景留作后续（见"限制"）。

### 4. 状态机：`status` 描述最近一次发布尝试，在线与否看 `published_version`

不新增状态值，保持既有行为与测试不变：

```
draft ──> publishing ──> published ──（草稿改动后重新发布，新 requestId）──> publishing
               │  ├──> publish_failed ──────────> publishing（新 requestId）
               │  └──> pending_reconciliation ──> publishing（同一 requestId，靠网关幂等）
```

`publishing` / `publish_failed` / `pending_reconciliation` 搭配非空的 `published_version`，就是
"第 N 版在线、第 N+1 版在途（或失败、待核对）"这一子状态；此时路由指向第 N 版。V530 加约束
`published ⇒ published_version IS NOT NULL`。版本视图新增 `live`、`supersededAt`、`engineClosedAt`。

- **没有改动不能重新发布**：草稿版本等于在线版本发布时的草稿版本 → 409 `already_published`（沿用旧码）。
- **并发**：沿用问卷行锁，第二个请求看到 `publishing` 得到 409 `publish_in_progress`；同一时刻只有一个
  请求到达网关。并发保存草稿照旧受乐观锁保护（409 `version_conflict`），不会无声覆盖。
- **审批**（ADR 0011 原样适用）：须审批的成员重新发布也要凭对当前草稿版本的批准；改稿即作废；
  已上线且草稿未改动时不能提交申请（`already_published`）。
- **核对**：结果未知的重新发布与首次发布走同一套 requestId 机制（用户重试或后台核对）；
  收尾时才切换路由，所以核对之前旧版一直在线。

### 5. 漂移检测：只读、只记录、只告警，绝不自动覆盖

漂移＝有人绕过平台在引擎管理端改了已发布的问卷。网关新接口 `POST /v1/drift-check`：给定实例、sid、
期望指纹（可附绑定记录），网关只调 `get_fieldmap` 与 `get_survey_properties`，复用既有指纹与
`check_drift` 代码，报告 match / drift 及原因；sid 不存在（`E_SURVEY_MISSING`）与被停用
（`E_SURVEY_NOT_ACTIVE`）也算漂移，**过期不算**（那是第 3 条的正常结果）。

平台：

- `POST /v1/surveys/{id}/versions/{n}/drift-checks`（需要编辑权）立即检查并记录；
  `GET` 同路径（需要查看权）列出最近 50 次，新的在前。在线版本与已被取代的版本都可以查。
- 每次检查一行 `survey_drift_check`（只追加）：`match` / `drift` / `error`。网关或引擎读不出来记 `error`，
  **绝不当作 match**。`drift` 另写审计 `survey.drift.detected` 并记 WARN 日志。
- 定时巡检（`platform.survey.drift-check.enabled`，默认关闭，测试关闭）：逐租户检查在线版本，
  同一版本在 `recheck-after`（默认 6 小时）内只查一次。
- 检出漂移后**不做任何自动动作**：不重新发布、不回写引擎、不改路由。处理办法由人决定（通常是在平台改稿后
  重新发布一个新版本，旧 sid 随即被收口）。

### 6. 契约升 v1.2 而不是 v2

`/v1/publish` 与 `/healthz` 一字不改，只新增两个路径，旧平台不调用即可照旧工作——这是向后兼容的增补，
按次版本号处理，写在独立文件 `platform/contracts/publish-gateway-v1.2.md`，v1 文件只加一行指引。
引擎管理员口令仍只在网关侧，两个新接口的应答同样经口令脱敏。

### 7. 旧版恢复＝只把旧定义放回草稿（WP-01 01.4）

"把第 N 版恢复过来"这件事，在上面的语义里只有一种安全的做法：**把第 N 版的定义原样写回草稿，别的什么都不动。**

`POST /v1/surveys/{id}/versions/{n}/restore`（需要编辑权，body 带 `expectedVersion`）：

```
读第 N 版的 definition（版本行只追加、不可改）→ 按当下规则重新校验 → 乐观锁写草稿（draft_version + 1）
→ 审计 survey.draft.restore → 未结的发布申请作废（与保存草稿同一条路）
```

- **不碰已发布版本**：`survey_published_version` 与题目映射仍然只追加；被恢复的第 N 版仍是那一行，
  sid、指纹、绑定、答卷全都不动。
- **不碰公开路由**：`survey_route` 一个字都不写，在线版本仍是恢复之前的那一版。恢复期间没有任何窗口
  让路由指向别的版本——因为根本没有路由写入。
- **不碰引擎**：不调网关（既不 publish 也不 close），所以不会有第二份问卷、也不会误收口在线版本。
- 旧内容要重新上线，照常按决定 1–3 走一次发布：它是**新的一版**（新 sid、新版本号），被恢复的旧版与
  它的答卷原样留着。恢复只是"把草稿倒回去"，不是"把线上倒回去"。
- **并发**：与保存草稿共用草稿乐观锁——`expectedVersion` 不符即 409 `version_conflict`，
  并发的恢复与保存只有一个能赢，不会无声覆盖。恢复算改稿，因此已批准的发布申请随之作废（ADR 0011）。
- 历史定义按**当下**的校验规则重新过一遍：规则收紧后恢复不出去的旧稿宁可 422，也不把坏草稿静默写进去。

## 模块边界

`survey_route` 属于租户模块（V102）。切换路由是重新发布的核心，本车道只允许 V530–V539，所以表结构的改动
放在 V530 里，并在本 ADR 记录；**运行期访问仍只经租户模块**：新增的 `SurveyRouteService.switchPublished`
与 `SurveyRouteRepository.lockCurrent / supersede`，`findByPublicId` 改为只看当前路由。问卷模块不直接读写该表。
集成方如希望把这段 DDL 挪回租户号段，只需把 V530 的第 1 节拆成 V1xx，语义不变。

## 验证

- 网关单元测试：`tests/test_close.py`、`tests/test_operations.py` 与 `tests/test_server.py` 的新增用例
  （收口、幂等、缺失 sid、引擎拒存、口令脱敏、漂移 match / 改代码 / 仅指纹 / 被停用 / 被删 / 过期不算、400/401/404/502）。
- 平台测试：`SurveyRepublishTest`（切换、旧版不可变且可查、只收口旧版、无改动 409、激活失败保留旧路由、
  发布中旧版仍被路由且并发只有一个赢、未知结果核对后才切换、收口失败后台重试、审批与改稿作废）、
  `SurveyRouteSwitchTest`、`SurveyDriftCheckTest`、`SurveyRepublishApiTest`、`HttpGatewayOperationsTest`。
- 旧版恢复（决定 7）：`SurveyVersionRestoreTest`（恢复后草稿即旧定义；路由 sid、在线版本、版本行逐字段不变、
  网关零调用；恢复后再发布是新 sid 的第 3 版而旧版仍可反查；乐观锁冲突与并发只有一个赢；
  不存在的版本 404；恢复在线版本＝丢弃草稿改动；无编辑权 403；恢复作废未结申请）、
  `SurveyVersionRestoreApiTest`。以"把乐观锁换成读当前版本"故意改坏实现，两类冲突与并发用例即转红。
- 真引擎端到端（MariaDB 10.11 与 PostgreSQL 16 均通过，2026-09-22）：
  `platform/deploy/test/run-publish-gateway-service.sh` 增加重新发布（新 sid）、收口（旧 sid 仍 `active='Y'`、
  `expires` 已写、作答入口显示"no longer available"、新 sid 可作答、重复收口 alreadyClosed、缺失 sid 502）、
  新版 match、已收口旧 sid 不算漂移，以及直接改库 `lime_questions.title` 后检出 `QSINGLE → QDRIFTED`；
  `platform/deploy/test/run-p1-e2e.sh` 增加重新发布一步：第 2 版新 sid、公开路由切换、旧 sid 反查仍归属原问卷、
  第 1 版 superseded 且已收口、网关恰一次 close、旧答卷留在旧表、第 2 版作答后答卷 id 从 1 开始并被平台投影、
  第 1 版投影不变、对真引擎的漂移检查 match。
- 并行车道隔离：测试栈容器名改为可由 `SURVEY_TEST_PREFIX` 前缀（缺省仍是 `survey-test-*`），
  P1 脚本的网络、平台 / 网关容器、平台库名与网关镜像标签随之带前缀，互不冲突。

## 限制与后续

1. **切换时进行中的旧版作答会被截断**（见决定 3）。需要"旧版宽限期"时，可以把收口推迟一段时间再执行，
   机制（待收口表 + 后台重试）已经具备。
2. **路由切换与收口之间有短暂窗口**：旧 sid 的直链在收口成功前仍可作答；这些答卷照常归属旧版本。
3. **指纹看不见答案选项与题干文案**（ADR 0009 已知限制 1、8）：在引擎里改选项代码或文案，漂移检查仍报 match。
4. **漂移检查不带参与者表、配额、设置**：只比结构与激活状态。
5. **收口依赖 `expires`**：如果有人在引擎管理端把过期时间改回去，旧 sid 会重新开放；漂移检查不把
   "被取代的版本重新开放"当作漂移，这一项留给后续（可在收口任务里周期复核）。
6. 网关仍是单副本（进程内锁），与 v1.1 相同。
7. **旧版恢复只回到"那一版发布时的定义"**，不回滚答卷、不回滚路由，也不保留"当前草稿"的备份——
   恢复会顶掉未发布的草稿改动（这正是"丢弃改动、回到上一版"的用法）。需要留底时先发布或自行另存。
8. 恢复没有单独的"预览差异"接口：调用方可以先 `GET /v1/surveys/{id}/versions/{n}` 自行比对。
