# 自治执行进度

计划：`docs/superpowers/plans/2026-10-11-template-branding-wave2.md`

分支：`feat/admin-product-alignment`

## 当前状态

Task 1: complete - Wave 2 已激活；治理完成记录见 SDD 账本。
Task 2: complete - 品牌资产跨 Java/Python/PHP 边界已实现并通过 2026-10-27 双库真实 E2E、PHPUnit、Gateway、Java 定向和 production 配置门禁；发布与预览的错误 channel key 回滚均验证问卷删除、上传目录清除及无作者品牌残留。能力矩阵仍按 Wave 2 完整六层证据维持原状态。
Task 3: complete - 模板、资产与品牌 typed client 已接入现有端点；FormData 上传、Zod 响应校验、租户缓存键及品牌草稿安全合并已实现。2026-10-27 定向 18 项与管理端全套 317 项通过，lint、typecheck、build、production bundle 检查通过；页面流程仍由 Task 4-7 承接。
Task 4: complete - 模板中心列表、范围与状态 URL 筛选、版本/历史详情、有效项目或文件夹目标选择及独立问卷复制已接入；工作区与全局导航可进入。2026-10-27 定向 29 项、管理端全套 325 项通过，lint、typecheck、build、production bundle 检查通过；真实浏览器与双库纵向验收留待 Task 7。
Task 5: complete - 用户确认后补入最小只读管理列表与模板动作能力接口，沿用同租户 RLS 和真实权限判定；前端已接入创建/添加版本、送审、批准、驳回、下架、运营 JSON 导入与选版上架。2026-10-27 Java 全量 1492 项/183 类（失败、错误、跳过均为 0），前端全套 345 项、lint、typecheck、build、production bundle 检查通过。Task 6–7 的品牌编辑器及真实浏览器纵向验收尚未完成，Wave 2 能力状态不提升。
Task 6: complete - 品牌与语言编辑页、受控图片选择/上传、题组/题目/子题/选项结构化译文、草稿乐观锁保存、未保存离开拦截及 dashboard 最近访问已接入；V934 仅扩展品牌页受控枚举并保留版本约束。2026-10-27 隔离数据库 Java 全量 1492 项/183 类（失败、错误、跳过均为 0），前端全套 352 项、lint、typecheck、build、production bundle 检查通过。Task 7 真实浏览器纵向验收及 Task 8 生产门禁仍待完成，Wave 2 能力状态不提升。
Task 7: complete - 2026-10-27 隔离 MySQL 与 PostgreSQL 真实浏览器均为 19/19，通过模板生命周期、平台模板复制、品牌/语言保存、409 冲突、带图预览、正式答卷、两版发布、旧版图片字节固定、无第三方请求及四断点布局；平台 API、平台 DB、网关与引擎 DB 交叉核验通过。预览 502 的最终断点是冷启动引擎未注册 zh-business，已复用既有主题安装器补齐；同时修复资产 JSON 枚举契约、受控环回 HTTP 物化地址、插件通道测试配置及品牌保存后发布概览缓存。前端 353 项、网关 1235 项、门禁单元 48 项、隔离 Java 1494 项/184 类通过（失败、错误、跳过均为 0），lint、typecheck、build、production bundle 检查通过。实现分界提交：1ed42c34a2、9b059a7523、c80e9b406b；最终实现提交为 c80e9b406b，Task 8 完成前不提升 Wave 2 整体能力状态。
Task 8: complete（Wave 2 本地功能验收口径）- 用户于 2026-10-28 指定本地 Docker 部署与真实使用验证作为功能审核和验收。当前实现提交 `2dc8e78725` 在隔离 MySQL、PostgreSQL Docker 栈中各通过 19/19 浏览器用例，平台 API、平台数据库、网关及引擎数据库交叉核验通过，容器已清理。GitHub 产品质量 run `40609740377` 为 10/10，Release 只读三镜像 AMD64/ARM64 构建 run `40609740345` 成功。正式 tag、GHCR/Release、公开 TLS 与生产安装升级恢复由后续 GitHub 发布及部署流程承接，不作为本次本地功能验收条件；能力矩阵涵盖的更广需求仍按部分完成记录。

