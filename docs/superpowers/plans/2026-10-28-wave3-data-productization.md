# Wave 3 数据产品化实施计划

状态：本地功能切片、双库故障路径和 GitHub CI 已核对通过（2026-10-28）；生产 Release 与生命周期仍不在本 Wave 验收范围内。基线：`feat/admin-product-alignment` 的 `247cefda1a`；实施分支：`feat/wave3-data-productization`。

## 目标与边界

沿用既有答卷投影、字段字典和导出作业，实现答卷列表、跨版本复合筛选、字段选择、摘要，以及导出任务创建、进度、取消、失败后重建、下载和过期状态。前端只展示真实支持的 `csv`、`xlsx`、`sav`、`docx`、`attachments`。不加入统计分析、PDF、通讯录、投放或新业务包。

Wave 2 的本地功能验收以 2026-10-28 用户指定的双库 Docker 各 19/19 为准；正式 Release、公开 TLS 和生产生命周期继续单列，不作为本计划已经完成的事实。能力矩阵在六层证据齐全前保持原状态。

## 已锁定的契约

1. **游标**：明细依 `ResponseCursor` 的不可变 `(版本, 代次, 答卷号)` 键集排序，服务端 SQL 下推状态、版本、开始与完成时间区间。游标只用于翻页，每页重新检查明细权限。开始时间谓词稳定；状态与完成时间会变化，按查询时状态解释。导出使用独立作业快照，不复用列表游标。
2. **权限与脱敏**：摘要需要 `view-statistics`；明细需要 `view-raw-responses`；敏感字段按服务端 `view-sensitive-fields` 判定遮蔽。字段字典仅包含发布快照的元数据，不能被前端用于反推明文。无明细权限的角色不显示逐行时间和导出操作。
3. **导出快照**：创建时冻结版本、来源、字段字典和水位线；作业物化有序键并分批生成。当前导出契约仅支持状态、版本过滤；列表时间区间仅影响列表，界面必须清楚说明，不把时间过滤伪称为导出范围。
4. **任务恢复**：运行中作业按租约和检查点恢复；失败、取消或过期后由用户按当前筛选创建新作业，不复用旧文件或伪造成功。前端保存当前作业 ID 以便重访后重新查询；服务端仍按当前身份授权。
5. **下载再授权**：`GET /v1/exports/{jobId}/download` 每次检查创建者、导出权限及敏感字段权限；未就绪返回 409，到期返回 410。页面只在服务端状态为完成时显示下载动作；异常不生成本地假文件。

依据：`platform/docs/adr/0013-response-query-and-answer-reads.md`、`platform/docs/adr/0015-response-export-jobs.md`、`platform/services/business/src/main/java/cn/mjy/platform/response/` 和 `platform/apps/admin-web/src/shared/api/`。

## 顺序与验收

### 阶段 1：页面契约与交互

- 为字段字典增加类型化客户端，按版本映射字段标签；字段选择只控制显示，服务端脱敏始终优先。
- 增加状态、版本、开始/完成时间复合筛选和游标重置；明确列表与导出过滤范围差异。
- 让作业状态在页面重访后可恢复；失败、取消、过期显示重建动作与说明。
- 先增加失败测试，再实现；运行管理端定向测试、全量测试、lint、typecheck、build 和生产包检查。

### 阶段 2：真实业务流程

- 核对 Java、Python 网关、PHP 引擎的现有跨层契约；仅在发现冻结范围内的必要缺口时作最小修补。
- 在隔离 Docker MySQL 和 PostgreSQL 栈各完成正式作答、跨版本查询、复合筛选、字段遮蔽、摘要与五种真实导出格式的创建、状态、取消、失败/过期恢复和下载再授权。检查平台 API/数据库、网关与引擎答卷的一致性；不使用未跟踪的敏感夹具。

### 阶段 3：交付同步

- 通过范围内测试和双库真实流程后，更新既有进度、README 或路线图中的本次事实；按功能边界提交，推送登记的 `origin` 当前功能分支，核对 GitHub 产品质量与 Release 只读构建结果。
- 同步 Obsidian 既有项目总览或进度页，记录日期、分支、提交、测试证据、遗留问题和下一步。保持堆叠 PR 关系；不自动合并、重置、制作正式 tag 或发布 Release。

## 2026-10-28 执行断点

阶段 1 的时间复合筛选、字段字典与显示选择、导出任务 ID 重访恢复已有未提交实现。管理端 356 项单测、lint、typecheck、生产构建和 bundle 检查通过。初版浏览器增强在隔离 MySQL 与 PostgreSQL 各 19/19 且跨层核验通过；后补的双版本与五格式真实验收尚未通过。

