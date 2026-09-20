# MJY 平台（LimeSurvey 本土化）

本目录存放本土化商业平台在引擎仓库内的全部自有内容。引擎源码保持与上游一致，自研插件放在 `plugins/Mjy*`（引擎插件加载机制要求放在该目录）。

总方案见 `/Users/lionel/Documents/MJY 2/LimeSurvey本土化方案/04-开发方案总蓝图（仓库校准版）.md`；P0 进度见 [docs/p0/progress.md](docs/p0/progress.md)。

## 目录

| 路径 | 内容 |
|---|---|
| `platform/deploy/dev/` | 开发镜像（PHP 8.3＋Apache，含 MySQL/PostgreSQL 驱动与 Xdebug） |
| `platform/deploy/test/` | 与 CI 一致的测试配置与运行脚本 |
| `platform/docs/adr/` | 架构决策记录 |
| `platform/docs/p0/` | P0 技术验证进度与证据 |
| `platform/phpunit.xml` | 平台自有测试套件 |
| `plugins/MjyPlatformBridge/` | 答卷生命周期事件日志与补偿扫描（P0-00.4 原型） |
| `platform/tests/e2e/` | 端到端测试脚本 |
| `platform/tests/fixtures/plugins/FaultInjector/` | 仅测试用的故障注入插件，只挂载进测试容器 |

## 常用命令（在仓库根目录执行）

```bash
# 开发环境：http://localhost:8090 ，DB 127.0.0.1:3307
docker compose -f docker-compose.dev.yml up -d

# 引擎 unit 套件（隔离测试库，root/root，admin/password，debug=0）
platform/deploy/test/run-tests.sh --fresh

# 平台自有测试
platform/deploy/test/run-tests.sh -c platform/phpunit.xml

# 端到端故障注入（真实 HTTP 填写＋SIGKILL），加 TEST_DB=pgsql 切换数据库
platform/deploy/test/run-fault-injection.sh

# 代码风格（仓库规则集）
docker exec survey-web vendor/bin/phpcs --standard=phpcs.ruleset.xml plugins/MjyPlatformBridge
```

## 仓库与分支

- 代码仓库：`lsgoodlionel/star-survey`（remote `origin`），唯一工作分支 `main`。
- 上游 fork `lsgoodlionel/LimeSurvey` 保留为 remote `limesurvey-fork`，用于对比上游与合并升级。
- `main` 的根提交是 LimeSurvey 7.1.2 快照（上游 `4c20c680`）：本地为浅克隆，无法推送浅历史，故以单个根提交导入；上游完整历史仍在 fork 仓库。
