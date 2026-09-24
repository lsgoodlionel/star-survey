# 业务平台开发约定（P1 起适用）

## 构建与测试

```bash
PLATFORM_DB_NAME=<你的库名> platform/deploy/platform-dev/mvn.sh test
```

本机不需要 Java，Maven 在容器里运行，依赖走阿里云镜像并缓存在命名卷。每条并行车道用自己的数据库名，互不干扰。

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

## 其他

- 金额一律用 `Money`（最小币种单位整数），不用浮点。
- 写操作支持幂等键；资金、额度、权限、名额一律服务端判定。
- 先写测试，确认失败，再实现（TDD）。测试名说明被测行为。
- 注释与文档用中文，标识符用英文；单文件不超过 800 行，函数尽量不超过 50 行。
- 密钥与口令只从环境变量读取，不进代码、不进日志。
