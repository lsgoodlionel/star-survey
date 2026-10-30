# Wave 4 受众与投放实施计划

状态：Wave 4 蓝图本地功能已准出（2026-10-30）；其他邮件供应商真实联调按用户决定延期，正式生产环境留待 Wave 6。实施分支：`feat/wave3-data-productization`；保持现有堆叠 PR 关系，不变基或合并父分支。

## 目标与边界

Wave 4 的首个纵切位于已发布问卷的上下文中：运营人员创建或选择联系人名单、配置问卷受众、从该受众创建 Email 投放任务、查看脱敏收件人和任务状态，并在未配置真实通道时明确显示不可用而不伪造投递成功。

不做全局一级“投放”导航，不实现短信或回执 Webhook。Email SMTP adapter 只支持 Gmail、Outlook.com/Hotmail、网易 163 的固定配置档，批量 worker 默认关闭，租户凭据使用部署侧主密钥派生的 AES-GCM 密钥加密；SMS 暂停。SMTP 接收不等于投递回执，网络中断恢复只能做到至少一次尝试，不能声称跨供应商恰好一次。投放链接/二维码现有发布页能力不改变。

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

## 2026-10-29 阶段 5：问卷内联系人批量导入

问卷内页面现可向选中 respondent 名单提交 CSV/JSON 文本导入，提交携带幂等键并显式接受既有 `202 Accepted` 作业响应。页面轮询作业计数；作业进入终态后才读取逐行结果，避免运行中空结果被缓存。页面仅展示聚合计数、行号、结果与原因码，不回显提交内容、地址或联系人详情。最近导入作业以租户、操作人、问卷和名单范围保存；重访时只读服务端状态，403/404 会清除恢复标识并提示不可访问。

前端类型化 API/页面测试覆盖 CSV 提交、queued 到 completed 的结果读取、403/404 恢复清理、Idempotency-Key 与无 PII schema。管理端全量 Vitest 373/373、typecheck、lint、生产构建和 production bundle assertion 通过；隔离 Docker MySQL 与 PostgreSQL 真实 Chromium 各 20/20，旅程完成一行 CSV 导入、验证第 1 个数据行的脱敏结果，再继续名单、audience 与无 provider 的 503 路径，并核对平台 API、平台数据库、网关和引擎数据库。真实 provider、回执、催答和生产验收仍留在后续切片。

## 2026-10-29 阶段 6：任务发现与催答规则管理

新增受限的 `GET /v1/surveys/{surveyId}/delivery-tasks?limit=50`：先要求问卷 `VIEW`，限制 1–100 条最新任务，返回仅含类型、状态和统计的 `TaskView`，不包含收件人地址、正文或回执信息。问卷内页面可从该脱敏列表重访任务；仅当前 `invite` 任务显示催答规则管理。

既有催答规则创建/读取接口现补充 `PATCH /v1/delivery/surveys/{surveyId}/reminder-rules/{ruleId}` 启停动作。服务端先核对 rule 归属 URL 中问卷，再要求 `SEND`，跨问卷规则按不存在处理。页面将分钟转换为受服务端边界约束的秒数，强制正文带 `{{unsubscribe}}`，展示启用/暂停与上限；不展示收件人、正文回显或回执密钥。`DeliveryApiTest` 覆盖脱敏任务列表与规则创建/启停，`DeliveryReminderTest` 保持完成者跳过、退订抑制、上限与恢复的测试 provider 契约。管理端全量 Vitest 375/375、typecheck、lint、生产构建和 production bundle assertion 通过；隔离 Docker MySQL/PostgreSQL 真实 Chromium 各 20/20，确认真实运行栈无回归和无 provider 的失败语义。真实 provider、真实发送/回执联调仍非本阶段证据。

## 2026-10-29 阶段 7：已认证回执只读视图

`GET /v1/delivery/tasks/{taskId}/receipts?limit=50` 先在当前租户读取任务并要求问卷 `VIEW`，再返回最多 200 条最近的已验签、已去重回执。响应只含收件人内部标识、provider、类别、计费标识、事件时间与平台接收时间；不返回地址、正文、渠道商 eventId 或消息 ID。管理端任务详情展示中文回执类别和时间，并明确 SMTP 已提交不等于送达；暂无回执与读取失败分别可辨。`DeliveryReceiptTest` 覆盖去重回执后的安全读取，`DeliveryApiTest` 回归 API 权限路径；前端 API/页面定向测试、typecheck 与 lint 已通过。真实供应商回执仍须另行验收。