2026-10-27 Task 8 接续核验（实现 HEAD `28a5e18925`，文档提交另计）：隔离 Harness run `0416bb7c-235d-4107-b050-6a05e45a304a` 的 Java、Gateway、管理端测试/静态检查/构建/production bundle 及文档门共 11 项通过；新增浏览器门在独立 worktree 的 Docker Desktop 嵌套挂载阶段失败，未执行浏览器用例，Harness 仍处于 repairing。原 worktree 直接复验 Java 1494 项/184 类、管理端 353 项、Gateway 1235 项通过；生产部署 Python 测试 105 项中 104 项通过，真实原生验收按前置条件跳过，Python 编译通过。MySQL 浏览器首次在 `authoring.spec.ts:594` 的“新建”按钮键盘焦点断言失败，隔离重跑 19/19；PostgreSQL 随后在同一断言失败（6 项通过、1 项失败、12 项未运行），依“两次同类失败即停”约定停止自动修复，保留 `/tmp/survey-wave2-browser-{mysql-final,mysql-retry,pgsql-final}.log` 脱敏诊断。GitHub 当前无 `v*` tag/Release；自动生成 `release.json` 的 `release.yml` 仅在开发分支，默认分支未注册，首版 baseline policy 为 `0.3.0-rc.1` bootstrap，第二版需真实前版 manifest SHA-256；用户确认尚未制作两版 Release。指定验收服务器的只读 SSH 连续两次连接超时；生产 Compose/Caddy、doctor、备份恢复和三路独立复审均无通过证据。Wave 2 未满足准出，Wave 3 新任务窗口尚未创建。

2026-10-27 用户改指定本机 Docker 验收：本机 Docker Engine 29.9.2、Compose 5.6.1 可用；生产 `compose.yml` 用仓库示例环境执行 `docker compose config --quiet` 通过，生产配置/单元测试在有 Docker API 权限的本机重跑 105 项，104 项通过、1 项真实原生验收按前置条件跳过。现有其他服务占用本机 80 端口，生产 Compose 固定绑定 80/443，且缺两版真实 Release manifest；因此未启动生产栈，也未声称 Caddy/doctor/升级恢复的真实产品验收通过。已有隔离管理端测试栈使用随机本地端口；上段记录的浏览器重复焦点失败仍待单独诊断。

2026-10-27 用户指示继续后，重复焦点失败与权限查询完成前开始 Tab 路径的时序竞争一致：测试先等待“新建”按钮可见，再保留完整搜索框→类型→状态→排序→新建的键盘顺序和可见焦点断言。仅测试时序修正提交 `4bc959c813`；本机隔离 Docker MySQL/PostgreSQL 全套真实浏览器各 19/19，平台 API、平台 DB、网关和引擎 DB 交叉核验通过，测试容器已清理；管理端 lint、typecheck 和 `git diff --check` 通过。该结果关闭本轮本地浏览器焦点复现，不改变 Harness 最终、真实 Release、生产 Compose/Caddy/doctor/备份恢复及三路独立复审的待办状态，Wave 2 能力状态不提升。

2026-10-27 同提交 `28dcab9b0d` 的独立干净 worktree `/Users/lionel/Develop/survey-wave2-gate-20261027` 运行 Harness `6f0b7c28-bd90-4be9-ad01-78c78eee1c36`：11 项文档、管理端、Java、Gateway 门禁通过；`admin-e2e` 在浏览器执行前被 Docker Desktop 对 `platform/deploy/test/.runtime/config.php` 的 bind mount 拒绝，0 个浏览器用例执行，run 为 repairing。此前另一独立 worktree 曾出现同一挂载失败，按两次同类失败停止自动修复；原 worktree 的 MySQL/PostgreSQL 各 19/19 是独立的直接验收证据，不能替代 Harness 成功。GitHub PR #11 本次 HEAD 的治理 run `40472080896` 仍因独立控制平面 `strict_unittest` 两项临时目录不可导入错误失败，平台业务修正没有新失败指纹。Release 与原生生产验收仍无制品和通过证据，Wave 2 保持未完成。

2026-10-28 用户指示继续处理后，以隔离 Docker 实验确认根因：全新检出缺少被忽略的 `application/config/config.php` 目标时，Docker Desktop 嵌套文件挂载报 `outside of rootfs`；预先创建目标后同一容器命令成功。`551ac98f9a` 在测试栈启动前仅为缺失目标创建空文件，保留已有配置内容；回归测试先失败后通过，相关 Python 测试 49 项通过。全新 worktree 初始目标缺失，Harness run `b55d2545-64a7-435c-a4c0-3dca1998ad8a` 在该提交上 12/12 门禁通过，含 MySQL 真实浏览器 19/19、Gateway 1235 项和 Java 全量；随后 PostgreSQL 真实浏览器 19/19 及跨层核验通过，隔离容器已清理。Harness 状态仍为 verifying；真实 Release、生产 Compose/Caddy/doctor/备份恢复、托管治理和三路独立复审仍待完成，Wave 2 不提升为完成。

