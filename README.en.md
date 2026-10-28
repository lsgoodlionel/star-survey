<!-- Translation draft based on README.md at 96086f6f42cfaa31530cb658e47931eef0ca47f8; technical and language review required. -->
[简体中文](README.md) · [English](README.en.md) · [日本語](README.ja.md)

# Survey · A localized LimeSurvey platform

Survey (repository name: star-survey) is a Chinese-language survey management and delivery platform built around the LimeSurvey 7.1.2 engine. It targets multi-tenant SaaS and private deployments. Most project-owned code lives in `platform/` and `plugins/`. The engine source remains at its upstream version; the platform works with it through controlled interfaces, a publishing gateway, and plugins.

> Status as of 2026-10-28: Wave 2 template and branding features passed local functional acceptance using Docker with both supported database configurations. Wave 3 data product work is underway. An official GitHub Release, public TLS, and production installation, upgrade, and recovery have not yet passed acceptance. This repository is not a stable release ready to be called production-ready.

## Verified product flows

| Area | Current progress |
| --- | --- |
| Admin workspace | The Chinese-language dashboard, project and survey context, recent work, and role-based visibility have passed real-browser acceptance. |
| Templates and branding (Wave 2) | Tenant and platform template browsing, approval, and copying; primary color, title, footer, logo, favicon, and multilingual settings; visual preview, live response collection, and fixed assets in published versions passed local functional acceptance. |
| Publishing and responses | Isolated preview, approval-based publishing, public responses, and response readback form an end-to-end flow. The full data workspace and export task experience remain in Wave 3. |
| Production deployment | Single-host production Compose, `surveyctl`, and the Release pipeline have implementations and automated checks. An actual Release and the production lifecycle still need separate acceptance. |

Wave 2 acceptance ran on isolated Docker stacks with MySQL and PostgreSQL. The admin interface passed **19/19** real-browser checks for each database, with checks across the platform API, platform database, publishing gateway, and engine database. This result covers only the planned local feature slice. The broader [machine-readable capability map](platform/docs/productization/capability-map.md) still marks some requirements as incomplete.

[Current progress](docs/agent/PROGRESS.md) · [Product blueprint](platform/docs/productization/product-blueprint.md) · [Wave 0–6 roadmap](platform/docs/productization/release-roadmap.md) · [Platform technical entry point](platform/README.md)

## System components

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

The platform manages tenants, permissions, survey definitions, published versions, and response projections. The gateway controls interactions with the engine. Project-owned plugins handle engine events, response policies, and branding assets. See the [platform technical documentation](platform/README.md) for contracts and trust boundaries.

## View the frontend locally

You need Docker Engine, Docker Compose, a working local Python installation, and the project's build tools. The repository provides a separate persistent demo stack that does not use production configuration:

~~~bash
bash platform/deploy/demo/run-admin-web-demo.sh start
bash platform/deploy/demo/run-admin-web-demo.sh status
~~~

After startup, local access addresses, test identities, and credentials are written to `platform/deploy/demo/.runtime/access.json`. The directory and file are restricted to modes 0700 and 0600 respectively and are not committed to Git. The demo admin interface uses a test-token login: open `/dev/token` after the `adminWebUrl` listed in `access.json`, then enter `platformAccessToken`. The engine admin account is listed separately in the same file. Demo services bind only to `127.0.0.1`; Docker allocates ports dynamically, so use the addresses in the file.

~~~bash
# 令牌过期后刷新；停止服务时保留演示数据
bash platform/deploy/demo/run-admin-web-demo.sh refresh-token
bash platform/deploy/demo/run-admin-web-demo.sh stop
~~~

The test-token entry point is for local demos and end-to-end tests only. See the [production operations guide](platform/deploy/production/README.md) for production entry points and deployment requirements.

## Development and quality checks

The admin interface uses Node.js 22; the platform uses Java 21. Run these common commands from the repository root:

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

Database-backed user flows are accepted separately with MySQL and PostgreSQL. For example, to run the admin end-to-end flow:

~~~bash
TEST_DB=mysql SURVEY_TEST_PREFIX=readme-mysql COMPOSE_PROJECT_NAME=readme-mysql \
  bash platform/deploy/test/run-admin-web-e2e.sh --fresh
TEST_DB=pgsql SURVEY_TEST_PREFIX=readme-pgsql COMPOSE_PROJECT_NAME=readme-pgsql \
  bash platform/deploy/test/run-admin-web-e2e.sh --fresh
~~~

The [GitHub product quality workflow](.github/workflows/platform-quality.yml) checks the frontend, Java platform, publishing gateway, real browser, access policies, and both database configurations. The [product quality run](https://github.com/lsgoodlionel/star-survey/actions/runs/40612142008) for the Wave 2 closing commit passed **10/10** checks. The [Release build run](https://github.com/lsgoodlionel/star-survey/actions/runs/40612142061) completed read-only AMD64/ARM64 image builds for admin, platform, and publish-gateway.

The [Release workflow](.github/workflows/release.yml) publishes images, signs artifacts, creates deliverables, and runs native-host acceptance only after a candidate tag triggers it. A successful PR build does not mean an official Release exists. The current production acceptance boundary and commands are in the [production operations guide](platform/deploy/production/README.md).

## Documentation and project boundaries

- [Frontend/backend requirements and acceptance assertions](platform/docs/productization/frontend-backend-requirements.md)
- [Product blueprint](platform/docs/productization/product-blueprint.md)
- [Release roadmap](platform/docs/productization/release-roadmap.md)
- [Capability status and evidence](platform/docs/productization/capability-map.md)
- [Wave 2 closing status and next steps](docs/agent/PROGRESS.md)

This repository builds on [LimeSurvey](https://www.limesurvey.org/). Its original project, trademarks, and assets belong to their respective rights holders. See [LICENSE](LICENSE) for the code license (GPL-2.0-or-later). Treat repository code, the capability map, and the corresponding acceptance evidence as the source of truth for this project's status and features.
