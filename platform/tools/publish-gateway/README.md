# publish-gateway（发布网关原型）

把平台的问卷定义发布到 LimeSurvey 引擎，并保证**要么完整上线、要么什么都没发生**。
设计决定与实测证据见 [ADR 0009](../../docs/adr/0009-publish-gateway.md)，
引擎侧的前置事实见 [ADR 0005](../../docs/adr/0005-publishing.md)。

纯标准库，Python 3.9 起可用。

## 链路

```
validate → compile → import → apply → activate → verify → bind
                                ↑                    ↑
                          失败即 delete_survey 回滚 ──┘
```

| 阶段 | 做什么 | 为什么引擎不管 |
|---|---|---|
| `validate` | 题型形状、代码合法性、继承设置、选项缺失 | `activate_survey` 不跑 `checkQuestions()` |
| `compile` | 定义 → `.lss`，所有设置写显式值 | `surveys_groupsettings` 不随 LSS 走 |
| `import` | `import_survey` | — |
| `apply` | 回读问卷设置与题型主题；**激活前**先核对一遍结构 | 导入端会静默改名、静默降级主题 |
| `activate` | `activate_survey`（＋参与者名单） | 导入永远产出未激活问卷 |
| `verify` | 再读一次 `get_fieldmap`，算结构指纹 | 激活本身也可能改变结构 |
| `bind` | 产出绑定记录交给平台存档 | — |

## 用法

```bash
cd platform/tools/publish-gateway
export LIMESURVEY_RPC_PASSWORD=...        # 口令只走环境变量

python3 -m pubgw.cli validate    --definition survey.json
python3 -m pubgw.cli compile     --definition survey.json --out survey.lss
python3 -m pubgw.cli publish     --definition survey.json \
    --engine-url http://localhost --engine-instance survey-prod-a \
    --binding-out binding.json
python3 -m pubgw.cli drift-check --binding binding.json --engine-url http://localhost
```

退出码：`0` 通过；`1` 发布失败／校验不通过／检出漂移；`2` 配置或定义本身有问题。

## 作为服务运行

平台通过内部 HTTP 接口调用网关，契约见
[`platform/contracts/publish-gateway-v1.md`](../../contracts/publish-gateway-v1.md)：
`POST /v1/publish`（HMAC 认证）与 `GET /healthz`；向后兼容增补
[v1.2](../../contracts/publish-gateway-v1.2.md) 加了 `POST /v1/close`（收口被取代的版本）与
`POST /v1/drift-check`（只读漂移检查）。

```bash
python3 -m pubgw.server                      # 或者用本目录的 Dockerfile
docker build -t survey-publish-gateway platform/tools/publish-gateway
```

| 环境变量 | 说明 |
|---|---|
| `PUBGW_SHARED_SECRET` | 与平台 `PLATFORM_PUBGW_SECRET` 相同，至少 32 字节；缺失或过短拒绝启动 |
| `PUBGW_ENGINES_CONFIG` | 引擎实例配置 JSON 的路径（必填） |
| `PUBGW_STATE_DIR` | 幂等结果 SQLite（`publish-results.sqlite3`）所在目录（必填；镜像里是 `/var/lib/pubgw`，应挂持久卷） |
| `PUBGW_HOST` / `PUBGW_PORT` | 监听地址与端口，缺省 `127.0.0.1:8080`（镜像里是 `0.0.0.0:8080`） |
| `PUBGW_RESULT_TTL_SECONDS` | 完整回执的留存期，缺省 `604800`（7 天），**下限 86400**（24 小时） |
| `PUBGW_TOMBSTONE_TTL_SECONDS` | 墓碑的留存期，缺省 `7776000`（90 天），不得短于回执留存期 |
| 配置里 `passwordEnv` 指名的变量 | 各实例的引擎管理员口令 |

引擎实例配置（口令**只**走环境变量，文件里出现口令字段直接拒绝启动）：

```json
{
  "hd-engine-01": {
    "rpcUrl": "http://engine-01/index.php/admin/remotecontrol",
    "user": "admin",
    "passwordEnv": "PUBGW_ENGINE_HD01_PASSWORD"
  }
}
```

`rpcUrl` 是完整的 RemoteControl 端点。任何配置错误都以退出码 `2` 拒绝启动。

结果存档的留存期（契约 v1.3）：

- **回执期**（缺省 7 天）内，同一 `requestId` 重放拿到首次应答，逐字节一致。
- 之后只剩**墓碑**（请求指纹、原状态码、落库时刻、引擎 sid；应答体已丢弃），重放是
  `410 result_expired`——一个明确的终局。平台据此记发布失败并把 `expired.surveyId` 按孤儿问卷存档，
  **不会**误判成"没发过"而重复发布。
