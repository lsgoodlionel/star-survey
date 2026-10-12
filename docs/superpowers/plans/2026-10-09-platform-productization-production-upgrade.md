# Platform Productization And Production Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已完成的后端能力转化为可发现、可操作、可验收的前端产品，并交付 Ubuntu 22.04/24.04、AMD64/ARM64 可一键安装、升级、回滚、备份、恢复和卸载的单机生产版。

**Architecture:** 以「需求证据基线 -> 生产运行基座 -> 发布后闭环 -> 多架构 Release」为首批交付链。现有 Platform Service 继续作为业务事实来源，Admin Web 消费已有投放/答卷/导出契约，仅为隔离真实预览增加最小后端契约。Production Compose 用内部网络组合前端、平台、网关、LimeSurvey、PostgreSQL 和 MariaDB，只由 Caddy 暴露 80/443。

**Tech Stack:** Java 21、Spring Boot 4.1、PostgreSQL 16、React 19、TypeScript 5.9、TanStack Query 5、Vitest 5、Playwright 1.64、Python 3.12、Docker Compose v2、Caddy 2、GitHub Actions、GHCR、Cosign。

**Spec:** `docs/superpowers/specs/2026-10-09-platform-productization-production-upgrade-design.md`

## Global Constraints

- 本阶段冻结新后端业务包；只允许前端契约、生产安全和部署必需的后端变更。
- 一项能力只有在需求、后端、前端、真实流程、生产和追溯六层证据齐全时才能标记产品完成。
- 业务用户不进入 LimeSurvey `/admin`；`/engine-admin/*` 仅供受限运维访问。
- 不把正式答卷链接当作隔离预览，不使用静态假数据、占位页或测试令牌通过生产验收。
- 只允许 Caddy 绑定宿主机 80/443；数据库、Platform、Gateway 和 LimeSurvey 不发布宿主机端口。
- 凭据不进 Git、文档、Release 资产或命令输出；安装生成的私密文件必须为 `0600`。
- 任何升级前先备份，回滚不可假设数据库迁移可逆，要求 release manifest 声明数据兼容范围。
- 支持 Ubuntu 22.04/24.04 与 `linux/amd64`、`linux/arm64`；不能只用 QEMU 构建成功代替原生架构运行验证。

## Review Focus

- 计划实施后，线上产品界面应该清晰回答「发布后去哪里、怎么投放、怎么看数据」，不再只展示版本号。
- 隔离预览必须用独立引擎 SID/世代，预览答卷不进入正式投影、统计和导出。
- `surveyctl doctor` 必须检查端口、TLS、容器健康、内网暴露、迁移、持久卷、备份可读性和最小业务探针。
- Release 必须通过 digest 固定所有镜像，并同时提供 checksum、签名/证明、SBOM 和 release manifest。
- 进度文档的 Accepted 数只能由验收证据提升，不能因代码文件存在而自动增加。

---

## Child Plans And Order

1. `2026-10-09-productization-requirements-baseline.md`：先建立机器可校验的能力映射和文档单一事实来源。
2. `2026-10-09-single-host-production.md`：建立完整 Production Compose 与 `surveyctl`，是真实 E2E 和 Release 的运行基座。
3. `2026-10-09-publish-aftercare-product-flow.md`：将已有投放/答卷/导出 API 接入前端，并增加隔离预览契约。
4. `2026-10-09-multiarch-github-release.md`：在生产栈和最小业务闭环通过后构建多架构镜像与 GitHub Release。

## Parallel Execution

- Agent A 执行需求基线，只修改文档和校验工具。
- Agent B 执行单机生产方案，主要修改 `platform/deploy/production/` 和镜像构建文件。
- Agent C 执行发布后闭环，主要修改 Admin Web 和 preview 后端契约。
- Agent D 可在 Production Compose 文件名、镜像名和 manifest schema 锁定后开始 Release 流水线。
- 合并顺序固定为 A -> B -> C -> D；每次合并后重跑对应子计划验证，最后执行生产安装与真实浏览器全链路。

## Final Acceptance

- [ ] 从干净 Ubuntu 22.04 AMD64、22.04 ARM64、24.04 AMD64、24.04 ARM64 安装同一候选版。
- [ ] 使用组织登录或生产管理员引导创建租户，不使用 `/dev/token`。
- [ ] 完成创建问卷、编辑、隔离真实预览、审批、发布、获取二维码、正式作答、查看答卷和下载导出。
- [ ] 升级到下一候选版，核对问卷、链接、答卷、资产和导出不丢失，并演练回滚边界。
- [ ] 从备份在新目录恢复全栈，重跑最小业务旅程。
- [ ] 卸载默认保留数据；仅在显式参数和二次确认后删除数据卷。
- [ ] 同步 `platform/README.md`、进度板、追溯文档、外部蓝图、GitHub PR/Release 和 Obsidian 项目记录。
