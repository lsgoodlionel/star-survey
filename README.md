[简体中文](README.md) · [English](README.en.md) · [日本語](README.ja.md)

# Survey · LimeSurvey 本土化问卷平台

Survey（仓库名 star-survey）是在 LimeSurvey 7.1.2 引擎之外建设的中文问卷管理与交付平台，面向多租户 SaaS 和私有化部署。自有代码主要位于 platform/ 与 plugins/；引擎源码保持上游版本，平台通过受控接口、发布网关和插件与引擎协作。

> 当前状态（2026-10-28）：Wave 2 的模板与品牌功能已按本地 Docker 双库真实使用口径验收；Wave 3 数据产品化正在推进。正式 GitHub Release、公网 TLS 及生产安装、升级、恢复尚未完成验收。本仓库不是可直接宣称生产就绪的稳定发行版。

## 已验证的产品流程

| 范围 | 当前进展 |
| --- | --- |
| 管理工作台 | 中文 Dashboard、项目和问卷上下文、最近工作与角色可见性已有真实浏览器验收。 |
| 模板与品牌（Wave 2） | 租户及平台模板浏览、审批、复制；主色、标题、页脚、Logo、favicon 和多语言配置；带图真实预览、正式作答及发布版本素材固定，完成本地功能验收。 |
| 发布与答卷 | 隔离预览、审批发布、公开作答和答卷回读已有贯通流程；完整数据工作区及导出任务体验属于 Wave 3。 |
| 正式部署 | 单机生产 Compose、surveyctl 和 Release 流水线已有实现与自动检查；真实 Release 和生产生命周期仍需独立验收。 |

Wave 2 的验收在隔离 Docker 栈上分别使用 MySQL 和 PostgreSQL 执行，管理端真实浏览器各 **19/19** 通过，并核对平台 API、平台数据库、发布网关和引擎数据库。此结论只覆盖计划中的本地功能切片；[机器能力映射](platform/docs/productization/capability-map.md)涵盖更广需求，仍保持部分完成状态。

[当前进度](docs/agent/PROGRESS.md) · [产品蓝图](platform/docs/productization/product-blueprint.md) · [Wave 0–6 路线图](platform/docs/productization/release-roadmap.md) · [平台技术入口](platform/README.md)

## 系统构成

~~~mermaid
flowchart LR
    A[作者浏览器] --> W[管理端 React / TypeScript]
    W --> P[平台 Spring Boot / Java 21]
    P --> D[(PostgreSQL：租户与业务数据)]
    P --> G[发布网关 Python]
    G --> E[LimeSurvey 引擎与自有插件]
    E --> M[(MariaDB / MySQL 或 PostgreSQL)]
    R[作答者浏览器] --> E
    E -->|签名事件| P
~~~

平台管理租户、权限、问卷定义、发布版本与答卷投影；网关负责与引擎的受控交互。自有插件处理引擎侧事件、作答策略及品牌资产通道。详细契约和信任边界见[平台技术文档](platform/README.md)。

## 在本机查看前端

需要 Docker Engine、Docker Compose、本机可用的 Python 和项目所需的构建工具。仓库提供独立的持久演示栈，不会使用生产配置：

~~~bash
bash platform/deploy/demo/run-admin-web-demo.sh start
bash platform/deploy/demo/run-admin-web-demo.sh status
~~~

启动后，访问地址、测试身份和访问凭据写入本机的 platform/deploy/demo/.runtime/access.json。该目录和文件分别限制为 0700、0600，且不会提交到 Git。演示管理端使用“测试令牌”登录：在 access.json 所列 adminWebUrl 后打开 /dev/token，填入 platformAccessToken。引擎管理后台的单独账号见同一文件。演示服务只绑定 127.0.0.1；端口由 Docker 动态分配，以文件中的实际地址为准。

~~~bash
# 令牌过期后刷新；停止服务时保留演示数据
bash platform/deploy/demo/run-admin-web-demo.sh refresh-token
bash platform/deploy/demo/run-admin-web-demo.sh stop
~~~

测试令牌入口仅用于本地演示和端到端测试。生产入口及部署要求见[生产运行手册](platform/deploy/production/README.md)。

## 开发与质量检查

管理端使用 Node.js 22；平台使用 Java 21。常用命令从仓库根目录执行：

~~~bash
# 管理端静态检查、测试与构建
cd platform/apps/admin-web
npm ci
npm run lint
npm run typecheck
npm test -- --run
npm run build
cd ../../..

# 发布网关单元测试
cd platform/tools/publish-gateway
python3 -m unittest discover -s tests -t . -q
cd ../../..

# 平台 Java 全套测试
platform/deploy/test/run-platform-tests.sh
~~~

涉及数据库的真实流程按 MySQL 与 PostgreSQL 分别验收，例如管理端纵向流程：

~~~bash
TEST_DB=mysql SURVEY_TEST_PREFIX=readme-mysql COMPOSE_PROJECT_NAME=readme-mysql \
  bash platform/deploy/test/run-admin-web-e2e.sh --fresh
TEST_DB=pgsql SURVEY_TEST_PREFIX=readme-pgsql COMPOSE_PROJECT_NAME=readme-pgsql \
  bash platform/deploy/test/run-admin-web-e2e.sh --fresh
~~~

[GitHub 产品质量工作流](.github/workflows/platform-quality.yml)检查前端、Java、网关、真实浏览器、访问策略及双库流程。Wave 2 收尾提交的[产品质量运行](https://github.com/lsgoodlionel/star-survey/actions/runs/40612142008)为 **10/10 成功**；[Release 构建运行](https://github.com/lsgoodlionel/star-survey/actions/runs/40612142061)完成 admin、platform 和 publish-gateway 的 AMD64/ARM64 只读镜像构建。

[Release 工作流](.github/workflows/release.yml)在候选版标签触发后才执行镜像发布、签名、制品生成和原生主机验收。PR 的成功构建不等于已经创建正式 Release。当前生产验收边界与操作命令以[生产运行手册](platform/deploy/production/README.md)为准。

## 文档与项目边界

- [前后端需求与验收断言](platform/docs/productization/frontend-backend-requirements.md)
- [产品蓝图](platform/docs/productization/product-blueprint.md)
- [发布路线图](platform/docs/productization/release-roadmap.md)
- [能力状态与证据](platform/docs/productization/capability-map.md)
- [Wave 2 收尾与后续进度](docs/agent/PROGRESS.md)

本仓库基于 [LimeSurvey](https://www.limesurvey.org/) 开发；其原有项目、商标和素材归相应权利人所有。代码许可证见 [LICENSE](LICENSE)（GPL-2.0-or-later）。本项目的状态与功能以仓库代码、能力映射和对应验收证据为准。
