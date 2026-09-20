# P0-00.2 functional／acceptance（浏览器）套件回归

- 日期：2026-09-20
- 关联：[ADR 0001](../adr/0001-engine-baseline.md) 待办第 1 条
- 验证栈：`platform/deploy/functional/`（compose 项目 `survey-functional`）
- 结论：**CI 的 13 个浏览器套件全部跑通，189 个用例 0 失败 0 错误。没有任何失败可以归到我们的代码上。**

---

## 1. 环境

| 项 | 本地 | CI（`.github/workflows/functional.yml`） |
|---|---|---|
| PHP | 8.3.33（`survey-web` 镜像） | 8.1 / 8.3 两条矩阵 |
| 数据库 | MariaDB 10.11（tmpfs） | Ubuntu 22.04 自带 MySQL |
| WebDriver | Selenium Standalone **4.49.0** ＋ Firefox **156.0**＋geckodriver 0.37.1（aarch64） | selenium-server-standalone **3.9.1** ＋ runner 自带 Firefox |
| 拓扑 | selenium 与 web **共享网络命名空间**，三方互为 localhost | 三方同在一台 runner 上 |
| 套件隔离 | `--fresh` 时每个套件都重建库与全部卷 | 每个套件一个独立 job（全新检出＋全新库） |

> **浏览器版本差异是本次最大的不可控因素**：Selenium 4.49＋Firefox 156 与 CI 的
> Selenium 3.9.1 差了 6 年。下面第 4 节「环境类」里有几条就是由此而来。

跑法：

```bash
platform/deploy/functional/run-functional.sh --fresh security api      # 基线
platform/deploy/functional/run-functional.sh --with-mjy-plugins ...    # 自研插件归因对照
platform/deploy/functional/teardown.sh
```

---

## 2. 结果：CI 矩阵的 13 个套件全部跑通

`functional.yml` 的 matrix 恰好是这 13 个套件，本次全部覆盖（单 PHP 版本 8.3）。

| 套件 | 用例 | 断言 | 失败 | 错误 | 其他 | 用时 |
|---|---:|---:|---:|---:|---|---:|
| api | 5 | 16 | 0 | 0 | | 5s |
| plugin | 10 | 365 | 0 | 0 | | 13s |
| validation | 4 | 16 | 0 | 0 | | 25s |
| survey | 6 | 23 | 0 | 0 | | 44s |
| security | 9 | 20 | 0 | 0 | 1 risky | 59s |
| import-export | 3 | 20 | 0 | 0 | | 57s |
| theme | 4 | 21 | 0 | 0 | | 69s |
| admin | 62 | 66 | 0 | 0 | | 108s |
| user | 7 | 31 | 0 | 0 | | 139s |
| navigation | 26 | 80 | 0 | 0 | | 92s |
| question-a | 16 | 62 | 0 | 0 | 1 incomplete | 159s |
| question-b | 17 | 75 | 0 | 0 | | 196s |
| expression | 20 | 119 | 0 | 0 | | 118s |
| **合计** | **189** | **914** | **0** | **0** | 1 risky ＋ 1 incomplete | **1084s（约 18 分钟）** |

真实通过率：**189/189 = 100%**；其中 1 个 risky、1 个 incomplete 是上游测试自身的状态（见第 4 节）。

> **这不是第一次跑出来的数字。** 第一轮 13 个套件里有 6 个是红的。
> 每一条红都定位到了原因，全部是本地栈与 CI 的环境差异，逐条修掉之后才变成上表。
> 第 4 节按类别给出每一条的证据与修法 —— 那些修的过程本身就是「这些失败不是引擎问题」的证明。

---

## 3. 归因：失败是不是我们造成的？

两条独立证据。

### 3.1 全新库里我们的插件根本没被登记

引擎只有在插件管理器扫描之后才会往 `lime_plugins` 落记录。全新安装的库里：

