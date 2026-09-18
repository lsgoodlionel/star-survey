# ADR 0003：答卷生命周期事件的可靠采集

- 状态：原型已实现、经独立审查并修复；故障注入与投递中继待完成
- 日期：2026-09-18
- 关联：P0-00.4；需求 R23-04、R23-05；总方案 §4.3

## 背景：引擎源码中的四条“无事件”路径

| 路径 | 源码位置 | 影响 |
|---|---|---|
| 完成时写 `submitdate` 用 `Response::updateByPk()` | `application/helpers/expressions/em_manager_helper.php:5435` | 不触发 AR 事件；完成只能靠 `afterSurveyComplete` 或扫描 |
| `afterSurveyComplete` 在渲染流程中派发，无事务 | `application/helpers/SurveyRuntimeHelper.php:1317-1324` | 写完 `submitdate` 后进程中断即丢事件 |
| 后台批量删除答卷用 `deleteByPk` | `application/controllers/ResponsesController.php:657` | 不触发删除事件 |
| 插件 API `removeResponse` 用 `deleteByPk` | `application/libraries/PluginManager/LimesurveyApi.php`（`removeResponse`） | 同上 |

会触发事件的路径：分页保存 `Response::encryptSave()` → `afterResponseSave`（`dynamicId=sid`）；数据录入 `SurveyDynamic` 保存 → `afterSurveyDynamicSave`；单条 `delete()`（含 RemoteControl `delete_response`）→ `afterResponseDelete`。

**新发现：response id 不是稳定主键。** 停用再激活问卷会重建 `responses_<sid>`，id 从 1 重新计数。仅用 `(sid, response_id)` 做去重键会把新答卷误判为“已记录”，并吞掉事件。测试中重复导入同一 sid 时复现了该问题。

## 决定

1. 插件 `MjyPlatformBridge` 在**引擎库内**维护追加式事件表 `lime_mjyplatformbridge_event_log`，与答卷同库，便于同库反连接对账。
2. 事实类型：`response.saved`（每次保存，可重复）、`response.completed`、`response.deleted`（带唯一去重键，全局只记一次）。
3. 自然键＝`(engine_instance_id, survey_id, generation, response_id)`。`generation` 为答卷表代次 UUID，在 `afterSurveyActivate` 时轮换（模拟激活不轮换），首次使用时懒创建。代次存在独立表 `lime_mjyplatformbridge_generation`（`survey_id` 主键），用“插入失败即重读”保证并发首用只产生一个值。**不用插件设置表**：它没有唯一约束，并发写入会产生多行，`getGeneric` 随即返回数组，代次变成字符串 `"Array"`，导致重复事件（代码审查发现）。
4. 完成与删除以**数据为准**：cron 扫描器在当前代次内
   - 找 `submitdate IS NOT NULL` 且无完成事件的答卷 → 补记 `completed`（source=scanner）；
   - 找事件表里出现过、答卷表中已不存在、且无墓碑的 id → 补记 `deleted`（source=scanner）。
   钩子与扫描器谁先到都只产生一条事实。
5. 事件处理异常只写日志，不影响填写者或管理员请求（含 `beforeActivate`）；遗漏由扫描器兜底。cron 扫描按问卷隔离，一个问卷出错不影响其他问卷。
6. 查询占位符每条 SQL 只出现一次（PostgreSQL 原生预处理不允许重复）；分批 `LIMIT 500`，无进展即停止，避免死循环。
7. **停用前最终扫描**：停用会把 `responses_<sid>` 改名为 `old_responses_<sid>_<日期>`（`application/models/services/SurveyDeactivate.php:121`），之后扫描器再也看不到这些答卷。插件订阅 `beforeSurveyDeactivate`（同文件 `:103`，在改名之前派发）同步扫描一次。
8. 所有写入路径在首次使用时确保表存在，不依赖 `beforeActivate` 一定执行过。

## 验证

`plugins/MjyPlatformBridge/tests/MjyPlatformBridgeTest.php`，12 个用例在 MariaDB 10.11 与 PostgreSQL 16 上全部通过（每轮先确认 RED）：

| 用例 | 断言 |
|---|---|
| 分页保存 | 记录 1 条 saved，无 completed |
| 完成钩子重复触发 | 只 1 条 completed，来源 hook |
| 钩子丢失 | 扫描器补记 1 条 completed，来源 scanner |
| 扫描幂等 | 第二次扫描补记 0 条；未完成答卷不产生 completed |
| 先扫描后钩子 | 仍只 1 条 completed |
| 单条 AR 删除 | 1 条 deleted 墓碑 |
| 批量 `deleteByPk` | 扫描器补记 1 条 deleted，多次扫描不重复 |
| 重新激活 | 代次轮换，旧代次事件不吞掉新代次的同号答卷 |
| 事件元数据 | 带引擎实例 ID、UUID 事件 ID、问卷 ID |
| 代次并发首用 | 败者采用胜者的代次，表中只有一行 |
| 停用前扫描 | 钩子丢失且在下次 cron 前停用，仍补记完成事件 |
| 表未创建即触发钩子 | 懒建表后正常记录 |

独立代码审查（2026-09-18）结论与处理：

| 级别 | 问题 | 处理 |
|---|---|---|
| 严重 | 代次存插件设置表，并发首用产生多行 → 重复完成事件 | 已修复：独立代次表＋主键约束 |
| 高 | 停用后答卷表改名，扫描器永远补不回停用前丢失的事件 | 已修复：`beforeSurveyDeactivate` 同步扫描 |
| 中 | `beforeActivate` 未受保护，建表异常会中断管理员操作 | 已修复 |
| 中 | 一个问卷插入失败会中止整轮 cron 扫描 | 已修复：按问卷隔离 |
| 中 | 从未产生事件的答卷被直接删除时不会记墓碑 | 未修复，列入下方限制 |
| 低 | 并发、停用、未建表场景缺测试 | 已补测试；PG 已实测 |

## 已知限制与后续

- [ ] **真实进程故障注入**（总方案 §11.2-00.4 四个中断点）：用 `docker kill` 在 HTTP 填写流程中中断，度量丢失窗口；决定是否需要核心补丁。
- [ ] 投递中继：未投递事件（`delivered_at IS NULL`）→ 平台 `/internal/engine-events`（mTLS）→ 标记已投递。
- [ ] 若 `afterSurveyActivate` 本身丢失，代次不会轮换。后备方案：扫描器比对答卷 `startdate` 与该 id 首次事件时间，发现倒退即告警。
- [ ] 表结构迁移：`ensureSchema` 目前只建表不升级，正式版需带版本号的迁移。
- [ ] 扫描性能：当前按问卷全表反连接；30 万答卷量级需加 id 水位线与重叠窗口，并补压测。
- [ ] `SurveyDynamic::insertRecords`、答卷导入（`beforeDataEntryImport`）、归档表恢复等路径补测试。
- [ ] 删除的全量对账目前只覆盖“事件表见过的 id”；从未产生过事件的答卷被直接删除时，需周期性 id 集合比对。