2026-10-28 用户决定删除自建控制平面、保留其 GitHub 仓库，并以 Codex 原生功能及插件开发、GitHub Actions 在线 CI 控制质量。Survey 的 `agent-governance` 工作流已在 GitHub 停用；项目移除控制平面程序及配置，产品质量工作流保留既有产品、双库、浏览器和 Release 检查，使用普通 Python 测试入口并继续拒绝意外跳过和空测试集。真实生产验收、产品审查及当前提交的 GitHub Actions 仍需分别验证；此前 Harness 记录属于历史证据，不再是准出要求。

2026-10-28 控制平面移除及在线 CI 切换提交 `dcef1810f85bb368984fef253718624022609510` 的 [GitHub 产品质量 run 40604203635](https://github.com/lsgoodlionel/star-survey/actions/runs/40604203635) 成功，10/10 作业通过，包括真实浏览器、Java、P1、MySQL/PostgreSQL 访问策略与双执行比对，以及产品化和生产发布测试。该结果不代替真实 Release 的安装、升级、备份恢复与生产验收，Wave 2 保持未完成。

2026-10-28 Wave 2 Task 8 接续：产品质量工作流在当前 PR #11 的 `3aa6881790` 提交上 10/10 通过。Release 工作流原本只对目标为 `main` 的 PR 执行，堆叠 PR #11 因此没有三镜像构建验证；已改为对任何目标分支的相关 PR 执行只读的 `build-local` 路径，并增加触发策略回归断言。本地 Release 测试 58/58 通过。线上 Release 工作流结果须在新提交推送后核对；真实 tag、GHCR 镜像、双版 Release 与原生生产生命周期验收仍未完成。

2026-10-28 新触发的 Release CI `40607037885` 暴露跨工作流并发组冲突：复用 `platform-quality.yml` 时，独立产品质量 run `40607037755` 被取消。根据 GitHub 对 reusable workflow 的 `github.workflow` 上下文说明，已将调用方工作流名纳入质量门并发组，避免互相取消；回归测试先红后绿，Release 测试仍为 58/58。修复提交 `0fb16c8c7d` 的 [独立产品质量 run 40608389887](https://github.com/lsgoodlionel/star-survey/actions/runs/40608389887) 成功，10/10；[Release 构建 run 40608389960](https://github.com/lsgoodlionel/star-survey/actions/runs/40608389960) 成功，复用质量门与 admin、platform、publish-gateway 三镜像 AMD64/ARM64 只读构建均通过。PR 路径未发布镜像或 Release；真实 tag、双版制品和生产生命周期验收仍未完成。

2026-10-28 用户调整 Wave 2 准出：完成本地 Docker 部署、真实使用和验证即视为该 Wave 的功能审核及验收；其他版本 Release 由 GitHub 发布流水线处理。本次在 `2dc8e78725` 分支提交上重新执行隔离 MySQL 与 PostgreSQL 栈，各 19/19 浏览器用例通过，并确认平台 API、平台数据库、引擎数据库与网关状态一致；容器已清理，对外端口只绑定环回地址。[最终提交产品质量 run 40609740377](https://github.com/lsgoodlionel/star-survey/actions/runs/40609740377) 10/10 通过，[Release 构建 run 40609740345](https://github.com/lsgoodlionel/star-survey/actions/runs/40609740345) 成功。按本地功能切片完成 Wave 2，准备 Wave 3；正式 Release、公开 TLS 和生产安装/升级/恢复继续单列，不能由本地测试推断已完成。

2026-10-12：用户要求生成报告准备切换模型，暂停新增实现。接续入口为
[模型切换交接报告](run-history/2026-10-12-wave2-model-handoff.md)。
本轮网关 1,232 项和既有品牌双语 MySQL E2E 通过；不代表新增 brandAssets 真实链路通过。
这段 2026-10-12 历史记录当时没有完整交付证据；Task 2 的后续验收见下方当前状态和[接续记录](run-history/2026-10-12-wave2-model-handoff.md)。Task 2 完成不提升 Wave 2 整体 capability 或 Harness completed 状态。

Dashboard Task 1: complete - 治理 HEAD 绑定、hosted agent-governance run 19 与控制平面迁移已完成。

Wave 2: local-function-accepted - 模板与品牌计划切片按 2026-10-28 用户指定的本地 Docker 双库真实使用口径完成；正式 Release 与生产部署另行验收，接续 Wave 3。

2026-10-28 仓库入口文档更新：根目录 README 已按现有代码、Wave 2 本地双库验收和 GitHub CI 证据改为 Survey 项目说明，列出本地 Docker 演示入口、开发检查命令及正式 Release 的未验收边界；多语言 README 的生成方式另在独立任务分析，本次不增加翻译或发布流水线。

2026-10-28 README 多语言首批：以 `96086f6f42` 的中文 README 为内容基线，新增英文、日文完整翻译草稿与语言导航；`docs/readme-languages.json` 是版本清单，`scripts/docs/check_readme_translations.py` 核对导航、代码块、链接和关键产品事实。首次线上翻译 Action 在提供商请求阶段返回 HTTPError；复核后确认备选 GitHub Models 服务已退役，仓库亦未配置受支持的翻译 API 密钥。因此将工作流收敛为只读一致性检查，自动生成候选 PR 待确定翻译服务后再接入。译文需技术和语言人工复审；本地静态检查通过，线上检查结果单独核对。

## Token 控制平面（2026-10-12）

Task 1-10: delivered - Context Capsule、Gate Summary/证据复用、并发租约、Token Telemetry、
GitHub 受信摘要、全局规则安装与 Obsidian 单向投影均已实现，并通过独立复审。完整 Harness 套件 490 项通过，8 项仅因
当前 macOS 无法执行 Linux 权限夹具而跳过；12 个故障演练全部通过。真实 Survey Capsule 为 7,538 bytes，
相对 204,800-byte 基线减少 96.32%，启动 active-token 估算减少 96.23%。

实现与验收提交 `d586196354` 已推送到 `origin/feat/admin-product-alignment`；Obsidian 的项目总览与进度路线图
两个既有 marker 块已同步且无 pending。两个预先暂存的 Wave 2 测试脚本执行位及全部业务改动均未纳入该提交。

本次优化不提升 Wave 2 业务能力状态。后续仍从 Task 2 验收断点继续：先运行 PHPUnit，再完成品牌图片
发布/预览的 MySQL 与 PostgreSQL 真实双库验证；只有这两项通过后才进入 Task 3。35%-45% active Token
降幅仍是预测，需在后续两个产品 Milestone 结束后用项目归属明确的遥测复核。

最新后期进度、Wave 0-6 成熟度、剩余工作量和 Token 速度见
[2026-10-12 Survey 后期开发与整体进度分析](../audits/2026-10-12-survey-late-stage-progress-token-analysis.md)。
该报告维持产品口径 `accepted=0/12`，不把当前未提交的 Wave 2 Task 2 代码计为交付；短窗口 Token
下降仅作为趋势证据，不替代两个代表性产品 Milestone 后的复测。

## Dashboard 历史证据（非本批 Wave 2 验收）

- Dashboard 实现提交：`3ec78f09706c20b1a2ead20e19ee3f42993ab87e`。
- Harness 实现与 hosted fixture 修复提交：`b40a41099f9d5c4824ec8e34b873a32def46733b`。
- 本地 Harness run：`6c118d24-f546-4cbc-9277-edb5db52015b`，16/16 gates 通过。
- Admin Web：30 个测试文件、299 项测试通过；lint、typecheck、build 和 production bundle 校验通过。
- 平台：1478 项测试、182 个测试类，零 failure、error、skip。
- 真实浏览器：15/15 场景通过，覆盖 819/820/1179/1180 边界。
- Harness 非 Linux 单元测试：411/411 通过；Git guard：53/53 通过。

## 遗留边界

- GitHub Actions run 19 已验证 fresh checkout 的可追踪账本契约；Wave 2 业务实现与后续六层交付证据仍待完成。
- 仓库证明要求来自 `main` 的 GitHub artifact attestation；功能分支不得伪造完成状态。
- Wave 2、4、5、6、生产 Release、公开 TLS 与服务器部署仍按父计划后续推进。

## 更新规则

本文只记录脱敏摘要、当前 HEAD 的 Gate 结果、完成项、遗留问题和下一步。详细 SDD 执行日志保存在忽略目录，不进入仓库；秘密、原始模型事件和完整运行日志不得写入本文。
