# 业务平台开发约定（P1 起适用）

## 构建与测试

```bash
PLATFORM_DB_NAME=<你的库名> platform/deploy/test/run-platform-tests.sh
```

本机不需要 Java，Maven 在容器里运行，依赖走阿里云镜像并缓存在命名卷。每条并行车道用自己的数据库名，互不干扰。

### 同一个入口，两种执行环境

`run-platform-tests.sh` 用 `PLATFORM_MVN` 决定怎么跑 Maven，**对账口径两条路完全一致**——
「跑了多少条」只有一个数法，不能因为换了执行环境就换一套。

| 环境 | `PLATFORM_MVN` | JDK／Maven 来自 | 依赖缓存 | settings.xml | 数据库 |
|---|---|---|---|---|---|
| 开发机 | 默认 `platform-dev/mvn.sh` | `maven:3.9-eclipse-temurin-21` 容器 | docker 命名卷 `platform-maven-repo` | 本目录那份（阿里云镜像） | compose 起 `platform-db` |
| CI | `platform-dev/mvn-local.sh` | `actions/setup-java` | `actions/cache`（`cache: maven`） | 无（Maven 中央仓库） | service 容器 ＋ `init-db.sql` |

`mvn.sh` 另有两个可覆盖项：`PLATFORM_MAVEN_SETTINGS`（置为空字符串＝不挂 settings.xml，
走中央仓库）与 `PLATFORM_MAVEN_REPO`（依赖缓存的卷名或宿主目录；想实测冷缓存耗时就指向
一个临时卷，**别动共用的那个**）。

### 为什么 CI 上不用阿里云镜像

阿里云镜像是给**国内开发机**的。GitHub runner 在境外，对它来说镜像是慢且易抖的那一头，
而 Maven 中央仓库是近的。

实测（2026-09-28，开发机 8 核／8 GB Docker，国内网络）：

| 场景 | 耗时 | 落盘 |
|---|---|---|
| 依赖缓存命中，`clean test` 全套（1368 条／172 类，0 失败） | **74 秒**（复测 77 秒） | — |
| 依赖缓存全冷，`dependency:go-offline`，**阿里云镜像** | **851 秒（14.2 分钟）** | 347 jar／121 MB |
| 依赖缓存全冷，`dependency:go-offline`，**Maven 中央仓库** | **359 秒（6.0 分钟）** | 347 jar／121 MB |

两次冷缓存落盘的 jar 数与体积完全相同，所以这是一次干净的对照：**即便在国内开发机上，
中央仓库也比阿里云镜像快 2.4 倍**。镜像在这个 pom 上没有带来任何好处，
去掉它不是为 CI 做的妥协。

`clean test` 实际用到的是其中一部分：车道共用的 `platform-maven-repo` 卷只跑过
`clean test`，里面是 **191 jar／约 82 MB**——这才是 `actions/cache` 要存取的量级，秒级。

**不能外推的部分**：上面两个冷缓存数字测的是**本机的网络**。我无法在真实 GitHub runner
上实测（车道不推送），所以别把它们当成 CI 的预估。能跨环境外推的只有两样：
依赖体积（与网络无关）和缓存命中时的 74 秒（也与网络无关，可直接当作 CI 上这条 job
缓存命中时的量级）。

### 测试数字一律走对账脚本

**车道汇报「跑了多少条、多少个类」只认 `run-platform-tests.sh` 的输出，不要手工数 `target/surefire-reports`。** 前几波两次被假绿骗到，两次都是人工核对才发现的：

1. 新测试类根本没被执行，报告里却 `failures=0`（靠总数差 14 才发现）；
2. 改包名后旧报告残留在 `target/surefire-reports` 里被**重复计数**（虚高 14 条 1 个类）。

脚本做三件事：跑前清空报告目录并确认清空、强制 `mvn clean test`、跑完把报告和源码对账：