```
AuditLog 0 / Authdb 1 / AuthLDAP 0 / ... / UpdateCheck 1
```

**`Mjy*` 三个插件一条记录都没有** —— 上表的基线是在「我们的插件完全不参与」的前提下跑出来的。

### 3.2 把三个插件激活后重跑同样的 13 个套件，结果逐行一致

`run-functional.sh --with-mjy-plugins` 会先往 `lime_plugins` 写入并激活
`MjyPlatformBridge`、`MjyQuestionExtensions`、`MjyRuntimePolicy`，再跑同一批套件。
跑完确认三者 `active=1`、`load_error=0`（即确实被加载，不是被静默跳过）：

```
MjyPlatformBridge       1  0  NULL
MjyQuestionExtensions   1  0  NULL
MjyRuntimePolicy        1  0  NULL
```

两轮的 phpunit 汇总行逐行比对：

```
diff <(基线的 Tests:/OK 行) <(激活插件后的 Tests:/OK 行)   →  无差异
```

13 个套件、189 个用例、914 个断言，**完全一致**（总用时 1084s vs 1168s，差异在噪声范围内）。

**结论：没有任何一条失败可以归因到我们的插件。**

---

## 4. 失败分类与证据

### 4.1 上游自身状态（不是缺陷，CI 上同样如此）

| 用例 | 现象 | 证据 |
|---|---|---|
| `GetGroupAndQuestionIdPermissionTest::testPermissionOnCondition` | risky：`This test did not perform any assertions` | 该用例只在发现越权时调用 `$this->fail()`，通过路径上一个断言都没有。CI 用的是 `--fail-on-skipped`，没有 `--fail-on-risky`，所以在 CI 上是绿的。**安全结论本身是通过的**（没有触发任何越权失败） |
| `RankingArrayFilterMaxColumnTest::testRanking` | incomplete | 上游自己写的：`markTestIncomplete('Ranking drag-and-drop interaction not implemented for geckodriver')`（`tests/functional/frontend/RankingArrayFilterMaxColumnTest.php:35`） |

### 4.2 环境类（本地栈与 CI 的差异，已全部定位并修掉）

这一类的证明方式是统一的：**先复现，找到与 CI 的具体差异，改本地栈（不动引擎源码），失败消失。**

