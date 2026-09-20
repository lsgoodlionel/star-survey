# ADR 0009：发布网关

- 状态：原型已实现，MariaDB 10.11 与 PostgreSQL 16 端到端全通过；生产网关待实现
- 日期：2026-09-20
- 关联：P0-00.8；依赖 [ADR 0005](0005-publishing.md) 的引擎事实与 [ADR 0006](0006-question-types.md) 的题型承载；总方案 §4.2
- 配套证据：`platform/tools/publish-gateway/`（152 个单元用例）、
  `platform/tests/e2e/publish_gateway.py`、`platform/deploy/test/run-publish-gateway.sh`

## 背景

[ADR 0005](0005-publishing.md) 把发布链路上的三个未知数用引擎实测定死了：
`.lss` 携带什么、什么东西跨导入稳定、激活之后引擎还允许改什么。它留下的结论是一句话：

> 引擎会**静默**地把平台提交的东西改掉，而且不会告诉平台。

具体有三处静默：

1. **导入时改题目代码**。代码非法或冲突时自动改名（`import_helper.php:2646-2668`、`:2798-2822`），
   改名只写进 `importwarnings`，而 RemoteControl 的 `import_survey` **只返回新的 sid**，
   警告全部丢弃。
2. **激活时不查选项**。`activate_survey` 只跑 `checkHasGroup()` 与 `checkGroup()`
   （`remotecontrol_handle.php:573-575`），不跑 `checkQuestions()`，
   一道没有任何选项的单选题会被顺利激活成一道无法作答的题。
3. **导入时换题型主题**。目标实例没装该主题时引擎换成基础主题，既不报错也不写警告
   （`Question.php:1531-1556`，[ADR 0006](0006-question-types.md) 决定 6）。

再加上 `.lss` 不携带问卷组设置，值为 `I` 的设置会在目标实例解析成**目标侧**的默认值。

所以平台不能把「发布」理解成「调一次 `import_survey` 再调一次 `activate_survey`」。
本 ADR 决定发布网关长什么样，并用一个可运行的原型把每一条都验证过。

## 决定

### 1. 发布是七个阶段，任何一个失败都回到「什么都没发生」

```
validate → compile → import → apply → activate → verify → bind
                       └──────────── 失败即 delete_survey 回滚 ────┘
```

| 阶段 | 动作 | 挡住什么 |
|---|---|---|
| `validate` | 题型形状、代码合法性、继承设置、选项/子题缺失 | 引擎压根不查的那些 |
| `compile` | 定义 → `.lss`，所有设置写显式值 | 继承带来的跨实例行为差异 |
| `import` | `import_survey` | — |
| `apply` | 回读问卷设置与题型主题；**激活前**先把结构核对一遍 | 静默改名、静默换主题、设置被吃掉 |
| `activate` | `activate_survey`（＋ `activate_tokens`／`add_participants`） | 导入永远产出未激活问卷 |
| `verify` | 再读一次 `get_fieldmap`，算结构指纹 | 激活过程本身改变了结构 |
| `bind` | 产出绑定记录交给平台存档 | — |

**结构核对做两遍是刻意的。** 第一遍在 `activate_survey` 之前：这时引擎还没建答卷表，
回滚最干净。第二遍在激活之后：确认激活这一步没有动过结构，并把最终指纹存档。

引擎的失败应答有**四**种形状，客户端必须逐种识别，其中第四种最阴险：
`add_participants` 返回的是一个列表，没建成的参与者在**自己那一条里**多一个
`errors` 键（`remotecontrol_handle.php:2130-2134`），外层看起来完全正常。
只看「返回值是不是带 status 的字典」会把它当成功。

### 2. 校验用引擎自己的规则，而且比引擎更严

代码规则镜像自 `application/models/Question.php` 与 `Answer.php`
（实现见 `pubgw/codes.py`）。两处刻意比引擎更严：

- **题目代码**。引擎的模式是 `/^[a-z,A-Z][[:alnum:]]*$/`（`Question.php:271`），
  字符组里的逗号是笔误，结果首字符允许写成 `,`。平台只允许字母开头。
