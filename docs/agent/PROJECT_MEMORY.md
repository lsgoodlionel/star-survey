<!-- harness-delivery-status: not_started -->
# Survey 永久项目记忆

稳定事实核对日期：2026-10-10。来源是当前源码、现有 CI 与批准设计；本文不保存运行状态。

## 身份与权威入口

- 主产品仓库：GitHub lsgoodlionel/star-survey，登记 remote origin；上游 fork 为 lsgoodlionel/LimeSurvey，remote limesurvey-fork。
- 本地项目根路径 /Users/lionel/Develop/survey；独立 worktree 位于 .worktrees/ 下。
- 仓库身份与推送边界须每次核对 ~/.codex/memories/claude-code-migration/github-repositories.md 和 git remote -v。
- 产品与工程入口：[platform/README.md](../../platform/README.md)；产品进度：[progress.md](../../platform/docs/p2/progress.md)。
- 需求：[前后端需求](../../platform/docs/productization/frontend-backend-requirements.md)、
  [产品蓝图](../../platform/docs/productization/product-blueprint.md)、[发布路线图](../../platform/docs/productization/release-roadmap.md)。
- 能力状态由 [capabilities.json](../../platform/docs/productization/capabilities.json) 与生成器决定，人工文档不能提升状态。
- 设计与计划的唯一权威目录为 docs/superpowers/specs/、docs/superpowers/plans/；任务报告 .superpowers/sdd/ 是证据索引。
- 当前产品实施计划：`docs/superpowers/plans/2026-10-10-business-dashboard-v1.md`
  （[文档链接](../superpowers/plans/2026-10-10-business-dashboard-v1.md)）；
  安装、使用、恢复与分层边界见 [Harness 指南](../../tools/agent-harness/README.md)。
- Obsidian 同步入口为对应 Survey 项目的“项目总览”或“进度与路线图”；定位实际笔记后再写，不能猜测路径。

## 源码与技术栈

- 根 application/、assets/ 等为 LimeSurvey 7.1.2 上游引擎（PHP/Yii）；不修改引擎源码与根 README.md。
- 自有插件：plugins/Mjy*/；单仓库边界依据 [ADR 0004](../../platform/docs/adr/0004-single-repository.md)。
- 管理端：platform/apps/admin-web/，React 19、TypeScript、Vite、Vitest、Playwright；Node 22，具体锁定版本以 package.json/lock 为准。
- 平台：platform/services/business/，Java 21、Spring Boot 4.1.1、PostgreSQL、Flyway；认证与租户隔离是高风险边界。
- 发布网关：platform/tools/publish-gateway/，Python；三端契约涉及 Java 平台、Python 网关与 PHP 插件。
- 本地 Docker 入口 docker-compose.dev.yml、platform/deploy/test/ 与 platform/deploy/platform-dev/。
- 生产与 Release 入口 platform/deploy/production/，运行手册与环境和本地开发明确分离。
- 控制平面 tools/agent-harness/ 使用 Python 3.11+ 标准库，配置不依赖 PyYAML；确定性 CLI、状态恢复、Gate、
  脱敏诊断、受控 Codex adapter、fake drills 与只读治理 CI 已在本地实现和验证。核心套件、项目策略模板和宿主集成
  保持分层；独立审查、GitHub 与 Obsidian 同步状态以 [HARNESS_DELIVERY.json](HARNESS_DELIVERY.json) 为准。

## 现有质量门

命令事实来自 [.github/workflows/platform-quality.yml](../../.github/workflows/platform-quality.yml)、
[管理端 package.json](../../platform/apps/admin-web/package.json) 和现有脚本。可执行参数数组见 [Gate 矩阵](GATE_MATRIX.yaml)。

- 管理端目录：npm run lint、npm run typecheck、npm test -- --run、npm run build、npm run assert:production-bundle。
- 平台 Java：仓库根执行 platform/deploy/test/run-platform-tests.sh，包含实际测试与报告对账；须 Java 21 和正确数据库角色。
- 网关目录：python3 -m unittest discover -s tests -t . -q；包含三端注册表与上限一致性检查。
- 产品化：python3 -m unittest discover -s platform/tools/productization -p 'test_render_capabilities.py'；
  python3 platform/tools/productization/render_capabilities.py --check。
- 生产与 Release：分别 discover platform/deploy/production/tests 和 platform/deploy/production/release/tests，pattern test_*.py；
  Release workflow 解析测试使用现有 CI 锁定的 PyYAML，Harness 自身不引入该依赖。
- 需求追溯目录 platform/tools/traceability：python3 -m unittest discover -s tests -t . -q 与 python3 -m reqtrace.cli check。
- 浏览器纵向：platform/deploy/test/run-admin-web-e2e.sh --fresh；三进程 P1：platform/deploy/test/run-p1-e2e.sh。
- 引擎与策略端到端脚本位于 platform/deploy/test/；数据库相关场景按现有约定双库执行。
- Harness：仓库根 `scripts/agent-harness --python -m unittest discover -s tools/agent-harness/tests -p 'test_*.py' -v`；
  fake drills 使用 `scripts/agent-harness --python tools/agent-harness/drills/run_drills.py`。

## 已确认边界与维护

- 2026-10-10 决策：规则、状态、证据外置，复用现有 CI、Git 与 Docker；不建平行管理平台。
- 2026-10-11 决策：控制平面核心由独立仓库 `lsgoodlionel/Autonomous-Engineering-Control` 维护，Survey 只保留
  宿主策略、运行状态和通过 ownership manifest 安装的 runtime；升级不得覆盖宿主配置与产品文档。
- 同一指纹 3 次、每 Milestone 5 个失败修复周期、连续 2 轮无进展暂停；不能绕过审批和沙盒。
- `.github/workflows/agent-governance.yml` 只有只读权限，固定 action commit，验证配置、Harness、drills、危险参数
  与计划/记忆引用；不负责合并、发布或部署。
- 秘密、生产/发布、迁移/认证、生成文件与普通审查路径由 [保护契约](PROTECTED_PATHS.yaml) 分类。
- var/agent-harness/runs/ 原始状态不提交；docs/agent/run-history/ 仅脱敏摘要。
- 正常工作使用任务分支与独立 worktree，长期分支 main 经 PR 合并；本任务授权可明确禁止 push。
- 交付记录包含日期、分支、提交/PR、测试证据、完成项、遗留与下一步；并行实现不改共享进度板，由集成方更新。
- 不将历史测试数量、当前分支、HEAD、端口、容器、部署、远端可达性或临时错误存为永久事实。
- 所有可变事实必须在新会话重验；技术栈升级后按源码更新本文并注明日期，不凭迁移记忆判断服务已运行。