| # | 用例 | 现象 | 根因 | 修法 |
|---|---|---|---|---|
| a | `IpAddressAnonymizeTest`（2 条） | `Failed asserting that false is true` | 用例把 `ipaddr === '127.0.0.1'` / `'127.0.0.0'` 写死。selenium 独立组网时浏览器源地址是 `172.26.0.4`。**Apache access log 实证**：提交答卷的 `POST /index.php/467369` 来自 `172.26.0.4` | selenium 改为 `network_mode: service:web`，与 CI 单机拓扑一致 → 3 条全绿 |
| b | `InstallationControllerTest::testBasic` | `Could unlink config.php` | 我们把 `config.php` 以**单文件 bind mount** 挂进容器，而该用例要 `unlink()` 它再让安装向导重写一份；bind mount 的文件在容器里删不掉 | `application/config` 改成命名卷，每轮从只读仓库副本重灌 |
| c | `InstallationControllerTest::testBasic` | `Unable to locate element: #ls-next` | 安装器预检把 `/tmp directory` 标红（**截图实证**：`tests/tmp/screenshots/InstallationControllerTest_testBasic.png`），预检不过就不渲染 next 按钮。用例自己的兜底是 `exec("sudo chmod -R 777 ./tmp")`，而容器里没有 `sudo`，日志里明明白白写着 `sh: 1: sudo: not found`；CI 的 runner 有 | 容器内加一个 `sudo` 垫片（直接 exec 后续命令），语义与 CI 一致 |
| d | `ThemeControllerTest`（3 条） | `TimeoutException`，等不到「Theme editor: vanilla_version_1」 | 该用例把 vanilla 扩展成 `vanilla_version_1` 但跑完不清理。`upload/` 走的是仓库 bind mount，上一轮的 `vanilla_version_1` 还在，同名再建就卡住。CI 每个 job 都是全新检出，碰不到 | 给栈一份独立的 `upload` 命名卷，每轮重灌 → 4 条全绿 |
| e | `QuestionThemeTest::testImportQuestionTheme` | `POST /session` 超时 180s（连带 2 条 `@depends` 被 skip） | Selenium 只配了 1 个 session 槽位；上一个测试类的会话没退干净就一直占着，下一个类只能排队到超时 | `SE_NODE_MAX_SESSIONS: 2`，并在每轮开跑前确认槽位空闲，不空就重启 selenium。question-a 从 440s／1 错误 → 159s／0 错误 |
| f | 多个套件的 `tearDownAfterClass` | `Failed to decode response from marionette` | 出现在 `webDriver->quit()`（`TestBaseClassWeb.php:83`），**从不出现在用例体内**。Selenium 自己的日志是 `Session stopped successfully (QUIT_COMMAND)`，服务端没问题；单独建会话再 quit 两次都正常 | 与 e 同源（槽位争用）。槽位问题修掉后，security／theme／admin／import-export 上的这类失败全部消失 |
| g | `UserStatusTest::testFloatingActionDeactivate` | `Failed asserting that 1 matches expected 0` | 点完 AJAX 按钮**不等待**就直接读库断言。同一个文件里结构完全相同的 `testCanDeactivateNewUser` 却是绿的 —— 纯竞态 | 重跑即绿（已连续多轮通过）。属于上游用例缺同步，不改引擎源码 |
| h | navigation／question-a／question-b／expression 整套在 bootstrap 阶段 `CDbConnection failed to open` | 4 个套件各 0s 直接挂 | `InstallationControllerTest` 会 `DROP DATABASE`，失败时不重建；CI 里每个套件是独立 job 所以不受影响，本地串跑会被它带走 | runner 改成**每个套件前都重新 prepare**（含 `CREATE DATABASE IF NOT EXISTS`），复刻 CI 的一套件一环境 |

### 4.3 我们的代码造成的

**无。** 依据见第 3 节：基线跑的时候我们的插件连注册都没注册；把三个插件全部激活后重跑
13 个套件，189 个用例的结果与基线逐行一致。

---

## 5. 本地栈相对 CI 的已知偏差（结论的适用边界）

1. **浏览器版本差 6 年**：Firefox 156 + Selenium 4.49 vs CI 的 Selenium 3.9.1。
   本次没有因此产生残留失败，但「在本地绿」不等于「在 CI 的老浏览器上也绿」，反之亦然。
2. **只跑了 PHP 8.3**，CI 还有一条 8.1 的矩阵。
3. **没有跑 `functional` / `acceptance` 两个整目录套件** —— 它们与这 13 个域套件大量重叠，
   CI 也不跑它们。
4. **`themes/` 仍然是共享的仓库 bind mount**。`upload/`、`tmp/`、`application/config` 都已经
   隔离成命名卷，`themes/` 还没有。目前没有用例因此失败，但主题类用例将来若开始往
   `themes/` 写东西，会重演第 4.2 节 d 那个问题。
5. **CI 用 `--stop-on-failure`，我们不用**。我们要的是完整通过率，不是第一个红灯。

---

## 6. 对 ADR 0001 的影响

- 待办第 1 条「functional 套件（需 Selenium）选择性回归」**可以关闭**：
  不是「选择性」，是 CI 矩阵的 13 个套件全跑，189/189 通过。
- 建议把引擎变更门禁从「unit 套件 0 错 0 失败」扩展为
  「unit ＋ 这 13 个浏览器套件都 0 错 0 失败」，并且**插件激活态也要跑一遍**
  （`--with-mjy-plugins`）—— 本次的归因结论只有在这条持续执行时才继续成立。
