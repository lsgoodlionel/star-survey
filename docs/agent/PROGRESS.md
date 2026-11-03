# 自治执行进度

2026-11-02 PR #28 合并接续：用户直接授权合并后，因 main 已合入 PR #29，先普通合入 main `7eb63e59fb82d7fe64ba5175d537f1dbb68c1dac`；唯一冲突为本文件顶部新增记录，已保留策略登记与 CI 优化双方内容。相对最新 main 的业务差异仍仅 `0.3.7-rc.1: bootstrap` 及本进度记录。更新后 Release 全套与 CI 待复核；不创建标签或 RC。

2026-11-02 下一候选策略登记：基于远端 main `14e4005b35cddf1786dd5e3eb002f36bfabde67c` 建立独立分支 `codex/wave2-rc7-policy`，本地与远端均未发现 `v0.3.7-rc.1` 标签，基线文件无同名策略后，新增 `.github/release/baseline-policy.json` 的 `0.3.7-rc.1: bootstrap`。只登记策略，未创建标签或 RC，未修改旧策略或功能。Release 全套 60/60、`git diff --check` 通过。bootstrap 不代表升级验证通过或稳定版可推广，完整原生生命周期仍待发布后独立验收。推送前 main 精确提交的 [质量 run 41390926881](https://github.com/lsgoodlionel/star-survey/actions/runs/41390926881) 成功，10/10 作业通过；本分支 CI 尚未触发，推送后核验，不自动合并。

2026-11-02 PR #27 主线最终结果补记（覆盖下方历史待查状态）：PR 已普通合并至 `14e4005b35cddf1786dd5e3eb002f36bfabde67c`，main 质量 10/10 成功；合并前 head `3cce221e68` 的质量与 Release 只读构建均成功，四节点有序启动诊断成功。修复包含 edge 有限等待和 admin→platform 健康依赖；主线质量与启动诊断不能替代正式四节点完整生命周期及 Release readiness。
2026-11-02 CI 优化候选：按用户确认的顺序，在独立分支 `codex/ci-streamline` 基于 main `14e4005b35cddf1786dd5e3eb002f36bfabde67c` 实施 PR/main 单一 CI 入口，正式 Release 不再监听 PR；质量及四镜像只读构建保留可复用模块。仅 Markdown 文档和 baseline-policy PR 允许轻量分流，未知或不完整证据回退完整检查，main/正式 Release 保留完整质量门。名称已区分 CI、Release、质量模块与 PR 标签管理；标签流程合并为一个作业并适配 main。稳定汇总 `Survey CI required` 核对成功与合法跳过，未修改分支保护、发布安全门或业务功能。新增分流、分页/改名、失败阻断、标签未知状态回归先红后绿；Release 全套 74/74、生产 115 项中 114 通过/1 原生生命周期按前置条件跳过、actionlint 1.7.12（官方 SHA-256 校验，五工作流）、Python 编译、diff 检查通过。GitHub 推送/PR 验证待完成，不能据此宣称线上优化已验收；原工作区既有改动保留，未合并或创建标签、RC。

2026-11-02 PR [#27](https://github.com/lsgoodlionel/star-survey/pull/27) 原生诊断补证：首轮诊断缺少 platform DNS，四节点失败；修正为延迟启动 platform 的生产时序后，[第二轮 41382574536](https://github.com/lsgoodlionel/star-survey/actions/runs/41382574536) 四节点通过并捕获 nginx `host not found in upstream platform`，随后自动重启恢复 healthy。该复现机制与旧 ARM64 快照一致，但旧节点未保存 nginx 日志，不能断言旧失败的完全归因。新增 admin→platform service_healthy 依赖，保持所有探针参数；回归先红后绿，生产 115 项中 114 通过、1 原生生命周期按前置条件跳过。PR 原生诊断改验有序启动、无重启、无 DNS 错误。前提交质量/构建因新推送取消；eccb9ca48a 的质量与构建仍进行中；新修复推送后需以精确 HEAD 查询，不将诊断等同 Release readiness。

2026-11-02 原生启动修复：操作前登记 origin/main 与不可变 v0.3.6-rc.1 均为 `62e071d029a847a2b0b8a06cd0320aa0fa386bdd`。Release run 41377811981 四节点 install 失败。独立分支 `codex/wave2-native-health` 修复 running/starting 健康汇总竞态，最多等待 120 秒；unhealthy、服务缺失、异常退出仍阻断，status 保持即时检查。回归测试先红后绿；生产测试 114 项中 113 通过、1 项真实原生验收按前置条件跳过。隔离 Docker 容器真实 starting→healthy 等待通过，其余服务建模，不代表完整栈验收。22.04 ARM64 admin-web 短暂 unhealthy 原因仍未知，PR 四节点诊断使用原 admin digest 保存探针历史，不能替代 readiness。推送前分支无 CI；推送后结果待查。不合并 PR、不创建或移动标签、不创建 RC。

计划：`docs/superpowers/plans/2026-10-11-template-branding-wave2.md`

分支：`main`（全分支合并见 PR #13）

## 当前状态

2026-10-28 全分支合并完成：PR [#13](https://github.com/lsgoodlionel/star-survey/pull/13) 经最新候选 `cf13af165ae5` 全部检查通过后，以保留分支祖先的 merge commit 合并到登记仓库 `main`，合并提交为 `dd8fb35a4d05`。最新候选的 GitHub 检查 36 项中 25 成功、11 条件跳过、0 失败，包含两条 MySQL 真实浏览器 Job、Java 全量、双库访问策略与双执行比对、P1 纵切，以及 admin/platform/publish-gateway 多架构只读构建；运行 [40631144648](https://github.com/lsgoodlionel/star-survey/actions/runs/40631144648)、[40631144698](https://github.com/lsgoodlionel/star-survey/actions/runs/40631144698)、[40631144803](https://github.com/lsgoodlionel/star-survey/actions/runs/40631144803)。本地隔离 PostgreSQL 真实 Chromium 浏览器 19/19 通过，平台 API、网关、平台及引擎数据库交叉核验与运行根归档通过，隔离容器已清理。原始敏感备份仍不在合并历史中，脱敏备份及上游 `master` 在合并历史中，原始历史独有对象与候选可达对象交集为 0。正式 Release、生产安装/升级/恢复及公网 TLS 仍需独立验收。

2026-10-28 PR #13 验收：全分支候选 `76be7b0d0ea8` 已普通推送至登记的 `origin`，草稿 PR [#13](https://github.com/lsgoodlionel/star-survey/pull/13) 指向仍为 `d84c271daca3` 的 `main`。该提交的 GitHub 检查全部结束，36 项中 25 成功、11 条件跳过、0 失败，包含两条管理端真实浏览器 Job、Java 全量、双库访问策略与双执行比对、P1 纵切，以及 admin/platform/publish-gateway 三个不访问注册表的多架构只读构建；运行 [40629761591](https://github.com/lsgoodlionel/star-survey/actions/runs/40629761591)、[40629761601](https://github.com/lsgoodlionel/star-survey/actions/runs/40629761601)、[40629761521](https://github.com/lsgoodlionel/star-survey/actions/runs/40629761521)。本地隔离 PostgreSQL 真实 Chromium 浏览器 19/19 通过，跨平台 API、网关、平台及引擎数据库的校验与运行根归档通过；隔离容器已清理。首次本地运行因候选工作区缺少 `playwright` 命令而未执行断言；按本机有界测速选择较快的 npmmirror 安装锁定依赖、切换项目要求的 Node 22 后第二次通过。后续待最新文档提交的 GitHub 检查及 PR 合并。

2026-10-28 Git 对象补齐完成：按用户继续下载至 100% 的指示，先从引用匹配的 `ghfast.top` 获取 64 KiB 以下对象，使候选缺失数从 181232 降至 46070；再在隔离 bare 仓库按对象 ID 分 92 批获取剩余对象。最终 pack 的 46070 个对象与候选缺失集合逐项完全匹配，`git verify-pack`、候选 HEAD 的 `git fsck --connectivity-only` 通过，导入后候选缺失数为 **0**。再次核对远端 `main` 为 `d84c271daca3`、上游 `master` 为 `4c20c68033c8`，两者均为候选祖先。原始敏感备份不是祖先，脱敏备份是祖先，原始历史独有对象与候选可达对象交集为 0。候选仍未推送；下一步是普通推送、PR、GitHub CI、真实浏览器和 `main` 合并。

2026-10-28 中国镜像接续：已将此前遗漏的 `codex/survey-autofix` 分支纳入全分支候选 `6f5c6c19ce40`，并保留候选的手动触发、两次自动修复上限；新增失败日志文件范围限制。自动修复定向测试 9 项通过，工作流 YAML 可解析；除原始敏感备份外，核对的 87 个本地及 `origin` 分支引用均已成为候选祖先。原始备份仍不是祖先，脱敏备份是祖先，原始历史独有对象与候选可达对象交集为 0。官方源约 28 KB/s；`gitclone.com` 返回 504，`ghproxy.cn` 不支持该 Git URL，`ghproxy.net` 约 27 KB/s；引用匹配的 `ghfast.top` 有界测速约 472 KB/s。经用户明确要求改用中国镜像，HTTP/1.1 全量 Git fetch 在约 1.00 GiB、60% 处以 `curl 18`、`early EOF` 中断，候选仍缺 181232 个上游对象。结合前次镜像中断，按两次同类失败即停约定停止自动补齐。候选未推送，PR、GitHub CI、真实浏览器和 `main` 合并均未执行；诊断保留在本机 `/tmp/survey-ghfast-fetch-20261028.log`，原工作区未提交改动未触碰。本机 `gh` CLI 登录状态显示令牌失效，实际 Git 推送认证尚未验证。

2026-10-28 全分支合并候选：在隔离工作区基于远端 `main` 集成产品对齐（含多语言 README 提交）、PDF 导出、其他分支及 LimeSurvey 上游独立历史；与旧网关和重写前作者工作台分支重复的实现保留当前版本。`codex/survey-autofix` 仅保留手动触发，自动修复至多两次。前端 353 项、lint、typecheck，网关 1235 项，Java 全量 1513 项/186 类（失败、错误、跳过为 0）及 PDF 单元测试 14 项通过。候选尚未推送或合并到 `main`，真实浏览器与 GitHub CI 待验证。

2026-10-28 重写前 P0 备份历史核验：原始 `backup/pre-rewrite-20260921` 保留在本地，不得直接推送。上次临时副本因未检出工作区呈现大量删除状态，`filter-branch` 两次拒绝运行；本次改用隔离 bare 仓库，按 `platform/tools/secure-docs/sensitive.txt` 中的 5 个路径重写 16 个提交。逐提交比对确认其他文件无差异，5 个原始明文 blob 均不在脱敏分支的可达历史中，也不存在于只含脱敏分支的干净 bare 仓库。脱敏提交 `5717499c7a14` 已是合并候选的祖先，原始备份提交不是；候选对这 5 个 blob 的可达数为 0，无需再次合并。可审阅本地分支为 `codex/sanitized-pre-rewrite-20260921`。该次核验时候选有 181232 个缺失的上游对象，且 `github.com` DNS 不可解析；先补齐对象并确认远端状态，再进行候选推送、在线 CI 和真实浏览器验证。

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

2026-10-28 README 多语言首批：以 `96086f6f42` 的中文 README 为内容基线，新增英文、日文完整翻译草稿与语言导航；`docs/readme-languages.json` 是版本清单，`scripts/docs/check_readme_translations.py` 核对导航、代码块、链接和关键产品事实。首次线上翻译 Action 在提供商请求阶段返回 HTTPError；备选 GitHub Models 服务已退役。用户明确不接受启用 Google Cloud 账单，故不接入官方 Cloud Translation，也不继续使用无密钥的非官方翻译接口。当前工作流只做只读一致性检查，不自动生成译文或候选 PR。译文仍需技术和语言人工复审；线上只读检查 run `40616252792` 成功。

Wave 3: implementation-paused - 2026-10-28 从 Wave 2 的 `247cefda1a` 隔离出 `feat/wave3-data-productization`，计划见 [`2026-10-28-wave3-data-productization.md`](../superpowers/plans/2026-10-28-wave3-data-productization.md)。答卷时间复合筛选、字段显示选择、任务重访恢复已有未提交实现；管理端 356 项单测、lint、typecheck、生产构建和 bundle 检查通过。初版浏览器增强在隔离 MySQL/PostgreSQL 各 19/19 及跨层核验通过。后续新增双版本和五格式真实验收在同一浏览器场景连续两次失败：首次第二版答题页时序导致超时，修正后第二次在版本筛选控件定位遇到 Playwright strict mode 歧义（13 通过、1 失败、5 未运行）。依项目“两次同类失败即停”规则停止自动修复，保留未提交分支现场；五格式下载、双库最终真实验收、GitHub CI 与 Obsidian 同步未完成，不提升 Wave 3 状态，不提交或推送。

Wave 3: local-slice-verified（2026-10-28 接续）- 用户明确要求继续后，修正浏览器版本筛选定位与导出任务断言。隔离 Docker MySQL、PostgreSQL 各 19/19，完成双版本正式答卷、跨版本列表与筛选、CSV/XLSX/SAV/DOCX/附件包五种真实下载，并通过平台 API/平台 DB/网关/引擎 DB 交叉核验；容器已清理。管理端 356/356、lint、typecheck、build、production bundle 通过。推送前核对登记仓库 `lsgoodlionel/star-survey`，PR #11 开放，父分支远端 `b8535c203f`；其最新产品质量 run `40616502706` 和只读镜像构建 run `40616502723` 成功。本 Wave 3 分支 CI 尚未触发，待本分支提交/推送后核对。取消、失败/过期及撤权下载保留既有后端/前端测试证据，本次没有双库浏览器故障注入；能力矩阵不提升，正式 Release 与生产生命周期另行验收。

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

## Wave 4 合并验证（2026-10-30）

隔离合并候选 `76e2a4e622` 基于 main `5062459f40` 与 Wave 4 `735b982581`。全新 PostgreSQL 16 容器在独立网络中完成 53 项 Flyway 迁移；`DeliveryApiTest` 14/14、`DeliveryReceiptTest` 9/9、`DeliveryReminderTest` 11/11，合计 34/34，无失败或跳过。合并树前端 40 文件 / 379 tests、typecheck、构建及 SMTP 定向单测通过。同步前登记仓库 main 为 `5062459f40`，未启用分支保护，其[质量闸门](https://github.com/lsgoodlionel/star-survey/actions/runs/40983194330)通过；本候选 CI 待推送后核验。Gmail、Outlook 真实联调及供应商回执、正式生产验收仍待后续，SMS 暂停。

## Wave 2 发布阻断诊断（2026-10-30）

隔离分支 `codex/wave2-admin-security` 基于登记仓库 main `e7020e209cd2d2d0ba3e4fddb420c7d549190cbd`；同步前 [Release run 40987973770](https://github.com/lsgoodlionel/star-survey/actions/runs/40987973770) 因 admin Trivy 失败，原生验收及 Release 组装跳过；其余两镜像扫描、签名和 attestation 成功。未发现已发布的 GitHub Release。保持既有 RC 标签不动，拟以 `0.3.2-rc.1` 作为首个真实 Release 的 bootstrap 候选，后续正式版仍需真实前版和升级证据。

本地候选让 admin runtime 刷新 Alpine 包并允许构建时临时选择镜像源，默认及回退为官方源。ARM64 实际构建成功，包数据库确认 `brotli-libs 1.2.1-r0`、`curl` / `libcurl 8.23.0-r0`；管理端生产 bundle 检查通过，镜像策略测试 6/6、Release 测试 58/58。两次运行容器内 `apk info` 均卡住，停止同类自动修复，包数据库留在 `/private/tmp/survey-wave2-admin-installed`。这些结果不代替镜像 Trivy、运行时验收和四平台原生验收；改动尚未提交或推送，Wave 2 不提升为 Accepted。

用户授权继续后，限时诊断发现 `apk info -v` 在断网时等待索引，而 `apk info --installed` 离线成功；不能据此认定容器启动失败。ARM64 镜像在环回绑定的临时容器返回 HTTP 200，`nginx -t` 通过、用户为 10001，临时容器已删除。Alpine 官方索引 12 秒未下载完，阿里云镜像约 0.17 秒完成；本机构建使用临时镜像，生产默认保留官方源。生产测试 105 通过、真实原生验收 1 项按前置条件跳过；Release 测试 58/58。同步前额外核对发现独立质量 [run 40987922827](https://github.com/lsgoodlionel/star-survey/actions/runs/40987922827) 的 P1 纵切因 close 调用次数 2 与预期 1 不符而失败，同一 Release 内复用质量门成功；独立失败不能视为通过。候选的 CI、Trivy 及正式验收按实际结果另记。

Trivy 0.74.0 对本机 ARM64 构建镜像 `sha256:02fdee230604129442feda2ae3413d6709f1531af1e910a63519b002b2189b86` 执行 HIGH/CRITICAL、包含未修复项且无例外的扫描，结果 0 项；报告 `/private/tmp/survey-wave2-admin-trivy.json`。扫描器二进制按官方 SHA-256 校验，数据库临时使用与官方 digest 一致的镜像。此结果只覆盖本机镜像，不代替线上多架构不可变 digest 扫描。

PR #18 接续：原 [只读构建 40991736075](https://github.com/lsgoodlionel/star-survey/actions/runs/40991736075) 的 ARM64 `npm ci` 长时间未完成；日志不足以确认具体根因。已合入 main `60972ffe4723` 并保留双方进度记录。admin 前端构建阶段改用构建机原生架构，静态产物复制到各目标架构的 nginx 运行镜像；本机双架构 OCI 构建成功，清单含 amd64 与 arm64，production bundle 检查通过。生产测试 106 通过、1 项原生验收按前置条件跳过；GitHub 新提交的检查与正式 Release 验收仍待核对。

## Wave 3 全分支主线合并（2026-10-30，同步前）

登记仓库 `lsgoodlionel/star-survey` 的 Wave 3 远端分支为 `feat/wave3-data-productization` 与 `codex/wave3-accepted`；前者已同时包含于最新 `main` 和最终分支，合并提交 `f4858d38b1` 因此覆盖全部 Wave 3 历史。冲突解决保留主线更完整的自研 PDF/嵌入字体实现，同时纳入数据工作区、七格式、Word 统计报告、自定义 Word 模板和资源预算；新增 V951 统一并行 V935/V950 后的最终导出格式约束，不改历史迁移校验和。

本地验证：Admin Web 41 文件 / 390 tests、lint、typecheck、生产构建（2123 modules）及 production bundle 通过；全新隔离 PostgreSQL 完成 56 项迁移至 V951，平台 Java 1548/1548、195 类通过。首次 Java 全量复用了旧库并被 V111 历史校验和阻断，不计业务结果；全新库首次暴露 `report-docx` 被 V950 后置约束覆盖，V951 修复后定向 8/8 与最终全量通过。待推送 `main` 后核对 GitHub Actions；生产 GHCR/Release、Ubuntu 四节点和真实 predecessor 升级验证仍未完成。

2026-10-30 Engine 供应链修复候选：正式 run 41031418974 在提交 `271bbace6090f573baa86ce3e520b8d93c272fc1` 的 Engine Trivy 失败（309 条 HIGH/CRITICAL，均无 FixedVersion）。Debian 13 多阶段候选仍有 59 条；停止后用户再次明确授权继续。现改为 Alpine 3.24 原生 PHP 8.3/Apache 运行包，保留生产启动与 CLI 契约、非 root、静态防护。ARM64 本地构建、no-new-privileges/HTTP/健康检查、PHP/session/应用 autoload 与附件拒绝/公开资源烟测通过；Trivy v0.74.0 同策略扫描 HIGH/CRITICAL 为 0，发布测试 59/59。远端 main 推送前核对为 `271bbace`，质量 run 41031418951 与 41029509115 成功，正式 Release run 41031418974 失败。候选分支 `codex/wave2-engine-trivy` 待提交推送和 PR CI，未创建新标签，不代表正式 Release 或原生生命周期验收通过。

## 原生验收宿主就绪检查（2026-11-01，同步前）

分支 `codex/native-systemd-diagnosis` 基于登记仓库 `lsgoodlionel/star-survey` 的 main `60aea7478ae9ea9268e75b908016e3ddda9ff27b`。重新下载 [正式 run 41182166181](https://github.com/lsgoodlionel/star-survey/actions/runs/41182166181) 的日志及四节点工件，确认 24.04 AMD64、22.04 ARM64 在 install 因 systemd 检查失败（exit 3）；24.04 ARM64、22.04 AMD64 实际通过前置检查，因 publish-gateway unhealthy 安装失败（exit 4）。四节点磁盘通过，upgradeVerified=false。不能把全部节点归因于 systemd，也不能将尚未记录的 systemd 状态认定为启动中。

本次仅为宿主契约补充有界就绪检查：要求 PID 1 为 systemd，最多等待 180 秒，沿用安装器接受 running/degraded 的状态标准，随后检查 Docker daemon 和 Compose。候选包仍由 SHA-256 校验后提取并执行原有完整生命周期；不修改 v0.3.4-rc.1。新增回归测试先失败后通过；Release suite 60/60，production suite 107 项中 106 通过、1 项真实原生测试按环境条件跳过。首次 production 测试因沙盒无 Docker socket 权限失败，授权访问后通过。macOS Docker Desktop 不证明四节点原生 systemd 验收成功。

推送前该 commit 产品质量检查成功，正式 Release run 因上述原生节点失败；无 v0.3.4-rc.1 GitHub Release。服务启动失败缺少容器日志、health 输出和实际 systemd 状态，尚未关闭；PR 质量门及非 tag Release 构建待推送后记录。此次是一次有界环境修复，若原生 systemd 再次失败应停止并保留证据，不追加修复提交。


## 原生容器挂载权限修复（2026-11-01，同步前）

用户在上一轮原生运行失败后明确授权继续。PR #22 分支 `codex/native-systemd-diagnosis` 的 `8bc42deeab` 产品质量 run 41252429687、非 tag 构建 run 41252429710 均成功；原候选运行 41252436155 的四节点均通过 systemd 就绪检查（24.04 AMD64 等待约 75 秒），但两个 ARM64 节点 platform-db unhealthy、两个 AMD64 节点 publish-gateway unhealthy，install 全部 exit 4，完整生命周期未通过。

本轮已复现安装器的 Linux 文件权限缺陷：root 生成的 `0600` 原始密钥通过文件型 Compose secret 挂载后，UID 10001 网关和数据库非 root 用户不能读取；网关生成配置 `engines.json` 同样为 `0600`。Docker 官方 Compose services 文档确认文件型 secret 保留绑定文件权限，忽略 uid/gid/mode 转换。修复保留 canonical secret `0600`，仅将容器所需密钥投影到 `0700` 宿主目录内的 `0444` 副本，继续按各服务既有 secret 列表只读挂载；不投影备份加密密钥。生成的非密钥配置 `engines.json` 为 `0644`，release 父目录保持 `0700`。补充权限、内容一致、派生哈希同步、刷新及拒绝符号链接回归测试，既有 canonical 权限断言保留。

验证：新增回归先失败后通过；production 110 项中 109 通过、1 原生项按环境跳过，Release 60/60。Linux 容器内由原候选/修复后安装器生成真实配置，以 UID 10001、70 降权读取单文件挂载等价路径：修复前网关 secret、DB 角色 secret、engines.json 全部 Permission denied，修复后全部可读。Docker Desktop 的原版和修复版隔离数据库/网关均 healthy，不能把 Desktop 的宿主文件映射结果当成 Linux 权限复现；实验没有发布端口，专用容器/卷已清理。证据脚本 /private/tmp/survey-linux-permissions.py、/private/tmp/survey-permission-proof.py；未输出生成密钥。

本轮只修改安装器、对应测试、生产 README 和本进度，共 4 个文件。推送前登记远端仍为 lsgoodlionel/star-survey，目标任务分支一致，PR 未合并。最新 CI 待新提交推送后核验；不可变 v0.3.4-rc.1 的 bundled installer 含旧缺陷，不能用本次源码修复宣称该 RC 完整验收通过，不重新打包旧候选、不创建新 RC 标签。

2026-11-01 原生 RC 安装修复：`v0.3.5-rc.1` 四节点 install 失败定位为 Compose `--project-directory` 指向安装根目录，相对脚本挂载未落到版本目录。改为所选 release 目录；回归先失败后通过，production 111 项（110 passed / 1 opt-in skipped）、Release 60/60，Docker 确认错误路径及目录脚本退出 0。新 RC、完整原生生命周期和生产 Accepted 均待后续授权验收；既有标签不变。

## 访问策略 DST 测试夹具修复（2026-11-01，同步前）

PR #24 合并提交 `25dd9e186d0bad8a8dacec7f58dee0e9743b01e7` 的 [main 质量闸门 41286125561](https://github.com/lsgoodlionel/star-survey/actions/runs/41286125561) 以 failure 完成：MySQL、PostgreSQL 的 W2 均把当前时刻前一小时换算为纽约夏令时回拨的歧义本地时间，按产品契约被 `E_POLICY_LOCAL_TIME` 拒绝，其余 8 个作业成功。本次只修正 W2 测试夹具为纽约前一天中午，保留纽约时区、真实截止拒绝、UTC 换算及伪造客户端时间断言；新增春秋 DST 回归并纳入端到端入口。生产时间校验不变。

回归测试 2/2、PostgreSQL 与 MySQL 串行完整端到端均通过。首次本地并发双库共用运行时配置，MySQL 在 W2 前遇到数据库连接错误，保留日志 `/private/tmp/survey-dst-mysql.log`；PostgreSQL 结束后串行复测通过。当前 GitHub main 仍为上述合并提交，新分支 CI 尚未触发。原生四节点验收及新 RC 仍需后续授权。

## Wave 2 `v0.3.6-rc.1` 策略准备（2026-11-02，同步前）

PR #25 已合并至登记仓库 `lsgoodlionel/star-survey` 的 `main` @ `f33eeaf2a65869309563835596d63142e38aab31`；合并后质量闸门 run 41295603448 的 10/10 作业成功。当前分支 `codex/wave2-rc6-policy` 仅为下一不可变候选登记 `0.3.6-rc.1` bootstrap baseline policy，不修改产品功能、生产校验或发行门禁。同步前 Release 策略测试与 GitHub CI/CD 尚待核对；不得据此宣称四节点原生生命周期或 Wave 2 Accepted 已完成，也不得在分支合并和主线全绿前创建标签。

## Wave 6 候选发布保护（2026-11-02，待审）

候选标签现有工作流会在检查成功后自动发布 GitHub Release。已临时关闭 publication 作业并增加回归测试；候选构建及证据生成不变。后续增量加入同仓库指定 Actions 制品的前代校验，以及绑定候选与 #41 人工记录的手动晋升入口；实现和 CI 待独立复审，真实读屏、原生候选验收及发布授权尚未完成，不能视为发布准出。仅候选组装新增作业级 actions:read，人工晋升使用原发布所需的只读 Actions、包读取与 contents 写入范围。未创建标签、发布镜像或 Release。

## Wave 6 原生候选 doctor 失败证据修复（2026-11-03，待审）

候选 `v0.3.7-rc.1` 对应 GitHub run 41548675784；四平台在启动服务、健康检查和本地 CA 复制后，首次 doctor 以 exit 4 失败，尚未进入备份、升级或恢复。现有 CLI 丢失子进程失败报告，原生脚本又在生成脱敏 doctor 证据前退出，无法据此判定具体失败检查项。本修复只在失败时保留白名单结构化检查报告，原生验收在脱敏并保存后继续返回非零；不将候选标为通过。两项新增回归先失败后通过，surveyctl 75 项、原生验收 22 项通过。仍需实际候选验证并定位 doctor 根因；不得将旧候选当成通过的升级前代。

PR #51 的 CI run 41553006902 中 `DeliveryRateLimitTest` 首轮预期 2 条、实际 3 条；该测试使用真实一秒固定窗口，运行跨越秒边界时限速器按契约进入新窗口。将测试专用限速时钟固定并显式推进，保留每窗口 2 条及最终 5 条的断言，并在测试后恢复共享组件时钟。此修复不改变生产限速实现；线上新 head 的 Java 套件仍需通过后才能确认。

## Wave 6 后续 bootstrap 策略（2026-11-03，待审）

登记 `0.3.8-rc.1` 为包含 PR #51 doctor 诊断证据修复的下一测试 bootstrap；候选只允许在本策略合并且该合并提交 CI 通过后另行触发。先前 `v0.3.7-rc.1` 原生 doctor 失败，不能作为升级前代；bootstrap 不具有升级证据、不能晋升或发布。此策略不创建标签、发布镜像或 GitHub Release。

## Wave 6 候选 doctor 双阻断调查（2026-11-03，待审）

`v0.3.8-rc.1` run 41561527713 的四份脱敏 doctor 报告均显示 `migrations=blocked`、`minimal-probe=blocked`；端口、内部与运行时隔离、TLS、容器和卷均正常，未备份为 warning。引擎 `productionInit status` 的成功契约是退出码 0，可无 stdout；doctor 原先误要求输出文字。修复按退出码检查引擎且继续要求数据库 schema 精确匹配，失败时仅记录数字 schema 和状态。候选报告未包含两侧实际值，因此不能证明这已解释所有迁移阻断。

业务探针原始报告只有泛化错误，无法判断发生在哪个步骤；新增固定阶段及 HTTP 状态码白名单，禁止保存 HTTP body、地址或凭据，失败和清理异常仍阻断。当前尚未确认业务探针根因，不得将此补丁或旧候选标记为生产通过或升级前代；下一步需独立评审并取得充分的具体阶段证据，不能仅凭假设盲目宣称修复。

进一步定向复现：隔离 PostgreSQL 和真实平台 HTTP 测试确认机器初始化后探针所有者令牌可列资源、建项目和建问卷；取出平台实际规范化的问卷定义后，真实发布网关校验器报告 7 项 `E_SETTING_NOT_EXPLICIT`。doctor 的测试问卷因此存在可确定的发布阻断，原先假网关测试未覆盖；现补齐显式设置并增加调用真实网关校验器的回归。上述隔离测试未连接真实引擎，不证明线上候选的探针实际失败阶段一定是发布，也不排除其他问题；仍需审查和完整验收。

## Wave 6 后续诊断 bootstrap 策略（2026-11-03，待审）

登记 `0.3.9-rc.1` 作为包含 PR #53 引擎状态和探针定义修复的下一测试 bootstrap；前提是本策略合并及精确合并提交 CI 通过。`v0.3.7-rc.1`、`v0.3.8-rc.1` 原生验收失败，不作升级前代；bootstrap 不具备升级通过证据，不可晋升或发布。本变更不创建标签、发布镜像或 GitHub Release。

## Wave 6 引擎内部网关 HTTPS 上下文修复（2026-11-03，待审）

`v0.3.9-rc.1` run 41571133456 的四平台迁移检查均 OK，唯一 blocked 是最小业务探针 publish HTTP 502。隔离真实网关与引擎在相同 `force_ssl=on` 下复现网关到引擎内部 HTTP 被重定向至不可用 HTTPS，发布导入返回 502；仅给已解析为 `publish-gateway` 的直连来源、指定方法与 RemoteControl/插件通道路径设置 HTTPS 上下文后发布 200，非网关请求仍重定向。隔离真实平台、网关、MySQL 引擎的探针发布、作答、投影、导出和清理旅程通过，测试以本地 HTTP 反向代理模拟边缘 TLS 终止，不代替原生四平台验收。临时容器、网络和镜像标签已清理。

修改仅作用于引擎生产配置中的内部网关上下文，不关闭 force_ssl，不修改 RemoteControl 凭据或插件签名验证；其信任边界是单机 Docker 网络的服务发现与源地址，需要独立安全审查。生产 Compose 19 项、doctor 15 项、原生验收测试 22 项通过；PHP 边界测试覆盖来源、方法、路径和 DNS 无结果。仍须新的已授权候选验证，不将旧候选标为通过或前代。


## Wave 6 生产 smoke 事件链根因修复（2026-11-03，PR #59 待审）

run 41607123060 在真实作答后的 response-projection 阶段失败；清理前本轮事件 3 条均 pending，不能将错误响应表名产生的 completedResponses=0 解释为未提交。隔离当前源码平台 + PostgreSQL、生产 Alpine 引擎 + MariaDB、真实网关与原生产 Caddy 配置，使用真实 setup-probe 和原 ProductProbe，复现相同的 3 条事件未投递。直接合成事件/cron可投递，进一步定位实际 cron 在加载 Survey 的主题配置时退出：生产镜像排除 upload 内容，初始化未建立 upload/themes/survey，SurveyThemeHelper 的 opendir 在 CLI 严格错误路径失败，中继尚未执行。

单变量补建目录后，未修改的真实 ProductProbe 完整通过发布、作答、投影、导出下载和清理，3 条事件全部 delivered。正式修复在 engine-init.sh 数据库状态判断前执行 productionInit directories，以配置中的 userthemerootdir 幂等准备目录；已完成安装也先修复目录再短路，已有文件不覆盖，路径冲突或不可用则失败。实际修订 engine-init.sh 连续执行两次后，真实旅程再次通过，目录存在、事件 3/3 delivered，全部自建容器/网络清理成功；未修改上游引擎源码。该隔离实验使用本机 ARM64 Alpine 旧构建镜像（CLI 入口三文件哈希与当前源码相同）、当前源码平台 JAR/插件和初始化配置；本地 LibreSSL 显式信任当次临时 Caddy CA。它不替代 PR amd64 source smoke、签名候选或原生四平台升级/恢复验收。

永久回归实际调用未修改的 SurveyThemeHelper，确认缺目录失败、初始化后正常、重复调用保留已有文件、普通文件占位拒绝。Compose/初始化 24 项通过（新增真实 helper 用例随后单独再验证）；smoke 15 项通过。另修复诊断响应表名、30秒命令超时，以及诊断写盘 OSError 不得阻断实际 ProductProbe 的 engine close 与项目 archive；缺失证据仍不能通过 smoke。推送后需精确 head 的常规 CI、真实 amd64 smoke及独立审查通过，禁止提前标记生产或 Wave 6 accepted。未新建候选标签、发布镜像或 GitHub Release，人工读屏仍待。