- **子题代码**。引擎的模式是 `/^[a-zA-z0-9]*$/`（`Question.php:293`），
  `A-z` 覆盖了 ``[ \ ] ^ _ ` ``，而且允许空串。平台只允许非空的字母数字。

宁可自己更严：引擎接受但形状古怪的代码会在别处（ExpressionManager、导出表头）出问题，
而平台这边一旦被改名就再也对不上了。

### 3. 只支持列形状明确的题型，其余一律拒绝

答卷表的列由 `createFieldMap()` 按题型硬编码生成
（`application/helpers/common_helper.php:1704` 起），没有任何数据可以查询。
原型把 `L ! M P F 1 S T U N D X` 这 12 个题型的列形状写成一张表（`pubgw/qtypes.py`），
表外的题型在 `validate` 阶段直接拒绝。

「先放过去，出问题再说」在这里是最糟的选择：一道没人核对过列形状的题目，
发布时看起来一切正常，只有导出答卷时才发现对不上。

**实测修正了一条原本的想当然**：说明文字题（`X`）不收集任何作答，但引擎照样给它一列——
`createFieldMap()` 的「无子题」分支把 `X` 和普通文本题一视同仁
（`common_helper.php:1759`）。绑定记录必须照实记这一列。

### 4. 结构指纹＝归一化 `get_fieldmap`，但排除答卷表固有列

沿用 [ADR 0005](0005-publishing.md) 决定 4 的口径：每条题目行取
`题型|题目代码|aid|尺度`，按引擎返回的原顺序拼接，SHA-256 取前 16 位。
**与 00.6 的口径有一处收紧**：只取带 `qid` 的行，排除 `id` / `submitdate` /
`token` / `startdate` 等答卷表固有列。

原因是 00.6 的口径有个实际问题：`activate_tokens` 会往 fieldmap 里加一列 `token`，
于是「给问卷建了个参与者表」这件与结构无关的事会让指纹漂移，产生假警报。
指纹带算法版本前缀（当前 `fm1:`），换口径时旧绑定记录不会被误判。

指纹是**双向**的：编译期从定义算一次，回读时从 `get_fieldmap` 算一次，两者必须相等。
这样编译器本身出 bug 也会被发现（见验证场景三）。

### 5. 回滚只有一个动作，而且只对新发布安全

失败时调 `delete_survey`，它连答卷表、参与者表、结构行与权限行一起清掉。
引擎的一致性检查在建表之前就返回，所以不存在「半张答卷表」。

**但 `delete_survey` 会连答卷一起删**，所以这条回滚路径只对「从未接收过答卷的新发布」
安全。已上线问卷的结构升级不能走这条路（见「已知限制」）。

回滚本身也可能失败。这时网关不装作没事：结果里置 `orphanSurveyId`，
失败信息以 `ROLLBACK FAILED` 开头，要求人工清理。

### 6. 绑定记录是平台侧的唯一事实来源

```json
{
  "engineInstance": "survey-test-web",
  "surveyId": 900001,
  "definitionUuid": "11111111-1111-4111-8111-111111111111",
  "compilerVersion": "pubgw-lss-1",
  "fingerprintVersion": "fm1",
  "fingerprint": "fm1:74e0d199d9839cdc",
  "language": "en",
  "publishedAt": "2026-09-20T08:30:54Z",
  "questions": [
    {
      "uuid": "33333333-0001-4111-8111-000000000001",
      "code": "QSINGLE",
      "type": "L",
      "fields": [
        {"fieldname": "Q256", "aid": "", "scale": 0},
        {"fieldname": "Q256_Cother", "aid": "other", "scale": 0}
      ]
    }
  ]
}
```

三段映射「平台题目 UUID ↔ 题目代码 ↔ 引擎字段名」里，第三段每次激活都要重建
（[ADR 0005](0005-publishing.md) 决定 3）。前两段跨实例稳定，第三段只在本次激活期内有效。

### 7. 漂移检查有两条互补的线索

- **指纹**便宜，适合周期性扫全量；只回答「变没变」。
- **字段名 → 代码**精确，适合报警。字段名在一次激活期内稳定，
  所以能指名道姓说出「题目 uuid X 的代码从 QSINGLE 变成了 QDRIFTED」。

两条都从同一次 `get_fieldmap` 回读里算出来，不额外增加调用。

## 验证

`platform/deploy/test/run-publish-gateway.sh`（`TEST_DB=mysql|pgsql`）。
引擎容器没有 Python、测试栈也没有对宿主暴露端口，所以驱动脚本跑在宿主机上，
RemoteControl 的传输层走 `docker exec survey-test-web curl`，SQL 探针走数据库容器。

四个场景在 MariaDB 10.11 与 PostgreSQL 16 上**全部通过**，
两种数据库得到**同一个指纹** `fm1:74e0d199d9839cdc`。

### 场景一：正常发布

| 断言 | 结果 |
|---|---|
| 七个阶段全部走完（`validate`→…→`bind`） | 通过 |
| `lime_surveys.active = 'Y'` | 通过 |
| 答卷表已建立 | 通过 |
| 绑定记录覆盖全部 5 道题 | 通过 |
| 绑定里的每个字段名都真实存在于答卷表 | 通过 |
| 说明文字题也占一列（引擎行为，不是平台选择） | 通过 |
| 编译期指纹 == 回读指纹 | 通过 |
| 没有触发回滚 | 通过 |

实测答卷列（MariaDB，sid 900001）：

```
id submitdate lastpage startlanguage seed startdate datestamp token
Q256 Q256_Cother Q257 Q258
Q259_S261 Q259_S261_Ccomment Q259_S262 Q259_S262_Ccomment
Q260_S263#0 Q260_S263#1 Q260_S264#0 Q260_S264#1
```

### 场景二：定义被拒 ＋ 引擎缺口对照

把样例问卷的单选题选项清空。

| 断言 | 结果 |
|---|---|
| 网关在 `validate` 阶段就拒绝，理由 `E_MISSING_ANSWERS` | 通过 |
| 引擎完全没有被碰过（`lime_surveys` 行数不变，没有产生 sid） | 通过 |
| **同一个缺陷绕过网关直接发给引擎，`activate_survey` 返回 `{"status":"OK"}`** | 通过（记录缺口） |
| 被激活的那份问卷确实一个 `lime_answers` 行都没有，但答卷表照建 | 通过 |

后两条是同一次运行里做的对照实验：证明这道闸门只能由平台来守。

### 场景三：强制改名 ＋ 回滚

用一个「编译正常但产出被改坏」的编译器，把 `QSINGLE` 换成 `9QSINGLE`。
引擎导入时按 `import_helper.php:2649-2651` 给数字开头的代码加前缀，改成 `q9QSINGLE`。

| 断言 | 结果 |
|---|---|
| 导入成功，但回读校验判负 | 通过 |
| 失败发生在 `activate_survey` **之前**（`failedStage = "apply"`） | 通过 |
| 识别出改名并报出两端：`QSINGLE → q9QSINGLE` | 通过 |
| 已回滚，`orphanSurveyId` 为空 | 通过 |
| `surveys` / `groups` / `questions` / `permissions` 行数全部归零 | 通过 |
| 没有留下 `lime_responses_<sid>` | 通过 |
| 问卷总数回到发布前 | 通过 |
| **场景一发布的那一版仍然 `active='Y'`** | 通过 |

失败信息（实测）：

```
E_CODE_RENAMED 引擎把题目代码 QSINGLE 改成了 q9QSINGLE；平台的 UUID ↔ 代码映射已经失配
E_FIELD_MISSING 题目 QSINGLE（uuid 3333…0001）缺少列 aid='' scale=0
E_FIELD_MISSING 题目 QSINGLE（uuid 3333…0001）缺少列 aid='other' scale=0
E_FINGERPRINT_MISMATCH 结构指纹不一致：编译期 fm1:74e0d199d9839cdc，引擎回读 fm1:c6a5b8b99d419936
```

**挑代码时踩到一个坑值得记下来**：一开始用的是 `Q-SINGLE!`，结果引擎先
`preg_replace("/[^A-Za-z0-9]/", '', …)` 把它修回 `QSINGLE`，与定义**恰好一致**，
发布正常通过。也就是说「引擎改名」不等于「结果一定不同」——
只有修复结果与提交值不同的改名才是危险的，而这正是回读校验能分辨的。

### 场景四：激活后漂移检测

对场景一那份已激活问卷调用 `set_question_properties` 改题目代码。

| 断言 | 结果 |
|---|---|
| 改名前漂移检查为空 | 通过 |
| 引擎接受激活后的改名（返回 `{"title":true}`） | 通过 |
| 漂移被检出（`E_CODE_DRIFT`） | 通过 |
| 指纹变化 `fm1:74e0d199d9839cdc` → `fm1:0e1235f2e2bc5a95`（两种数据库一致） | 通过 |
| 报出具体是哪道题：`(uuid, QSINGLE, QDRIFTED)` | 通过 |
| **答卷表的列一个都没变** | 通过 |

最后一条正是危险所在：库层面完全看不出异常。

### 单元测试

`platform/tools/publish-gateway/tests/`，152 个用例，不需要运行中的引擎
（RemoteControl 的传输层被替换成按定义生成应答的假引擎）：

| 文件 | 覆盖 |
|---|---|
| `test_model.py` | 定义解析的边界校验 |
| `test_qtypes.py` | 12 个题型的列形状 |
| `test_validate.py` | 引擎缺口、继承设置、题目/子题/选项代码规则 |
| `test_compiler.py` | LSS 结构、显式设置、指纹、CDATA 转义、拒绝非法定义 |
| `test_rpc.py` | 四种失败应答形状、会话、base64 编码、参与者逐条失败 |
| `test_fieldmap.py` | 归一化、指纹稳定性、三段映射 |
| `test_verify.py` | 改名、缺列、多出代码、顺序 |
| `test_publish.py` | 七阶段编排、各阶段回滚、回滚本身失败、导入返回怪形状 |
| `test_drift.py` | 无漂移、代码漂移、列消失、记录往返 |
| `test_cli.py` | 四个子命令与三种退出码 |

引擎自带 unit 套件保持绿色：MariaDB 848 用例、PostgreSQL 840 用例
（CI 口径 `--exclude-group mysql`），0 错误 0 失败，1 warning ＋ 1 risky 为基线既有。

## 已知限制与后续

1. **指纹看不见答案选项。** 选项的 `code` 变更不进入 `get_fieldmap`
   （选项只影响取值域，不影响列），改一个选项代码指纹纹丝不动。
   需要一份独立的选项字典校验，数据源只能是 `list_questions` ＋ 逐题
   `get_question_properties`，成本比指纹高一个量级。
   这是 [ADR 0005](0005-publishing.md) 遗留项里唯一没有在本轮解决的。
2. **重新发布没有实现。** 引擎在激活后禁止增删题目，现实路径只有
   「停用 → 改 → 重新激活」，而这会重建答卷表并轮换代次
   （[ADR 0003](0003-runtime-events.md)）。网关当前的回滚动作 `delete_survey`
   会连答卷一起删，**绝不能**用在已接收答卷的问卷上。生产版必须先做
   「答卷存在性检查 ＋ 拒绝回滚」这道闸门。
3. **没有幂等与并发保护。** 同一份定义连发两次会产生两个问卷；
   两个进程同时发布同一份定义不会互相阻塞。生产版需要平台侧的发布锁
   与「定义指纹 → 已发布 sid」的幂等表。
4. **题型主题只在导入后回读，没有发布前预检。** RemoteControl 没有列出
   `lime_question_themes` 的方法，所以「目标实例装没装这个主题」只能等导入完
   再用 `list_questions` 回读比对。代价是一次白跑的导入＋回滚。
   备选：给发布机器人开一个只读的主题清单接口。
5. **支持的题型只有 12 个。** 排序题 `R`、文件上传题 `|`、多项文本 `Q`、
   多项数值 `K` 这些的列形状还没写进形状表，尤其 `R` 与 `|` 在
   `createFieldMap()` 里走的是单独分支（`common_helper.php:1760` 的守卫把它们排除在
   「无子题」分支之外）。[ADR 0006](0006-question-types.md) 的 C 档题型（自增表格）
   在列形状上等同于长文本 `T`，但它的列定义与副表契约还没接进网关。
6. **多语言只发布基础语言的 l10n。** `additionalLanguages` 会写进 `languages`
   与 `surveys_languagesettings`，但题目、子题、选项的 l10n 行只写基础语言，
   其余语言在引擎里会是空的。
7. **条件/相关性、配额、评分、默认值都没编译。** 定义模型里只留了
   `relevance` 字段，`conditions` / `quota` / `assessments` / `defaultvalues`
   四个 LSS 小节完全没写。
8. **回读比对不覆盖题干文案。** 指纹刻意排除 `question` / `help`，
   所以「引擎里的题干和平台的不一样」这件事当前发现不了。
   如果平台要保证文案一致，需要第二个只比文案的校验（可以做成非阻断的告警）。
9. **端到端驱动跑在宿主机上，靠 `docker exec` 打洞。** 这是测试栈没有暴露端口
   的权宜之计，不代表生产形态——生产里网关通过 HTTP 直连引擎（`rpc.HttpTransport`）。
   这也意味着 e2e 依赖宿主机有 Python 3.9＋，与其他 P0 脚本（纯 PHP，跑在容器里）
   不一致。
10. **口令走环境变量，但会话密钥没有生命周期管理。** `get_session_key` 拿到的
    密钥在整个发布过程中复用，发布很长时可能过期；生产版需要在 `RpcError` 为
    `INVALID_SESSION_KEY` 时自动重登一次。
11. **`delete_survey` 在某些情况下会返回 `ERR_NO_PERMISSION`。** 本轮在测试库里
    遇到过一次：一份由同一个 `admin` 导入、权限行齐全（`survey` 的 `delete_p = 1`）
    的问卷，跨会话删除仍被拒。未能复现出稳定路径，也未定位到原因。
    对网关的影响是「回滚可能失败」——当前已经按失败处理（置 `orphanSurveyId`），
    但生产版需要把它查清楚，否则孤儿问卷会累积。
12. **没有发布后的冒烟渲染。** 网关只证明了结构对，没有证明问卷能打开。
    [ADR 0006](0006-question-types.md) 限制 6 提到题型主题的 twig 沙箱越界只在作答时暴露，
    这类问题当前的验证链路发现不了。