## 2026-10-30 阶段 8：租户级 SMTP 设置、真实提交与生产密钥闭环

新增仅 `MANAGE_SETTINGS` 可访问的“邮件服务设置”页及 `/v1/delivery/email-settings` API。Gmail、Outlook.com/Hotmail 与网易 163 使用固定 TLS/认证配置档；授权码或 OAuth token 以租户隔离的 AES-GCM 密文保存，不回显、不进入浏览器持久化。“仅验证登录”只认证 SMTP 会话；“发送测试邮件”必须由管理员明确填写收件人并点击，且结果文案只声明 SMTP 接受。网易 163 隔离测试账户已完成登录验证、页面测试邮件提交和一名受众的真实批量任务，任务由 `queued` 进入 `completed`、收件人由 `queued` 进入 `sent`；这证明应用到 SMTP 服务端的提交链路，不证明收件箱送达、打开或真实回执。

生产基础 compose、`surveyctl install` 和测试 fixture 已补齐 `platform_delivery_secret` 的生成、`0600` 文件路径、容器挂载与 `PLATFORM_DELIVERY_SECRET` 注入；批量 worker 由 `PLATFORM_DELIVERY_ENABLED` 控制且默认 `false`。静态 SMTP 覆盖仍可作为迁移回退：租户没有保存设置时无需动态设置主密钥；一旦存在租户设置但密钥缺失，发送失败关闭，不能静默改用共享发件账户。生产运维回归 105 项通过（1 项按环境跳过），邮件设置/adapter 定向测试通过。真实供应商回执 Webhook 和 Gmail、Outlook.com/Hotmail 隔离账户实测仍待后续验收。

## 2026-10-30 阶段 9：正式投放工作区与 Wave 4 本地准出

新增正式一级 `/delivery` 投放工作区与全局“投放”导航。运营人员可在同一页面创建/选择名单、创建带父级的部门、创建标签、向名单新增并停用联系人、指定部门、分配标签，以及提交 CSV/JSON 导入任务并查看无 PII 的逐行结果；页面可继续进入项目与问卷选择具体投放上下文或邮件服务设置。问卷内任务详情把逐收件人状态、催答次数、退订、完成抑制和安全错误码全部转为中文，不再暴露英文裸值。

真实浏览器旅程从一级投放工作区创建名单、部门、标签和联系人，完成标签分配与一行批量导入，再贯通问卷编辑、真实预览、发布、链接/二维码、受众保存、未配置渠道 503、正式作答、答卷与导出；隔离 Docker MySQL 与 PostgreSQL 均为 20/20，并通过平台 API、平台数据库、发布网关和引擎数据库交叉门禁。后端定向测试中 `DeliveryReminderTest` 11/11 证明完成者跳过、退订抑制、上限及崩溃恢复，`DeliveryReceiptTest` 9/9 证明回执验签、去重和防重放，`DeliveryApiTest` 14/14、`ContactApiTest` 5/5 通过。管理端全量 Vitest 378/378、lint、typecheck 与生产构建（2121 modules）通过。

动态 SMTP provider 的注册曾导致无租户配置时错误接受任务；现 provider 可用性按租户判定：租户动态设置或部署静态回退至少一个真实可用时才允许建任务，否则保持 HTTP 503，不产生排队假成功。网易 163 已有真实 SMTP 提交证据。按用户 2026-10-30 决定，Gmail、Outlook.com/Hotmail 及其他邮件供应商暂不联调，SMS 继续暂停；这些明确延期项不阻塞 Wave 4 蓝图本地功能 100% 完成，但不构成正式生产验收或跨供应商真实回执证据。

## 风险与停止条件

- 当前后端 `DeliveryTaskController` 只接受调用方提交的 recipients，不能被前端直接复用为受众投放；若受众物化不能在不扩大 PII 或权限范围的前提下完成，停止并保留证据。
- 未配置 provider 是首批必须覆盖的真实失败流，不允许为了演示而插入成功状态。
- 双库真实门禁中同一问题连续两次失败时，停止自动修复、保留日志，并请求用户在新窗口继续。
- 正式 Release、公开 TLS、生产安装/升级/恢复不属于本计划的完成条件。