- 墓碑期（缺省 90 天）过后整行删除，同一 `requestId` 会被当成新请求真的再发布一次。
  所以墓碑期必须长于平台任何可能的重试视野，人工复核要在这之前收尾。
- 判过期只看 `created_at`，与清理任务无关：`ResultStore.get` 自己算，所以清理晚跑一小时
  也不会多返回一个字节。进程内一个守护线程每小时清一次（只删行，启动后立刻清第一轮）。
- **回收磁盘是显式的维护动作**，不在自动路径上：

  ```bash
  PUBGW_STATE_DIR=/var/lib/pubgw python3 -m pubgw.cli prune-results
  ```

  `VACUUM` 要独占锁并重写整个文件；每小时来一次会和正常请求抢锁，并发的 `put` 等满 30 秒后抛错——
  而**发布成功之后 `put` 失败等于结果没落库，平台用同一 `requestId` 重试就会重复发布**。所以
  这条命令由运维在维护窗口里跑，配置读的是同一套环境变量。
- 诚实边界：不跑 `prune-results` 时，删掉的行留下的空闲页会被后续写入复用，旧正文字节在被覆盖前
  仍在文件里；`VACUUM` 抹的也只是主库文件，WAL 残留、文件系统未擦除的块、以及**任何数据库备份**
  都不受留存期约束——真要按期销毁，备份策略得一起管。
- 如果 `PublishResult` 将来要带凭据（例如把引擎生成的邀请码回读进回执，见 ADR 0016 缺口），
  回执期就是那批凭据的明文存活期，必须按小时而不是按天设，并单独在契约里写清楚。

行为要点：

- 状态码映射：`ok` → 200；`failedStage` 为 `validate`／`compile`（引擎未被触碰）→ 422；
  其余失败 → 502（带 `rolledBack`／`orphanSurveyId`）。引擎登录是惰性的，
  前置校验不通过的定义一次引擎调用都没有。
- 幂等：200／422／502／500 都按 `requestId` 落库，重复请求原样重放、不再发布；
  同一 `requestId` 带着不同内容重放 → 400。
- 并发：同一 `requestId` 或同一 `(实例, definition.uuid)` 正在发布 → 409，
  锁覆盖整个发布过程。锁在进程内，**只支持单副本部署**。
- 响应里的引擎口令（原文、repr、JSON 转义）一律替换为 `***`；堆栈只进服务端日志。
- SIGTERM 时等在途发布做完再退出，编排里的停止宽限期要长于单次发布。

样例定义：[`platform/tests/fixtures/surveys/publish-gateway.json`](../../tests/fixtures/surveys/publish-gateway.json)。

## 访问策略（`policy` 块）

定义（v1、v2 均可）可带 `policy`：时间窗（本地时刻＋IANA 时区）、访问密码（只收平台生成的
PBKDF2 哈希）、验证码、邀请码、按 token／设备／IP 限次、作答时长、IP 与地区规则。验证码与邀请码
编成引擎原生设置；其余编成一行 `plugin_settings` 交给 `MjyRuntimePolicy` 执行，激活前经
`policyStatus` 回读核对摘要，插件没激活或摘要不符即回滚。字段与问题代码见
[`platform/contracts/survey-access-policy-v1.md`](../../contracts/survey-access-policy-v1.md)，
设计见 [ADR 0016](../../docs/adr/0016-access-policy.md)。

```bash
# 端到端：真引擎上的时间窗、密码、限次、时长、验证码、IP（约两分钟，含两次服务端计时等待）
platform/deploy/test/run-access-policy.sh
TEST_DB=pgsql platform/deploy/test/run-access-policy.sh
```

## 问卷逻辑（definitionVersion 2）

v2 定义可以带显示条件（题目／题组 `condition`）、校验规则（`validation`）、计算值
（`type: "*"` ＋ `calculation`）与文本里的答案引用（`{{ 表达式 }}`）。表达式用平台自己的
小型 DSL 书写，按题目代码或 `q("uuid")` 引用，由 `pubgw/logic/` 解析、做类型／引用／顺序／
循环检查（不通过即 422 `validate`），再编译成 ExpressionScript 的受限子集写进 LSS。
**v2 定义里不允许直写引擎表达式**（`relevance`、`em_validation_q`、`equation` 等）。
v1 定义的校验与编译结果逐字节不变。

文法、类型、函数、编译产物与错误码见
[`platform/contracts/survey-logic-dsl-v1.md`](../../contracts/survey-logic-dsl-v1.md)；
样例：[`platform/tests/fixtures/surveys/publish-gateway-logic.json`](../../tests/fixtures/surveys/publish-gateway-logic.json)。