- 源码里有 `@Test`／`@ParameterizedTest`／`@RepeatedTest`／`@TestFactory`／`@TestTemplate`、名字又落在 surefire 默认扫描模式里的类，**必须**有一份报告——少一份就是事故 1；
- 报告里出现的类，**必须**在源码里找得到——多一份就是事故 2；
- 外加：同一个类多份报告、0 条用例的报告、失败／错误非零。

对不上就以 2 退出。用法：

```bash
PLATFORM_DB_NAME=platform_x platform/deploy/test/run-platform-tests.sh
# 与上一轮记下的数字对账（汇报前跑这一条）
PLATFORM_DB_NAME=platform_x platform/deploy/test/run-platform-tests.sh --expect-tests 1231 --expect-classes 153
# Maven 参数放在 -- 之后
PLATFORM_DB_NAME=platform_x platform/deploy/test/run-platform-tests.sh -- -Dtest=SurveyServiceTest
```

对账本身是 `platform/tools/test-report/surefire_reconcile.py`，可以单独对着已有的报告目录跑；它的用例在 `platform/tools/test-report/tests/`（`cd platform/tools/test-report && python3 -m unittest discover -s tests -t . -q`），里面按两次事故的原样造了 fixture，证明它确实会红。

不按文件时间判断陈旧：Maven 在容器里跑，报告 mtime 来自容器时钟，和宿主机时钟不是一个源，比时间会出假红。上面的集合对账是无时钟的。

## 模块与包的归属

| 包 | 职责 | Flyway 版本号段 |
|---|---|---|
| `cn.mjy.platform.shared` | 跨模块契约（`TenantId`、`TenantContext`、`Money`、`EngineInstanceDirectory` 等）与基础设施 | V1–V99 |
| `cn.mjy.platform.tenant`、`identity` | 租户开通与状态、引擎实例登记与路由、身份 | V100–V199 |
| `cn.mjy.platform.entitlement` | 套餐、订阅、额度判定、用量账本 | V200–V299 |
| `cn.mjy.platform.engine` | 引擎事件接收、去重、答卷投影、发件箱 | V300–V399 |
| `cn.mjy.platform.access` | 角色、资源范围、字段与导出权限、席位 | V400–V499 |
| `cn.mjy.platform.survey` | 问卷草稿、发布状态机、发布审批、待核对核对、版本与题目绑定 | V500–V599（V500–V509 发布，V510–V519 审批，V520–V529 核对，V530–V539 重新发布与漂移，V540–V559 模板库） |
| `cn.mjy.platform.survey.template` | 企业模板库：租户私有模板与平台（运营）模板、版本、审核、复制、下架 | V540–V559（V540 租户模板，V541 平台模板） |
| `cn.mjy.platform.response` | 答卷查询（投影分页、按需经网关取作答、字段字典、数据权限）、导出作业（ADR 0015） | V600–V609（V600 导出作业与快照） |
| `cn.mjy.platform.delivery` | 投放触达（链接／二维码／短链／内嵌、签名渠道参数、短信邮件批量任务、回执与退订、催答与新答卷通知） | V700–V799（V700 链接与短链，V710 任务与收件人，V720 回执与退订，V730 催答与通知） |
| `cn.mjy.platform.contacts` | 通讯录：三类身份的联系人与显式关联、名单与导入作业、部门树与标签、部门级数据范围、参与者令牌映射（ADR 0017） | V800–V899（V800 目录，V801 部门范围，V802 导入作业，V803 参与者映射） |
| `cn.mjy.platform.dictionary` | 平台层级字典：字典、版本与节点树，按父节点分页与关键字搜索、服务端路径判定（ADR 0019） | V900–V999（V900 字典、版本与节点） |
| `cn.mjy.platform.asset` | 平台资产：作者上传的图片／音频／视频、版本留存、按租户隔离、匿名作答者的签名取件票、发布时把定义里的 `assetId` 换成取件地址（ADR 0019） | V901–V909（V901 资产库；**V900 归字典车道**，本模块不占它） |
| `cn.mjy.platform.audit` | 审计日志 | V1–V99（随 shared） |

