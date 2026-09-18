# ADR 0001：引擎基线与测试基线

- 状态：部分决定（上游差异与 SBOM 待补）
- 日期：2026-09-18
- 关联：P0-00.2；总方案 §2、§11

## 背景

引擎来自 fork `lsgoodlionel/LimeSurvey`，`master` @ `4c20c68033c8e37140f26af80a65f659b40f8a45`，版本 7.1.2、DB 版本 714。本地为浅克隆（网络条件下无法获取完整历史），未配置 upstream remote。

## 已验证事实

| 项 | 结果 |
|---|---|
| PHP | 8.3.33 下 `composer install` 与 CLI 安装成功；composer 平台锁仍为 8.1.29 |
| 必需扩展 | 在 CI 列表之外还需要 `calendar`（`khaled.alshamaa/ar-php` 依赖） |
| 数据库 | MariaDB 10.11.19 安装成功，59 张表 |
| unit 套件（开发配置） | 850 用例，79 错误／12 失败：`admin/admin123`、`debug=2`、普通库账号导致 |
| unit 套件（CI 对齐，MariaDB 10.11） | **848 用例，19,623 断言，0 错误，0 失败**；1 警告（上游 `ThemeOptionsControllerTest` 类中没有用例）；1 无断言用例（上游 `AttributesServiceTest`） |
| unit 套件（CI 对齐，PostgreSQL 16） | **840 用例，2,517 断言，0 错误，0 失败**；与 CI 一样排除 `@group mysql`（`UpdateDbHelperTest`、`CheckDatabaseJsonValuesTest`）。断言数差距来自被排除的逐版本升级测试 |
| 平台插件测试 | `MjyPlatformBridge` 9/9 在 MariaDB 与 PostgreSQL 上均通过 |

CI 对齐配置：
- MariaDB：`platform/deploy/test/config.mysql.php`＝`config-sample-mysql.php` 仅改 DB 主机，并按 `functional.yml` 设置 `editorEnabled=false`；root/root。
- PostgreSQL：`platform/deploy/test/config.pgsql.php`＝`config-sample-pgsql.php` 仅改主机与密码（postgres/postgres），与 `main.yml` 一致。CI 用 PG 14，本项目按总方案用 PG 16。
- 超级管理员 `admin/password`；数据库均为 tmpfs；独立 `tmp` 卷，每次运行清空 schema 缓存，不与开发库共享。

## 决定

1. 开发与测试分离：开发库（`survey-db`）与测试库（`survey-test-db`）互不影响；引擎回归一律用 `platform/deploy/test/run-tests.sh`。
2. **引擎变更门禁**：任何插件、主题或核心补丁合入前，unit 套件必须保持 0 错误 0 失败。
3. 生产 PHP 定为 8.3（MariaDB 与 PostgreSQL 16 的 unit 套件均通过），composer 平台锁保持 8.1.29 不变；功能套件回归后复核。
4. 用户决定不使用代理、仍以当前仓库为唯一仓库（见 ADR 0004）；upstream 差异比对改为在网络条件允许时执行，不阻塞 P0 其他任务。
5. PostgreSQL 16 保持为生产首选数据库。**风险**：PG 上的数据库升级路径（`UpdateDbHelper`）没有自动化测试，引擎升级前须在 PG 影子环境做升级演练。

## 待办

- [ ] functional 套件（需 Selenium）选择性回归。
- [ ] `git fetch --unshallow` 并添加 upstream，输出 fork 差异。
- [ ] SBOM 与许可清单。
