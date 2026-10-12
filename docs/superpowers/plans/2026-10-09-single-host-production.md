# Single-host Production Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付一套无开发入口、只暴露 80/443、可健康检查、可备份恢复和可安全升级的单机 Docker Compose 生产栈。

**Architecture:** Caddy 统一 TLS 和路由，Admin Web、Platform Service、Publish Gateway、LimeSurvey、PostgreSQL、MariaDB 全部位于内部网络。`surveyctl` 是唯一运维入口，消费带 digest 的 release manifest，管理预检、生成私密配置、启动、升级、回滚、备份、恢复、诊断和卸载。

**Tech Stack:** Docker Compose v2、Caddy 2、Ubuntu 22.04/24.04、Bash、Python 3.12、unittest、ShellCheck、Trivy。

**Spec:** `docs/superpowers/specs/2026-10-09-platform-productization-production-upgrade-design.md`

## Global Constraints

- `platform/deploy/private/` 是历史 LimeSurvey 私有切片，不原地改造为全栈生产方案。
- Compose 不含默认密码，不使用 `latest`，镜像必须支持 digest 锁定。
- 安装、升级和卸载不打印密钥或完整凭据；错误日志允许输出配置键名，不允许输出值。
- 备份必须包含 PostgreSQL、MariaDB、LimeSurvey upload/plugin data、Platform assets/exports、Gateway state 和安装元数据，并生成 checksum 与容量清单。
- `uninstall` 默认不删数据；`--purge-data` 必须要求明确确认字符串。

## Review Focus

- 确认 `docker compose config` 中只有 Caddy 出现 `ports`，而不是依赖 UFW 阻止 Docker 绕过。
- `/engine-admin/*` 不得和业务身份共用认证；未配置运维访问策略时拒绝启动生产入口。
- 备份恢复验收必须在新项目名和新持久卷上进行，不能对原数据就地覆盖后宣称成功。

---

### Task 1: Production images and immutable runtime

**Files:**
- Modify: `platform/apps/admin-web/Dockerfile`
- Create: `platform/services/business/Dockerfile`
- Modify: `platform/tools/publish-gateway/Dockerfile`
- Create: `platform/deploy/production/tests/test_images.py`

- [ ] **Step 1: Write failing image policy tests**

检查非 root runtime、健康检查、OCI labels、不含测试 bundle、构建参数 `VERSION/REVISION/CREATED`、可写目录最小化。

- [ ] **Step 2: Verify failure**

Run: `python3 -m unittest discover -s platform/deploy/production/tests -p 'test_images.py'`

- [ ] **Step 3: Implement multi-stage production images**

Business 镜像使用 Maven/JDK 21 构建、JRE 21 运行；Admin Web 保留生产 bundle 断言；Gateway 固定 Python patch 基础镜像并保持 UID 10001。

- [ ] **Step 4: Build both platforms and run container health probes**

Run:

```bash
docker buildx build --platform linux/amd64,linux/arm64 --build-arg VERSION=0.1.0-test --build-arg REVISION=$(git rev-parse HEAD) --output=type=cacheonly platform/apps/admin-web
docker buildx build --platform linux/amd64,linux/arm64 --build-arg VERSION=0.1.0-test --build-arg REVISION=$(git rev-parse HEAD) --output=type=cacheonly platform/services/business
docker buildx build --platform linux/amd64,linux/arm64 --build-arg VERSION=0.1.0-test --build-arg REVISION=$(git rev-parse HEAD) --output=type=cacheonly platform/tools/publish-gateway
```

Expected: all three images complete both platform builds; native loaded images become healthy without running as root.

- [ ] **Step 5: Commit**

```bash
git add platform/apps/admin-web/Dockerfile platform/services/business/Dockerfile platform/tools/publish-gateway/Dockerfile platform/deploy/production/tests
git commit -m "build: add production runtime images"
```

### Task 2: Production Compose and edge policy

**Files:**
- Create: `platform/deploy/production/compose.yml`
- Create: `platform/deploy/production/Caddyfile`
- Create: `platform/deploy/production/env.example`
- Create: `platform/deploy/production/engines.example.json`
- Create: `platform/deploy/production/tests/test_compose.py`

**Interfaces:** services `edge/admin-web/platform/publish-gateway/engine/platform-db/engine-db`; networks `edge/internal`; named volumes separated by data class.