新增双版本浏览器场景第一次在第二版问卷欢迎页寻找单选项，600 秒超时；修正进入题目页的测试时序后，第二次走到双版本答卷列表，但 `getByLabel('发布版本')` 同时命中摘要与筛选框，Playwright strict mode 拒绝，13 项通过、1 项失败、5 项未运行。这两次属于同一新增浏览器场景连续失败，当时按项目协作规则停止自动修复。第二次失败位于 `platform/apps/admin-web/e2e/template-branding.spec.ts:309`；当时五种格式下载断言尚未执行，分支未提交或推送。以下接续记录覆盖此断点，能力矩阵仍不得据此提升。

## 2026-10-28 接续验证

用户在保留现场后明确要求继续开发。版本筛选浏览器定位改为准确的 `combobox`，并让五格式循环核对每次创建响应与任务格式，避免把上一任务的完成状态误判为当前成功。隔离 Docker MySQL 与 PostgreSQL 最终各 19/19；新增场景在真实引擎完成两版正式作答、跨版本列表与版本筛选，并创建、下载非空的 CSV、XLSX、SAV、DOCX 和附件包。平台 API、平台数据库、网关及引擎数据库交叉核验通过，测试容器已清理。管理端全量 356/356、lint、typecheck、build、production bundle 通过；浏览器门禁要求的时间输入包装器已按项目规则改用 `fillAction`，全量单测再次 356/356。

推送前只读核对：登记仓库为 `lsgoodlionel/star-survey`，PR #11 仍开放且堆叠在 `feat/admin-web-drag-sort`；`origin/feat/admin-product-alignment` 已前进至 `b8535c203f`，本分支从已确认的 Wave 2 收尾提交 `247cefda1a` 开始，远端尚无 `feat/wave3-data-productization`。父分支该提交的质量门 run `40616502706` 与只读镜像构建 run `40616502723` 均成功。本分支尚无 CI 结论；后续 GitHub 同步只针对本分支，不合并或改写父分支。

增量故障证据：新增浏览器旅程以隔离 PostgreSQL 夹具验证 queued 作业取消、failed/expired 状态在页面的恢复提示，以及真实完成后撤销 `raw_data_viewer` 的下载 403；组件测试覆盖 403 后保留已完成任务以便恢复授权后重试。后端 `ResponseExportAccessTest` 新增运行中撤权后旧 job 不可下载、恢复后只能新建并按当前权限冻结脱敏产物的回归，目标类 7/7 已通过。新增浏览器旅程的 MySQL 全量门禁两次均在测试夹具/会话导航处中止（首次为 expired 夹具违反时间约束，第二次为 `page.goto` 清空产品有意的内存会话）；已改为合法时间和 SPA 路由重挂载，但遵循两次同类问题即停止自动修复的约定，未发起第三次 MySQL 或 PostgreSQL 复跑。

闭环记录：撤权夹具补充事务内租户作用域与恰好删除一条授权的断言后，修正后的隔离 Docker MySQL/PostgreSQL 真实浏览器各 20/20，平台 API、平台数据库、网关和引擎数据库交叉核验通过。`cde9ba4531` 与文档提交 `dced3debf5` 已推送；准确提交的[产品质量门 40657127681](https://github.com/lsgoodlionel/star-survey/actions/runs/40657127681) 10/10 成功，[非 tag Release 只读构建 40657133380](https://github.com/lsgoodlionel/star-survey/actions/runs/40657133380) 成功，复用质量门及 admin、platform、publish-gateway 三镜像构建均通过，发布作业按策略跳过。

边界：Wave 3 的本地功能准出证据已闭环；但正式 tag、GHCR/Release、公开 TLS、生产安装/升级/恢复及六层生产证据并未因此完成，能力矩阵继续保持 partial，不将本记录写成整体 production-ready 或 accepted。

## 2026-10-28 撤权夹具修复与双库复验

MySQL 再次复验在撤权后下载返回 200（预期 403）处失败，10 项通过、1 项失败、9 项未运行。只读核对发现撤权 SQL 未设置 `app.tenant_id`，而 `access_resource` 和 `access_grant` 对 `platform_owner` 强制租户行级安全；删除语句没有确认影响行数。应用户要求继续后，撤权夹具在事务内设置租户作用域并要求恰好删除一条授权。

修复后隔离 Docker MySQL 和 PostgreSQL 管理端真实浏览器各 20/20，包含 queued 取消、failed/expired 页面状态、完成后撤权下载 403；平台 API、平台数据库、网关和引擎数据库交叉核验均通过。定向 ESLint、TypeScript typecheck 与 `git diff --check` 通过。旧提交 `ccc12ddf46` 的产品质量 run [40648943924](https://github.com/lsgoodlionel/star-survey/actions/runs/40648943924) 与非 tag Release run [40648947745](https://github.com/lsgoodlionel/star-survey/actions/runs/40648947745) 均因浏览器验收作业失败，后者镜像构建跳过；新提交 CI 以实际运行结果为准。正式 Release、公开 TLS 和生产生命周期仍待另行验收，六层能力矩阵不因此提升。