- 迁移号段互相独立，已开启 Flyway `out-of-order`：某模块在自己的低号段追加迁移时，已跑过更高号段的库会补跑它。因此**迁移不得依赖其他模块号段里的迁移顺序**；跨模块的依赖只能指向已发布的旧迁移。
- 已发布（进入 `main`）的迁移不得修改，需要变更就追加新迁移。
- 模块之间**只通过 `shared` 下的接口或公开的服务方法**交互，不直接读写别的模块的表。
- 需要新的跨模块契约时，在 `shared` 下加接口并在报告里说明，由集成方审查。
- **不要修改 `pom.xml`**。需要新依赖时在报告里说明理由，由集成方统一添加，避免并行冲突。

## 租户隔离（硬性要求）

每张租户数据表必须：

1. 有 `tenant_id uuid NOT NULL` 列；
2. 按下面四行启用行级安全（`FORCE` 让策略对表所有者同样生效）：

```sql
ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <t> FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON <t>
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
```

3. 业务访问一律包在 `TenantScope.call/run` 里；
4. 至少一条**跨租户负面测试**：B 读不到 A 的数据、B 写 A 的数据被数据库拒绝。

控制平面表（租户登记、引擎实例登记等）不做行级隔离，但运行期账号的权限要收到最小。只追加的表（审计、账本流水）要 `REVOKE UPDATE, DELETE`。

## 跨端常量的一致性（硬性要求）

同一条限制落在平台（Java）、网关（Python）、插件（PHP）三处时，**不许只靠注释互指**。注释挡不住漂移，而这类漂移全是静默失败：三处不一致时没有任何测试会红，只有真实引擎在特定数据下才暴露。

两张对照表，都跟着网关单测跑（不需要 JVM、不需要 PHP）：

| 对照表 | 管什么 |
|---|---|
| `platform/tools/publish-gateway/tests/test_plugin_registry_parity.py` | 注册表：`STRUCTURED_THEMES`、`MANAGED_ATTRIBUTES` |
| `platform/tools/publish-gateway/tests/test_limit_parity.py` | **数值上限常量**：节点数、层级、行数、列数、单元格长度、批量上限、密钥长度、时间窗…… |

加一条跨端上限时，在 `test_limit_parity.py` 的 `LIMIT_GROUPS` 里加一行，写清 `name`、`why` 和各端落点：

```python
LimitGroup(
    name="字典节点数上限",
    why="快照随定义下发到引擎，和 1 MiB 的定义信封绑死（ADR 0019 决定 3）",
    sides=(
        GatewayConstant("pubgw.questions.theme_kit", "MAX_DICTIONARY_NODES"),
        PhpConstant("MjyDictionaryStore.php", "MAX_NODES"),
        JavaConstant("dictionary/DictionaryLimits.java", "MAX_NODES"),
    ),
)
```

规则默认是 `SAME`（各端取值相同）；`DECREASING` 表示按声明顺序严格递减，用于「外层信封必须给内层留余量」这类关系（网关请求体上限 > 平台定义上限）。

三条底线：

- 常量**找不到**（改名、挪走、删掉）必须转红，不能当成「这一端没有这个限制」跳过；
- 取值读不懂（引用了别的常量）就抛，不猜；
- 检查自己要有「它确实会红」的用例——`LimitParityGoesRedTest` 把真实源码文本里的某个数字人为改掉，断言检查必须报出来。一个永远不会红的一致性检查毫无价值。

## 其他

- 金额一律用 `Money`（最小币种单位整数），不用浮点。
- 写操作支持幂等键；资金、额度、权限、名额一律服务端判定。
- 先写测试，确认失败，再实现（TDD）。测试名说明被测行为。
- 注释与文档用中文，标识符用英文；单文件不超过 800 行，函数尽量不超过 50 行。
- 密钥与口令只从环境变量读取，不进代码、不进日志。
