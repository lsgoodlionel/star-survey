# Wave 4 受众与投放实施计划

状态：最小问卷内受众与投放纵切及任务重访恢复已完成本地双库浏览器核对（2026-10-28）。实施分支：`feat/wave3-data-productization`；保持现有堆叠 PR 关系，不变基或合并父分支。

## 目标与边界

Wave 4 的首个纵切位于已发布问卷的上下文中：运营人员创建或选择联系人名单、配置问卷受众、从该受众创建 Email 投放任务、查看脱敏收件人和任务状态，并在未配置真实通道时明确显示不可用而不伪造投递成功。

本轮不接入真实邮件/短信供应商、不配置任何凭据、不做全局一级“投放”导航、不实现短信、回执 Webhook、催答规则或联系人批量导入 UI。它们继续消费既有后端能力，作为后续 Wave 4 切片。投放链接/二维码现有发布页能力不改变。

## 已锁定的契约

1. **PII 边界**：浏览器不得先读取完整联系人地址再把它们回传给投放 API。服务端在当前租户、问卷 `EDIT` 权限和通讯录数据范围下解析已保存的 `SurveyAudience`，再冻结为任务收件人快照。
2. **权限**：通讯录读取/写入沿用 `manage-settings` 或受范围约束的 `manage-members`；投放创建/取消要求问卷 `EDIT`，任务状态和收件人读取要求 `VIEW`。任务列表与收件人地址均按服务端脱敏。
3. **通道失败语义**：无可用 Email provider 时，服务端返回现有 `DeliveryUnavailable`；前端显示“渠道未配置，未创建任务”，不显示伪造成功、队列数或发送结果。
4. **受众快照**：创建任务时只允许使用当前问卷保存的 audience；任务提交后联系人、名单或受众的后续修改不改写已创建任务的收件人。
5. **恢复**：任务页面重访只读取服务端任务状态；可取消的任务展示取消动作，终态不伪造重试。实际通道与回执恢复继续由既有后端任务契约处理。

## 分阶段交付

### 阶段 1：服务端按受众建任务

- 在 `DeliveryTaskService` 增加最小、显式的 audience 创建入口，复用 `SurveyAudienceService.recipients`，由服务端转换为 `NewRecipient` 并交给现有创建路径。
- 新增受保护的 `POST /v1/surveys/{surveyId}/delivery-tasks`；请求只允许 channel、link、主题、模板和 idempotencyKey，禁止携带 recipients。
- 先写 API/服务测试：无 audience、无收件人、越权、通道未配置、受众冻结和地址不泄露。保持现有 `/v1/delivery/tasks` 通用接口不变。

### 阶段 2：问卷内前端

- 在 `SurveyShell` 加入 `audience` 路由/tab；增加 contacts/audience/delivery 类型化 API client。
- 页面支持名单列表/创建、单个联系人新增、问卷受众选择与服务端人数展示；不在 UI 展示不必要的完整地址。
- 使用 audience 创建 Email 任务，展示服务端状态与脱敏 recipients；未配置渠道、403 和可取消状态均提供中文可恢复说明。

### 阶段 3：真实流程与交付同步

- 单测覆盖 API client 与页面状态；后端目标测试走项目 Docker Maven 入口。
- 隔离 Docker MySQL、PostgreSQL 使用真实发布问卷完成：名单/联系人 -> audience -> 未配置通道的真实不可用 -> 配置测试通道后的任务创建、脱敏状态/收件人和取消；核对平台 API、平台数据库、网关与引擎数据。
- 通过范围内测试后更新 README、路线图、能力映射/追溯（仅在证据满足时），同步 Obsidian，按功能边界提交并普通推送当前分支，核对 GitHub CI。

## 2026-10-28 阶段 1 起步

已新增 `POST /v1/surveys/{surveyId}/delivery-tasks`：请求不接收 `recipients`，`DeliveryTaskService` 在服务端按已保存 audience 和当前调用者的数据范围选择可用的 Email/SMS 地址，再复用既有任务创建与收件人快照逻辑。`DeliveryApiTest` 覆盖从保存 audience 创建任务及收件人地址脱敏；隔离 Docker Maven 目标类通过。联系人/受众前端、未配置通道的页面恢复状态和双库浏览器纵切仍待后续阶段，不能将该服务端契约单独视作 Wave 4 准出。

## 2026-10-28 阶段 2–3：问卷内前端与本地验证

`SurveyShell` 已增加“受众与投放”标签及 `/surveys/:surveyId/audience` 子路由，保留当前问卷上下文。页面以类型化 API 客户端提供名单创建、单个 respondent 联系人创建、问卷 audience 读取/保存及服务端人数；浏览器不会读取联系人名册来建立投放请求。Email 任务仅提交 channel、主题、模板与幂等键，任务状态和收件人页面只消费后端脱敏响应；可取消状态提供取消动作。

`DeliveryUnavailable`（HTTP 503）在页面固定显示“渠道未配置，未创建任务”，且不渲染任务成功状态。前端 API/页面/壳层定向测试 49/49、typecheck、lint、生产构建和 production bundle assertion 通过。隔离 Docker MySQL 与 PostgreSQL 真实 Chromium 各 20/20，通过已发布问卷完成名单 → 联系人 → audience → 服务端 503 失败路径，随后核对平台 API、平台数据库、网关和引擎数据库。真实 provider、成功投递、回执、催答、批量导入及生产验收继续留在后续切片。

## 2026-10-28 阶段 4：任务重访恢复与测试投递契约

前端以 `sessionStorage` 按租户、操作人和问卷保存最近创建的任务 ID；重访问卷内页面时只向既有脱敏任务/收件人接口读取服务端状态。403/404 会删除该恢复标识，并显示“任务不可访问或已不存在”，不会继续渲染旧任务数据；取消失败也保持任务可见并给出可恢复的中文说明。全量管理端 Vitest 369/369、typecheck、lint、生产构建和 production bundle assertion 通过。

`DeliveryApiTest` 新增仅测试上下文的成功发送契约：已保存 audience 创建的 Email 任务可由 `src/test` 的 `FakeEmailProvider` 完成一次投递，任务转为 `completed` 且发送计数正确，而读取任务收件人依旧不返回完整地址。该 Fake provider 不进入生产源码或镜像，不能被视为真实供应商接入。隔离 Docker Maven 目标类、MySQL 与 PostgreSQL 真实 Chromium 各 20/20 均通过；后两项继续验证无 provider 的真实 503 路径及全栈回归。

## 风险与停止条件

- 当前后端 `DeliveryTaskController` 只接受调用方提交的 recipients，不能被前端直接复用为受众投放；若受众物化不能在不扩大 PII 或权限范围的前提下完成，停止并保留证据。
- 未配置 provider 是首批必须覆盖的真实失败流，不允许为了演示而插入成功状态。
- 双库真实门禁中同一问题连续两次失败时，停止自动修复、保留日志，并请求用户在新窗口继续。
- 正式 Release、公开 TLS、生产安装/升级/恢复不属于本计划的完成条件。
