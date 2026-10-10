# MJY 平台（LimeSurvey 本土化）

多租户 SaaS ＋ 私有化交付的问卷／考试／测评平台。引擎用 LimeSurvey 7.1.2，**引擎源码与上游逐文件一致**，本项目的全部内容都是新增文件（P0-00.2 结论，见 [docs/p0/upstream-diff.md](docs/p0/upstream-diff.md)）。

> 本文件是**开发与系统情况的单一入口**。总方案见 `/Users/lionel/Documents/MJY 2/LimeSurvey本土化方案/04-开发方案总蓝图（仓库校准版）.md` §0，逐波细节见 [docs/p2/progress.md](docs/p2/progress.md)。

---

## 1. 当前状态

`main` @ `4206f24a`（[PR #8](https://github.com/lsgoodlionel/star-survey/pull/8) 已合并）· 更新 2026-10-09

| 阶段 | 状态 |
|---|---|
| P0 基线与可行性 | ✅（供应商询证与法务意见待业务侧） |
| P1 租户与平台骨架 | ✅ 闸门通过 |
| P2 通用问卷与本土入口 | 🔄 管理端 Phase A+B 已交付并通过真实浏览器验收；Phase C/D 待后续计划 |
| P3 商业与业务应用 | 🔄 考试与测评（WP-09/10）已起步 |
| P4–P5 | ⬜ |

**产品化需求基线（2026-10-09）**

- 前后端需求：[`platform/docs/productization/frontend-backend-requirements.md`](docs/productization/frontend-backend-requirements.md)
- 产品蓝图：[`platform/docs/productization/product-blueprint.md`](docs/productization/product-blueprint.md)
- 发布路线图：[`platform/docs/productization/release-roadmap.md`](docs/productization/release-roadmap.md)
- 机器生成状态：[`platform/docs/productization/capability-map.md`](docs/productization/capability-map.md)

核查报告的需求起点仍为 13 条完整达成、86 条部分完成、191 条未开始、15 条等待外部输入；当前机器能力状态仍为
下列唯一摘要。

能力摘要（机器事实）：accepted=0，partial=11，not_started=1，external=0。

三份 canonical 文档定义角色、用户流、信息架构和 Wave 0-6 门禁，不构成状态提升证据；状态只由
`capabilities.json` 的强类型六层证据和生成器决定。

**测试基线**

| 套件 | 数量 | 说明 |
|---|---|---|
| 平台 Java | **1421 通过 / 175 类** | Task 7 新建隔离库；0 失败 0 错误 0 跳过 |
| 发布网关 Python | **1180 通过** | 0 失败 |
| 需求追溯工具 | **72 通过** | 305 条需求、80 条证据、覆盖 37 条需求、114 个源码编号；矩阵校验通过 |
| 管理端 Vitest | **20 个文件 / 174 通过** | lint、typecheck 与 build 同步通过 |
| 管理端生产构建 | **2089 modules** | 构建通过；大 chunk 提示为非阻断维护项 |
| 管理端真实 E2E | **10 通过（30.2s，fresh stack）** | 768/819/820/1024/1440/Pixel 7、44px、无溢出、键盘/对话框、最终全链路核对成功后归档 |
| 自治工程 Harness | **共 364：358 通过 / 6 环境 skip** | Python 3.12 受控 runtime；11/11 fake drills；只读 governance workflow |
| 插件 PHPUnit | 双库各 147 测试 / 337 断言 | MariaDB 10.11 ＋ PostgreSQL 16 |
| 运行时策略 PHPUnit | 双库各 81 测试 / 392 断言 | |
| 引擎端到端 | 14 个场景脚本 | 涉及数据库的**一律双库执行** |

[GitHub Actions run 37861261899](https://github.com/lsgoodlionel/star-survey/actions/runs/37861261899)
九组质量闸门全部通过，其中管理端真实浏览器纵向验收用时 **6m32s**。Node.js 20 运行时迁移和
`setup-java@v4` 弃用提示属于非阻断维护项。

**代码规模**（2026-10-09，可复现口径）：`platform/services/business/src/main/**/*.java` **558** 个，
`platform/services/business/src/main/resources/db/migration/V*.sql` Flyway 迁移 **45** 个，
`platform/tools/publish-gateway/pubgw/**/*.py` 发布网关生产包 **70** 个（不含 `tests/`）；另有自研插件
3 个、自研作答主题 15 套。前三项分别可用对应目录下的 `rg --files -g '*.java'`、
`rg --files -g 'V*.sql'`、`rg --files -g '*.py'` 复核。

---

## 2. 系统构成

```
作者浏览器 ──→ 管理端 Web（React / TypeScript）──同源 `/v1`──┐
作答浏览器 ──→ LimeSurvey 引擎（作答页）                    │
                    │ 事件回传(HMAC)       ↑ RemoteControl + 插件通道
                    ↓                      │                ↓
               平台（Spring Boot, Java 21）┴─ 发布网关（Python, 纯标准库）
                    │
               PostgreSQL（RLS, ENABLE+FORCE）
```

**五条通道，各自的信任模型不同**：

| 通道 | 方向 | 认证 | 决策 |
|---|---|---|---|
| 管理端 API | 作者浏览器 → 管理端 Web → 平台 | 页面内存 Bearer JWT；同源 `/v1` | 本次作者工作台设计 |
| 引擎事件回传 | 引擎 → 平台 | 每实例派生密钥 HMAC | ADR 0003 |
| 发布与答卷读取 | 平台 → 网关 → 引擎 | 平台↔网关 HMAC＋时间戳；引擎口令只在网关 | ADR 0009 / 0013 |
| 插件副表读取 | 网关 → 插件 | 通道密钥（自实例密钥再派生一层，单向） | ADR 0018 |
| 资产取件 | 浏览器 → 平台 | 签名取件票（按租户派生密钥，验签前零 IO） | ADR 0019 |

**为什么平台不直接连引擎库**：引擎口令只留在网关；平台只传它租户已发布版本里记录的 `(实例, sid)`，网关不做业务授权。

**为什么作答页拿不到平台**：引擎到平台只有 cron 回传（ADR 0008 的出网约束），所以任何需要作答者取用的东西都必须**随定义快照发到浏览器**——资产取件票就是这么设计的。

---

## 3. 目录

| 路径 | 内容 |
|---|---|
| `platform/apps/admin-web/` | 作者工作台 SPA：内存会话、资源树、基础编辑、大纲拖拽排序、批量导入、快速预览（本地草稿渲染）、审批发布与版本查看 |
| `platform/services/business/` | 平台主服务（Spring Boot）。模块：access、asset、audit、contacts、delivery、dictionary、engine、entitlement、identity、onboarding、response、survey、tenant |
| `platform/tools/publish-gateway/` | 发布网关：定义 → LSS → 导入 → 激活 → 回读校验 → 回滚；答卷读取；插件通道客户端 |
| `platform/tools/engine-theme/` | 把随镜像发布的作答主题装进引擎库 |
| `platform/tools/log-shipper/` | 错误日志脱敏、去重、上报 |
| `platform/tools/secure-docs/` | 敏感文档加密 |
| `platform/tools/test-report/` | surefire 报告与源码的测试数对账 |
| `platform/tools/traceability/` | 需求↔测试映射的机器校验（R23-12），数据在 `platform/docs/traceability/` |
| `platform/contracts/` | 跨组件契约（9 份，见下） |
| `platform/docs/adr/` | 架构决策记录（19 份） |
| `platform/docs/p0|p1|p2/` | 逐阶段进度与证据 |
| `platform/deploy/test/` | 与 CI 一致的测试配置与 14 个端到端脚本 |
| `platform/deploy/production/` | 单机生产 Compose、`surveyctl`、加密备份/隔离恢复、脱敏 doctor 与 clean-host 验收入口 |
| `platform/deploy/platform-dev/` | 容器化 Maven（本机无需装 JDK）与平台数据库 |
| `platform/tests/e2e/` | 端到端驱动脚本 |
| `plugins/MjyPlatformBridge/` | 答卷生命周期事件日志与补偿扫描 |
| `plugins/MjyQuestionExtensions/` | 结构化副表、上传会话、服务端校验、插件通道端点 |
| `plugins/MjyRuntimePolicy/` | 访问与作答规则：时间窗、密码、限次、服务端计时、验证码、IP 规则 |
| `themes/question/mjy-*/` | 15 套自研作答主题 |

---

## 4. 契约与决策记录

**契约**（跨语言、跨组件的约定，改动需同步三端）

| 契约 | 内容 |
|---|---|
| `publish-gateway-v1` / `v1.2` | 发布、改版、收口、漂移、**邀请码回读**、存储保留期 |
| `response-read-v1` | 按答卷号读作答；**参与者令牌**；扩展表作答 |
| `survey-logic-dsl-v1` | 定义 v2 逻辑：显示条件、校验、计算值、文本引用、**计分** |
| `survey-access-policy-v1` | 时间窗、密码、邀请码、限次、服务端时长、验证码、IP 规则 |
| `survey-branding-v1` | 品牌主题与多语言 |
| `question-extension-tables-v1` | 插件副表结构版本 |
| `plugin-channel-v1` | 网关↔插件鉴权通道 |
| `platform-dictionary-v1` | 层级字典与版本绑定 |

**ADR** 共 19 份，`docs/adr/`。要害几条：0012 改版再发布与漂移、0016 访问策略与邀请码、0017 通讯录、0018 插件通道、0019 资产与字典。

> ⚠️ **已知缺陷：ADR 0019 有两份**（`0019-platform-assets.md` 与 `0019-platform-dictionary.md`），资产与字典两条车道并行时撞号。65 个文件引用 `ADR 0019`。改号待当前一波车道落地后单独处理——现在改会与在跑的车道冲突。

---

## 5. 常用命令（仓库根目录执行）

```bash
# 平台 Java 测试（Maven 跑在容器里，本机无需 JDK）
# 一律用这个包装脚本，不要自己写数数的命令——见下面硬规矩 2
PLATFORM_DB_NAME=platform platform/deploy/test/run-platform-tests.sh

# 发布网关测试（本机无 pytest，用 unittest）
cd platform/tools/publish-gateway && python3 -m unittest discover -s tests -t . -q

# 管理端轻量质量闸门（Node 22）
cd platform/apps/admin-web
npm ci
npm run lint
npm run typecheck
npm test -- --run
npm run build
npm run assert:production-bundle
cd ../../..

# 管理端真实浏览器纵向验收（Docker + Playwright，MySQL 测试引擎）
SURVEY_TEST_PREFIX=adminweb-final COMPOSE_PROJECT_NAME=adminweb-final TEST_DB=mysql \
  platform/deploy/test/run-admin-web-e2e.sh --fresh

# 测试总数对账脚本自己的用例
cd platform/tools/test-report && python3 -m unittest discover -s tests -t . -q

# 引擎端到端（涉及数据库的都要 mysql 与 pgsql 各跑一遍）
TEST_DB=mysql platform/deploy/test/run-p1-e2e.sh --fresh
TEST_DB=pgsql platform/deploy/test/run-access-policy.sh --fresh

# 并行开发时各车道用自己的前缀，互不踩踏
SURVEY_TEST_PREFIX=l1 COMPOSE_PROJECT_NAME=l1 TEST_DB=mysql platform/deploy/test/run-question-themes.sh
```

CI 失败时只上传 `platform/apps/admin-web/test-results/ci-artifacts/` 中经 gate 白名单重写的
`*-sanitized-failure.png` 与 `*-sanitized-trace-summary.json`。后者只含 project、固定 test id、状态、耗时、
真实管理端路由白名单内的路径，以及最近最多 25 条仅含 method/status/无 query URL 的网络事件，是结构化排障
摘要而非 Playwright raw trace。gate 会在 JSON 解码、percent decode、NFKC 规范化后检查所有字符串，并复扫
最终输出 bytes；JWT、headers、body、临时元数据、raw trace 和原始截图不会上传。

Task 7 用户可见证据位于 `docs/audits/2026-10-09-product-alignment/07-admin-workspace-*.png`：截图人工检查只证明四档画面中
固定 demo 名称可见、无明显遮挡或截断，且业务导航未混入引擎 `/admin`。横向溢出、44px 控件、819/820 边界、焦点与对话框
由真实 Playwright DOM 测量；另有运行中 `adminweb-demo` 的 Chromium 巡检遍历固定三层并拒绝 E2E 时间戳资源。Task 7 在
`820–1179px` 只提供现有布局的不溢出临时保障，Phase C 约定的双栏 + 检查器尚未实现。代码、自动化和用户可见证据仅支持
Phase A+B；Phase C/D 仍是后续工作。
仓库已具备单机生产部署和 Release publication 代码路径，详见 [`platform/deploy/production/README.md`](deploy/production/README.md)：固定 digest Compose、`surveyctl` 生命周期、加密备份、隔离恢复、脱敏 doctor、RC 制品签名/证明、四节点 native readiness、stable 原字节推广，以及 GitHub Release 页面身份与资产字节的幂等核对均有自动化契约与故障注入。当前仍不能宣称正式 production-ready：本任务没有创建真实 tag、GitHub Release 或正式 Engine 多架构镜像，Ubuntu 22.04/24.04 × AMD64/ARM64 的真实 GitHub runner 与公网 TLS clean-host 证据仍须在首个 RC 流水线中产生。PR #10 于 2026-10-09 重新核验仍为 OPEN、未合并，依赖仍待合并。

### 跑测试的三条硬规矩（都是真实踩出来的）

1. **必须 `clean test`**。Maven 不删被改名或删除的资源，`target/classes` 里会留旧文件。迁移改名后只跑 `test`，轻则看到 935 个报错的**假故障**，重则旧迁移恰好可重复执行而给出**假成功**。
2. **跑前清空 `target/surefire-reports`，跑后用 `run-platform-tests.sh` 对账——不要自己写数数的命令。**
   只看 `failures=0` 会被三种假绿骗到，**三种都真实发生过**：
   - 新测试类根本没被执行（靠总数差 14 才发现）；
   - 改包名后旧报告残留被重复计数（虚高 14 条 1 个类）；
   - **量具本身有盲区**：surefire 的 `.txt` 摘要把带 `@Nested` 的类记成 `Tests run: 0`（XML 里那几条用例是记在外层类名下的），所以任何基于 `.txt` 求和的命令，对这类类**永远看不见**——它哪天真的不跑了，总数纹丝不动，守门的规矩完全不报警。
     本项目曾因此出现同一次运行 XML 算 1287、`.txt` 算 1282 的分歧，绕了三轮才定位。
   对账脚本读 XML 并交叉核对类集合，不受此影响。**因此：不要用 `grep "Tests run:" *.txt | awk` 这类一行流数数。**
3. **并行车道必须用独立前缀与独立数据库名**，且预先分配迁移号段——让各车道自己 `ls` 选号是不够的，三条并行时它们看到的最高号都一样，必然撞号（已发生过一次）。

---

## 6. 已交付能力

> ⚠️ **这张表是「工程切片」口径，不是「需求达成」口径，两者差距很大。**
> 表里的 ✅ 指该工作包的计划切片已交付并有测试证据，**不等于** `02-全量需求追踪矩阵.md` 里那条需求的验收断言全部满足。
> 已逐条取证的部分：WP-01 九条需求里只有 1 条全绿（表里标 ✅）；WP-03 七条里只有 2 条全绿（表里标 ✅）；
> WP-18 十条里 **0 条**全绿（表里标 ✅）；WP-02 四十七条里 5 条全绿。
> **验收一律以需求矩阵的逐条断言为准**，不要引用这张表。差距的主要来源有三类：
> 缺管理端 UI（无浏览器/移动/读屏测试）、断言的复合子项只做到一部分、以及外部供应商未签约。


| 工作包 | 状态 | 内容 |
|---|---|---|
| 管理端创作链路 | 🟡 | 组织免登安全交接、资源树和项目/文件夹/问卷创建、说明文字与单选/多选/短文本/长文本编辑、题组及题目拖拽/按钮/键盘排序、无损保存与冲突保护、批量文本导入、快速预览（本地草稿渲染）、审批发布和不可变版本查看；桌面与移动端已有真实 E2E |
| WP-23 共同基础 | 🟡 | 租户隔离（RLS）、公开路由、身份绑定、每实例事件密钥、审计；事件日志与补偿扫描；催答完成对账。跨系统对账报表、隐私删除、监控 ⬜ |
| WP-22 套餐与计量 | 🟡 | 套餐版本、试用／付费订阅、有效答卷计量、席位额度、租户开通；赠送有效期 ⬜ |
| WP-19 团队与品牌 | 🟡 | 角色目录、资源树授权继承、字段与导出权限、席位联动；企业模板库；`zh-business` 主题与多语言。协作员到期、自定义域名 ⬜ |
| WP-01 创建与编辑 | ✅ | 定义格式与 LSS 编译、首发与改版再发布（原子路由切换）、草稿乐观锁、漂移检测、**旧版恢复**、**批量文本导入预览** |
| WP-02 题型 | 🟡 | 47 项对照表；原生与原生加主题；**15 套自研主题**；插件副表结构版本；服务端校验（手机号／邮编／身份证／统一社会信用代码）。剩 6 类待资产服务补齐能力 |
| WP-03 逻辑与计算 | ✅ | 定义 v2 DSL、类型检查与环检测、AST→ExpressionScript、**计分**、**双执行比对**（双库 48 例零分歧） |
| WP-04 访问与作答规则 | ✅ | 时间窗（含改客户端时钟）、密码、邀请码、按 token/设备/IP 限次、服务端时长、验证码、IP 规则；策略摘要双端校验 |
| WP-05 投放触达 | ✅ | 链接、二维码、短链、内嵌、签名渠道参数；批量任务（崩溃续跑不重发）、回执验签去重、退订不可翻转；催答与通知。真实短信／邮件通道 ⏳ |
| WP-06 数据与输出 | 🟡 | 分页查询、字段字典、脱敏；导出作业（快照、续跑、再授权）；CSV／XLSX／**SAV**／**DOCX**；**扩展表作答进导出**。附件打包、PDF ⬜ |
| WP-18 通讯录 | ✅ | 三类身份分离、名单导入去重、部门树与数据范围、**联系人 → 邀请码自动登记** |
| WP-20 集成 | 🟡 | 企微／钉钉／飞书授权与免登、事件回调、定时同步。真实联调待凭据 ⏳ |
| WP-09/10 考试测评 | 🔄 | 本波进行中 |


### 自治工程治理

2026-10-10 已完成自治工程控制平面 Task 8，实现提交 `1101f824`。可复用核心位于
`tools/agent-harness/` 与 `scripts/agent-harness`；Survey 的 Gate/保护路径、项目记忆和计划属于项目策略模板；
`.github/workflows/agent-governance.yml` 属于当前仓库的只读宿主集成。三层边界、安装、使用、恢复和升级命令见
[`tools/agent-harness/README.md`](../tools/agent-harness/README.md)。

最终本地证据为 Harness 共 364 tests：358 通过，6 个依赖本机缺失 Docker fixture image 的 Linux wrapper 用例 skip，
11/11 fake drills 通过，productization 37 tests 与 capability check 通过。唯一一次 disposable real-Codex smoke
安全暂停：没有文件改动、没有 Gate 运行，脱敏运行历史在 worktree 清理前完成验证；主 worktree 与生产/发布均未接触。
当前本机 `java` 不是项目要求的 21，裸 `python3` 是 3.9，需继续使用版本化 Harness runtime；真实 smoke 的停止
分类未被当次旧版 runner 摘要保留，不能据此宣称 Codex 登录/配额/CLI 已可用。

Task 8 按用户要求不 push、不更新 Obsidian，且禁止 subagent，因此三名独立 reviewer 门尚未满足；本任务只完成
安全、状态恢复、开发体验和 CI 的作者自审。首个候选真实产品 Milestone 是管理端 Phase C-1 的
`820-1179px` 双栏与检查器壳层，须在独立 reviewer、GitHub/Obsidian 同步和 Codex 环境就绪后另行批准精确文件范围。

### 持续集成

本土化部分的检查在 `.github/workflows/platform-quality.yml`（上游自带的几条只在 `master` / `develop-*` 触发，本仓库工作分支是 `main`）：

| Job | 内容 | 需要什么 |
|---|---|---|
| `parity-tables` | 发布网关单测（含**三端注册表**与**三端数值上限**两张对照表）＋ 对账脚本自己的用例 ＋ **需求追溯校验** | 只要 Python，秒级 |
| `admin-web` | Node 22 下执行 `npm ci`、lint、typecheck、Vitest、生产构建与 production bundle assertion | Node 22 |
| `admin-web-e2e` | 桌面完整创作、pointer/键盘/按钮排序与移动端响应式编辑；真实串起管理端、平台、网关和 MySQL 引擎 | Docker ＋ Chromium；依赖 `admin-web` 与 `parity-tables` |
| `gateway-parity-e2e` | WP-03.4 双执行比对，`mysql` 与 `pgsql` 两个矩阵分支各跑一遍 | 起引擎栈（脚本自行构建 `survey-web` 镜像） |
| `platform-java` | 平台 Java 全套（`run-platform-tests.sh`，含测试数对账） | `actions/setup-java` ＋ PostgreSQL service 容器 |
| `access-policy-e2e` | WP-04 访问策略端到端（R04-01…05），`mysql` 与 `pgsql` 各一遍 | 起引擎栈 |
| `p1-e2e` | P1 纵切：平台 ＋ 网关 ＋ 引擎三个真实进程 | 引擎栈 ＋ `platform-db` ＋ 平台 jar |

平台 Java 套件**已接进 CI**。此前判断「Maven 走阿里云镜像、runner 上冷缓存跨境拉取，
接进来又慢又飘」，两个理由分别这样消掉：

- **镜像**：CI 不走 `platform-dev/mvn.sh`，改走 `mvn-local.sh`（runner 上 `setup-java`
  装好的 Maven，没有 `settings.xml`）。实测对照下来，镜像在这个 pom 上**根本没有好处**：
  同样 347 个 jar／121 MB 的冷缓存，阿里云镜像 851 秒，Maven 中央仓库 359 秒，
  而这还是在国内开发机上量的。
- **冷缓存**：`actions/setup-java` 的 `cache: maven` 按 `pom.xml` 哈希缓存 `~/.m2/repository`。
  `clean test` 实际用到 **191 个 jar、约 82 MB**（与网络无关的量级），`actions/cache`
  存取秒级，只有改 `pom.xml` 那一次才重新下载。

实测：依赖缓存命中时全套 **74 秒**（1368 条／172 类，0 失败）。两个冷缓存数字测的是
本机网络、**不能**当作 runner 的预估，口径与完整数据见
[`platform/services/business/CONVENTIONS.md`](services/business/CONVENTIONS.md)。

端到端脚本按「覆盖接缝与权限闸门」优先接入 `run-access-policy.sh`、`run-p1-e2e.sh` 和
`run-admin-web-e2e.sh`，
其余仍是本地约定；取舍与下一批建议写在 workflow 末尾的注释里。

---

## 7. 已知遗留

单机生产运行手册、命令与当前验收边界见 [`deploy/production/README.md`](deploy/production/README.md)。本地故障注入通过不替代 Release CI 的真实主机矩阵；备份加密密钥的异地保管和备份保留策略仍由部署运维负责。

**部署前必须处理**

- 发布网关的并发锁与限流**都在进程内**：网关只能单副本运行；多副本时实际限额 ＝ 缺省值 × 副本数。真实按 IP 限流需入口层提供客户端地址（不信任 `X-Forwarded-For`）。
- **对象存储只有本地实现**，资产与导出在多副本下都需共享卷或对象存储。
- 引擎自身的 phpunit 套件**未与 CI 配置对齐**（P0 起的遗留），因此未纳入本项目的回归基线。

**安全相关（已记录、未修）**

- **资产取件票是 bearer**：拿到链接的人能看图。这对问卷媒体是有意为之（作答链接自己也是 bearer），但**作答者自己上传的内容不能沿用这条路**。
- 邀请码在 `contact_participation` 里，撤销只置 `revoked_at`、不删文本；旧引擎问卷已关闭故码打不开任何东西，但「过期凭据不落盘」需要运维清理策略。
- 飞书解密测试 **1/256 flaky**：`AES/CBC/PKCS5Padding` 靠填充异常判定密钥错误，错误密钥下填充恰好合法的概率约 1/256。验签排在解密之前，故非漏洞，但会造成唬人的假警报。
- 停用租户已签发的令牌最长 10 分钟内仍有效。

**一致性与规范**

- **ADR 0019 撞号**（见 §4）。
- **平台自有键政策不一致**：`participants`／`dictionaryVersion` 静默摘除，`assetVersion` 判定义非法并报错。已决定统一为报错，待处理。
- ~~三端（平台／网关／插件）的数值上限常量靠注释互指，没有自动一致性检查~~ **已补**：`platform/tools/publish-gateway/tests/test_limit_parity.py` 的声明式对照表，15 组，跟着网关单测与 CI 跑；当时对照表上线时三端全部一致，没有发现存量漂移。字符集／正则形状仍只靠注释互指，**没有**进对照表。
- 双执行比对**仍然是固定快照**（4 条手写向量 × 12 处表达式 = 48 条，期望值按契约人工写下），无类型驱动的随机差分测试。接进 CI 只是让这份快照每次改动都被重跑，覆盖面没有变；要扩得往 `publish-gateway-scoring.vectors.json` 里加向量。

**功能缺口**

- 管理端已交付题组与题目的 pointer 拖拽、键盘排序和移动端按钮替代操作；仍未交付高级题型可视化配置、真实 LimeSurvey 运行时预览和专门的屏幕阅读器审计。当前复杂题型仍是只读无损往返，现有入口明确标注为“快速预览”，仅使用本地草稿 renderer。
- PDF 导出卡在中文字体（已决定随仓库交付思源黑体，SIL OFL 1.1）。
- 附件打包卡在 `get_uploaded_files`——它一次把**整份答卷**的全部文件 base64 塞进一个 JSON 应答。
- 字典节点上限 8000（由定义快照 1 MiB 倒推）：到区县够用，到乡镇街道不够。
- 行政区划数据集**不随仓库交付**（各家许可与署名义务不同，由交付方决定）。
- 新主题的编辑器脚本只有「标记与 JS 真的出现在页面上」这一层证据，无浏览器交互测试。

---

## 8. 工程约定

- **测试先行**：先写测试、**亲眼看它失败**再实现。首跑即绿的用例是「回归钉子」，必须与「驱动实现的测试」分开说明——两者价值不同。
- **涉及数据库的端到端一律双库**（MySQL ＋ PostgreSQL）。只过一种不算数。
- **契约改动要三端同步**，并尽量用机器钉住（先例：`test_plugin_registry_parity.py` 比对三处注册表）。
# 也可以钉住基线（数字每波都变，用时以本文 §1 的测试基线为准）
PLATFORM_DB_NAME=platform platform/deploy/test/run-platform-tests.sh --expect-tests <N> --expect-classes <M>
- **并行车道不动共享汇总文档**（`docs/p2/progress.md`），由集成方统一写——否则每次合并必冲突。
- 文件 200–400 行为宜、最多 800 行；函数 <50 行；错误显式处理，不静默吞掉。
- 提交信息 `<type>: <描述>`，正文写清**为什么这么选**，尤其是反直觉的决定——合并冲突的取舍也要写进最终提交信息，否则 squash 之后无处可查。

## 9. 仓库与分支

- 代码仓库：`lsgoodlionel/star-survey`（remote `origin`），唯一长期分支 `main`，经 PR 合并。
- 上游 fork `lsgoodlionel/LimeSurvey` 保留为 remote `limesurvey-fork`，用于对比上游与合并升级。
- `main` 的根提交是 LimeSurvey 7.1.2 快照（上游 `4c20c680`）：本地为浅克隆，无法推送浅历史，故以单个根提交导入；上游完整历史仍在 fork 仓库。
- **不要修改引擎源码与根 `README.md`**：自有内容全部是新增文件，这个不变式是上游升级可行性的基础。