## 模块

| 模块 | 职责 |
|---|---|
| `model.py` | 定义的解析与不可变数据模型（系统边界） |
| `qtypes.py` | 题型形状表：一道题该产生哪些答卷列 |
| `questions/` | WP-02 题型扩展：题型专属校验、`format`／`maxLength`／`exclusive` 编译成服务端规则 |
| `codes.py` | 引擎的代码合法性规则（镜像自 `Question.php` / `Answer.php`） |
| `validate.py` | 发布前校验，一次性列出全部问题 |
| `compiler.py` | 定义 → `.lss`，并算出编译期结构指纹 |
| `rpc.py` | RemoteControl 客户端，传输层可替换 |
| `fieldmap.py` | `get_fieldmap` 归一化、结构指纹、三段映射 |
| `verify.py` | 回读校验（改名、缺列、顺序） |
| `publish.py` | 七阶段编排与回滚 |
| `binding.py` | 绑定记录 |
| `drift.py` | 发布后的漂移检查 |
| `cli.py` | 命令行入口 |
| `auth.py` | 平台请求的 HMAC 认证 |
| `engines.py` | 引擎实例配置（口令只来自环境变量） |
| `request.py` | `POST /v1/publish` 请求体解析 |
| `ops_request.py` | `POST /v1/close`、`POST /v1/drift-check` 请求体解析（v1.2） |
| `close.py` | 收口旧版本：设过期时间，不停用、不删除（v1.2） |
| `drift_check.py` | 按需漂移检查：回读并对照期望指纹与绑定记录（v1.2） |
| `store.py` | 幂等结果存储（SQLite）与在途锁 |
| `service.py` | 发布接口的业务语义（认证、幂等、并发、状态码映射） |
| `server.py` | HTTP 外壳与服务入口 |
| `logic/parser.py` | 逻辑 DSL 词法与递归下降解析（不 eval） |
| `logic/scope.py` · `logic/types.py` | 引用解析（代码／UUID → qcode 变量）与类型检查 |
| `logic/graph.py` · `logic/check.py` | 前向／跨页引用、循环依赖；汇总成校验问题 |
| `logic/emit.py` · `logic/lower.py` | 语法树 → ExpressionScript；v2 定义 → 引擎层定义 |
| `policy/schema.py` · `policy/timewindow.py` · `policy/password.py` | 访问策略 `policy` 块的校验（422 `E_POLICY_*`）、本地时刻＋时区 → UTC、密码哈希格式（ADR 0016） |
| `policy/compile.py` | 策略 → 原生设置（`usecaptcha`／`access_mode`／`startdate`／`expires`）＋ LSS `plugin_settings` 载荷与摘要 |
| `policy/probe.py` | 激活前向 MjyRuntimePolicy 的 `policyStatus` 回读策略摘要，不符即回滚 |

## 测试

```bash
# 单元测试：不需要运行中的引擎（传输层被替换成假引擎）
cd platform/tools/publish-gateway && python3 -m unittest discover -s tests -t .

# 端到端：真引擎、两种数据库
platform/deploy/test/run-publish-gateway.sh
TEST_DB=pgsql platform/deploy/test/run-publish-gateway.sh

# 端到端：逻辑（条件、隐藏必答清值、校验、计算值、引用转义）在真引擎上的行为
platform/deploy/test/run-publish-gateway-logic.sh
TEST_DB=pgsql platform/deploy/test/run-publish-gateway-logic.sh

# 端到端：网关作为服务容器，经 HTTP 发布到真引擎
platform/deploy/test/run-publish-gateway-service.sh
TEST_DB=pgsql platform/deploy/test/run-publish-gateway-service.sh
```

## 当前边界

- 只支持 `L ! M P O 5 Y G F H A B C E 1 : ; S T U Q N K D R | X *` 这些题型（`*` 计算值只能来自 v2 的
  `calculation`）；其余一律在校验阶段拒绝，而不是「放过去再说」。题型映射、扩展键与缺失值见
  [`platform/docs/p2/question-type-map.md`](../../docs/p2/question-type-map.md)，真引擎验证
  `platform/deploy/test/run-question-types.sh`（`TEST_DB=mysql|pgsql`）。
- 回滚动作是 `delete_survey`，它连答卷一起删，因此**只适用于从未接收过答卷的
  新发布**。已上线问卷的结构升级不能走这条路。
- 指纹只覆盖 `get_fieldmap` 暴露的维度：答案选项的 `code` 变更不在其中。

完整的限制清单见 ADR 0009 的「已知限制与后续」。