- [ ] **Step 1: Write failing rendered-compose tests**

断言只有 edge 发布 80/443，数据库无 host port，所有服务有 healthcheck/restart policy/resource limits，不存在默认密码或 `latest`。

- [ ] **Step 2: Implement Compose and Caddy routes**

`/v1*` -> Platform，`/d/*` 与 `/a/*` -> Platform，`/survey/*` -> LimeSurvey runtime，`/engine-admin/*` -> 受限 LimeSurvey admin，其余 -> Admin Web。启用 HSTS、CSP、frame policy、body limits 和代理头白名单。

- [ ] **Step 3: Add first-boot engine initialization**

使用显式 one-shot init service 安装/升级 LimeSurvey 数据库、平台插件和 `force_ssl=on`/`ssl_disable_alert=true`；再次执行要幂等。

- [ ] **Step 4: Verify config and runtime**

Run: `python3 -m unittest discover -s platform/deploy/production/tests -p 'test_compose.py' && docker compose --env-file platform/deploy/production/tests/fixtures/test.env -f platform/deploy/production/compose.yml config`

- [ ] **Step 5: Commit**

```bash
git add platform/deploy/production
git commit -m "feat: add single-host production compose"
```

### Task 3: surveyctl lifecycle

**Files:**
- Create: `platform/deploy/production/surveyctl`
- Create: `platform/deploy/production/surveyctl.py`
- Create: `platform/deploy/production/release.schema.json`
- Create: `platform/deploy/production/tests/test_surveyctl.py`
- Create: `platform/deploy/production/tests/fixtures/release.json`

**Interfaces:** `surveyctl install|upgrade|rollback|backup|restore|status|doctor|logs|uninstall`; exit codes `0` success, `2` input/config, `3` environment, `4` runtime health, `5` integrity.

- [ ] **Step 1: Write failing command-contract tests**

用 fake Docker/GitHub clients 固定命令顺序、密钥权限、digest 校验、升级前备份、失败恢复版本指针和 purge 二次确认。

- [ ] **Step 2: Implement manifest parsing and host preflight**

校验 OS/arch、Docker/Compose、磁盘、内存、端口、DNS/TLS 条件和 release 校验和；仅在目标目录写入状态。

- [ ] **Step 3: Implement lifecycle commands**

`install` 生成密钥并启动；`upgrade` 备份、拉取 digest、迁移、探针、切换版本指针；`rollback` 只在 manifest 允许的 DB 兼容范围内执行。

- [ ] **Step 4: Verify unit tests and command help**

Run: `python3 -m unittest discover -s platform/deploy/production/tests && platform/deploy/production/surveyctl --help`

- [ ] **Step 5: Commit**

```bash
git add platform/deploy/production
git commit -m "feat: add surveyctl production lifecycle"
```

### Task 4: Backup, restore, doctor and clean-host E2E

**Files:**
- Create: `platform/deploy/production/backup.py`
- Create: `platform/deploy/production/restore.py`
- Create: `platform/deploy/production/doctor.py`
- Create: `platform/deploy/production/tests/test_backup_restore.py`
- Create: `platform/deploy/production/tests/test_install_upgrade.py`
- Create: `platform/deploy/production/README.md`
- Modify: `platform/README.md`

- [ ] **Step 1: Write failing backup/restore and fault-injection tests**

覆盖截断备份、checksum 不符、磁盘空间不足、容器不健康、迁移失败和旧版数据不兼容。

- [ ] **Step 2: Implement consistent backup and restore-to-new-stack**

备份目录含 `manifest.json`、SHA256SUMS、两个 DB dump 和卷归档；恢复先在新 Compose project 验证再允许切换。

- [ ] **Step 3: Implement doctor and minimal product probe**

区分警告/阻断，输出可脱敏的 JSON 摘要；探针不创建永久测试用户或泄漏凭据。

- [ ] **Step 4: Run native clean-host acceptance on all supported OS/arch runners**

执行 install -> doctor -> minimal journey -> backup -> upgrade -> doctor -> restore into new project -> uninstall。

- [ ] **Step 5: Update docs and commit**

```bash
git add platform/deploy/production platform/README.md
git commit -m "feat: verify production backup upgrade and recovery"
```
