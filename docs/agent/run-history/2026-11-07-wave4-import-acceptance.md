# Wave 4 联系人导入与完成后刷新验收

日期：2026-11-07（以下运行时间均为 UTC）。状态：隔离源码栈本地双库通过；候选正式身份与生产验收仍独立待核验。

## 版本与范围

- 仓库：`lsgoodlionel/star-survey`；独立分支：`codex/wave4-import-acceptance`。
- 基线：`46766fd4326c370e0a5eeaa8de50235b5abb4a16`。
- 本次实际测试的产品及测试代码提交：`186fee5dd8c1273e98fa315b5269f99cef72c0f1`。后续本记录与追溯登记只改文档。
- 关联需求：R18-01；计划：`docs/superpowers/plans/2026-10-28-wave4-audience-delivery.md`，SHA-256 `d77a0491aebd4d44818a3c9be0cec0e426166fd826f9edd7f8dbd8f444a5300d`。
- 环境：独立 Mac Docker 测试项目、随机 loopback 端口、虚构联系人与短期测试身份。未复用现有产品实例或真实供应商配置。

## 发现与最小修复

失败先行回归复现：后台导入已完成，页面显示完成状态和逐行结果，但联系人列表仍停留在导入前；等待已完成状态后找不到新增联系人。该次定向测试退出 1，旧有测试通过，新回归失败。

`DeliveryWorkspacePage` 现在在导入完成后失效该任务所属租户、名单的联系人查询。即使用户已切换名单，也不会误刷新当前另一个名单。新增两项回归覆盖完成后自动刷新和运行中切换名单。

改动的五个产品/测试文件：

- `platform/apps/admin-web/src/features/delivery/DeliveryWorkspacePage.tsx`
- `platform/apps/admin-web/src/features/delivery/DeliveryWorkspacePage.test.tsx`
- `platform/apps/admin-web/e2e/delivery-import.spec.ts`
- `platform/apps/admin-web/playwright.config.ts`
- `platform/apps/admin-web/src/test/browserGateHardening.test.ts`

## 真实浏览器断言

CSV 与 JSON 各完成一条独立旅程：创建名单和已有联系人，导入 6 个混合数据行，核对 2 个新增、1 个更新、1 个文件内重复、2 个无效。无效原因分别为坏邮箱与缺少去重键；文件内重复覆盖邮箱大小写归一化。

逐行结果核对当前接口约定的 1 起始数据行号，CSV 表头不计入数据行。作业与结果响应字段采用严格允许列表；结果面板和 outcomes 响应不得回显原始姓名或邮箱。成功三行必须有互不相同的联系人 UUID，更新行保留原联系人 ID。

完成后不经刷新即可看到名单变化。另开新页面重新登录，等待目标名单的实际 `GET /v1/contacts` 200 响应，核对恰好 3 个持久化联系人的 ID、姓名、邮箱及空分页游标，避免 React Query 缓存造成假阳性。测试监听整个浏览器上下文，未请求投递任务或邮件测试发送接口。

两条新增场景进入既有 desktop full 套件；原有 authoring、redaction 及其他用例未删除或放宽。

## 验证结果

前端目录 `platform/apps/admin-web`：

```text
npm run lint                         exit 0
npm run typecheck                    exit 0
npm test -- --run                    exit 0, 48 files / 462 tests passed
npm run build                        exit 0
npm run assert:production-bundle      exit 0, 39 files checked
```

两次最终浏览器运行均使用标准脚本，无测试筛选或自定义门禁覆盖：

```bash
SURVEY_TEST_PREFIX=<本次独立项目名> \
COMPOSE_PROJECT_NAME=<同一项目名> \
TEST_DB=<mysql 或 pgsql> \
ADMIN_WEB_CI_ARTIFACT_DIR=platform/apps/admin-web/test-results/ci-artifacts \
platform/deploy/test/run-admin-web-e2e.sh --fresh
```

| 引擎数据库 | 独立项目 | 开始 | 完成 | 结果 |
|---|---|---|---|---|
| MySQL | survey-w4import-mysql-final-1107 | 10:38:01 | 10:42:37 | exit 0；26 主套件 + 1 套餐 + 1 人员状态通过 |
| PostgreSQL | survey-w4import-pgsql-final-1107 | 10:42:38 | 10:47:16 | exit 0；26 主套件 + 1 套餐 + 1 人员状态通过 |

两次的 Platform API、平台数据库、网关及引擎数据库交叉门禁、运行项目归档均通过。平台数据库仍按现有测试架构使用 PostgreSQL；上述双库切换针对引擎数据库。五个产品/测试文件 SHA-256 在每次运行前后逐一一致。

每次退出后另行查询项目标签，确认容器、卷和网络均为空。标准 runner 删除临时凭据及私密结果；允许导出的脱敏证据和日志仅保存在独立检出的 ignored `.acceptance-evidence/` 下，未加入 Git。测试脚本产生的三个 `tests/tmp/**/.gitignore` 执行位已恢复为原来的 0644。

首次诊断 MySQL 运行曾有 26 个浏览器用例通过，但调用者指定的导出目录名不符合只允许 `ci-artifacts` 的规则，脚本在跨层门禁前退出 1。该次不记为 full 通过；随后使用允许的目录重新执行了上述最终双库运行，未修改门禁。

独立静态复核未发现产品阻断；复核指出的缓存读回证据不足已通过新页面、真实 GET 及联系人 ID 对账补齐。该复核不代替真实运行。

追溯登记沿用工具支持的标准 shell runner 定位符，补充新增场景说明。在 `platform/tools/traceability` 执行 `PYTHONDONTWRITEBYTECODE=1 python3 -m reqtrace.cli check`，退出 0：305 条需求、100 条登记证据、覆盖 41 条需求，原始 02 矩阵已对账。统计不等于功能完成率。

## 验收边界

本记录未执行真实邮件/SMS 发送、生产组织登录、不可变候选 Production Compose、公网 TLS、原生升级恢复、人工读屏、合并或 Release。源码 E2E 使用测试身份，不提升能力矩阵为 Accepted。

本地未另行执行全量 Java 单元测试；标准 E2E 按原流程构建并运行真实后端。远端精确提交 CI 的结果需在 PR 中另行核对。本次未修改共享进度板、蓝图、Wiki 或 Projects。
