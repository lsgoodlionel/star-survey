# ADR 0005：问卷发布与结构漂移检测

- 状态：原型证据已完成，两种数据库端到端验证通过；发布网关待实现
- 日期：2026-09-20
- 关联：P0-00.6；依赖 00.3 题型纵切；总方案 §4.2
- 配套证据：[P0 LSS 字段覆盖面](../p0/lss-coverage.md)、`platform/tests/e2e/publish_roundtrip.php`

## 背景

平台侧的发布流水线设想是：平台 DSL → 生成 `.lss` → RemoteControl `import_survey` →
`activate_survey` → `get_fieldmap` 校验 → 切换路由。这条链路上有三个未知数必须先用引擎实测定死：

1. `.lss` 到底携带哪些内容，哪些必须由平台另行下发；
2. 同一份 `.lss` 反复导入时，`get_fieldmap` 里哪些东西稳定、哪些不稳定，
   决定平台的题目 UUID 映射能用什么作键；
3. 问卷激活之后引擎还允许改什么，平台怎么发现这些“绕过平台”的改动。

## 决定

### 1. 发布单元是 `.lss`，但 `.lss` 不是完整发布包

`.lss` 只覆盖问卷结构（题组、题目、子题、选项、属性、配额、条件、默认值、语言设置、
主题配置值）。参与者名单、答卷、权限、属主、激活态、全局题型主题、主题文件、标签集、
问卷组设置**都不在其中**（逐条代码位置见覆盖面文档）。

因此平台的“发布包”定义为：

```
发布包 = .lss（结构）
       + 引擎镜像保证的前置条件（问卷主题、题型主题已安装）
       + 发布后的引擎侧动作（activate_survey、activate_tokens、add_participants）
       + 平台侧持有的权限模型（引擎权限不参与）
```

### 2. 问卷设置一律写显式值，不写 `I`（继承）

`lime_surveys_groupsettings` 不随 `.lss` 导出，`surveys_groups` 小节在导入端只用来按组名
**查找**已有问卷组（`import_helper.php:2293-2308`），不会创建。于是值为 `I` 的设置会在目标
实例解析成目标侧的组默认值——同一份 `.lss` 在不同实例上行为不同。生成器必须把所有影响
作答行为的设置落成显式值。

### 3. 题目 UUID 映射以“题目代码＋子题代码＋尺度”为键，不用任何数字 id

实测（见下）：同一份 `.lss` 导入两次，`sid`、`gid`、`qid`、`sqid` 以及由它们派生的字段名
全部不同；题目代码 `title`、子题代码 `aid`、题型 `type`、尺度 `scale_id` 逐条相同。

**但代码不是绝对安全的键**：导入端在代码非法或冲突时会自动改名
（`import_helper.php:2711`、`:2878`）。所以发布网关在 `import_survey` 之后必须用
`get_fieldmap` 回读，把代码集合与提交的 DSL 做等值校验，不一致即判定发布失败并回滚
（`delete_survey`），绝不进入路由切换。

### 4. 结构指纹＝归一化 `get_fieldmap`

指纹的输入是 `get_fieldmap` 每一条按原顺序取 `type | title | aid | scale_id` 拼成的行，
再整体 SHA-256 取前 16 位。**刻意排除** `sid`、`gid`、`qid`、`sqid`、`fieldname`、
`questionSeq`、`groupSeq` 等数字量，也排除 `question`、`help`、`group_name`、`mandatory`
等文案与行为属性。这样：

- 指纹在跨实例、跨数据库、跨导入批次之间可比（实测 MariaDB 与 PostgreSQL 得到同一指纹）；
- 纯文案修订不会误报；
- 任何改变作答数据语义的结构变更都会报出来。

发布成功时把指纹存进平台的问卷版本记录，之后周期性回读比对即可检出漂移。

### 5. 激活是独立一步，且平台必须自己做“答案选项”校验

`import_survey` 永远产出未激活问卷（`import_helper.php:2256`）。
RemoteControl 的 `activate_survey` 只跑 `checkHasGroup()` 与 `checkGroup()`
（`remotecontrol_handle.php:573-575`），**不跑** `checkQuestions()`——后者才是检查
单选/多选/数组等题型是否配了答案选项的那一步（`application/helpers/admin/activate_helper.php:131`）。
实测一道没有任何选项的单选题可以被 RPC 顺利激活。平台生成器必须自行拦截这种问卷。

### 6. 发布失败一律 `delete_survey` 回滚

一致性检查失败时引擎在建表之前就返回，不留半发布状态；`delete_survey` 会连同答卷表、
参与者表、结构行与权限行一起清掉（实测见下）。所以发布网关的失败处理只需要一个动作。

## 验证

`platform/deploy/test/run-publish-roundtrip.sh`（`TEST_DB=mysql|pgsql`），
fixture 为 `tests/data/surveys/survey-dual-scale-question-api-test.lss`
（7 道父题、10 道子题，含双尺度题型 `1`，覆盖 `scale_id` 0/1）。
四个场景在 MariaDB 10.11 与 PostgreSQL 16 上**全部通过**。

### 场景一：LSS 覆盖面

先导入 → 激活 → `activate_tokens` → `add_participants`，再导出，确认这些数据确实存在却仍不被携带。

| 断言 | 结果 |
|---|---|
| 必带小节齐全 | 通过 |
| 不带小节确实缺席（令牌、参与者、权限、标签集、题型主题、问卷组设置、全局设置等 16 项） | 通过 |
| 参与者表有 1 行但导出无 `tokens` 小节 | 通过 |
| 问卷权限有 12 行但导出无 `permissions` 小节 | 通过 |
| 全局题型主题表有 37 行、导出无 `question_themes`，但题目行保留 `question_theme_name` | 通过 |
| 导出的 `surveys` 行不含 `active` / `owner_id` / `datecreated` | 通过 |
| 把导出的 LSS 再导入一次，得到的是**未激活**问卷且没有答卷表 | 通过 |

实测导出小节与逐节行数见[覆盖面文档](../p0/lss-coverage.md#实测行数对照)。

### 场景二：映射稳定性

同一份 `.lss` 连续导入两次（MariaDB 上 sid 343732 与 149315）：

```
A: id submitdate lastpage startlanguage seed Q375_S382#0 Q375_S382#1 Q376_S383#0 Q376_S383#1 …
B: id submitdate lastpage startlanguage seed Q392_S399#0 Q392_S399#1 Q393_S400#0 Q393_S400#1 …
```

| 观察项 | 跨导入是否稳定 |
|---|---|
| `sid`、`gid`、`qid`、`sqid` | **不稳定**，两次的 qid 集合完全不相交 |
| `fieldname`（形如 `Q<qid>_S<sqid>#<scale>`） | **不稳定**，逐条不同 |
| `title`（题目代码） | 稳定 |
| `aid`（子题代码 / `other` / `comment`） | 稳定 |
| `type`、`scale_id` | 稳定 |
| 归一化签名与指纹 | 稳定，且 MariaDB 与 PostgreSQL 同为 `bccc3492a4f0e563` |

归一化签名样例（同一段在两次导入、两种数据库上完全一致）：

```
1|Q00|SQ001|0
1|Q00|SQ001|1
1|G01Q02|SQ001|0
1|G01Q02|SQ001|1
1|G01Q02|SQ002|0
1|G01Q02|SQ002|1
```

**关键结论**：LimeSurvey 7 的答卷列名是 `Q<qid>` 系列，只由数字主键决定，与题目代码无关
（`application/helpers/common_helper.php:1759`）。平台绝不能把字段名当稳定标识，
必须走「平台题目 UUID ↔ 题目代码 ↔ 引擎字段名」三段映射，并在每次发布后重建第三段。

### 场景三：激活后漂移面

问卷激活后逐项尝试，记录引擎应答与变更后的指纹（两种数据库结果一致）：

| 操作 | RemoteControl 应答 | 是否被接受 | 指纹是否变化 |
|---|---|---|---|
| 改题干文本 `question` | `{"questionl10ns":{"en":{...true}}}` | 接受 | 否 |
| 改必答 `mandatory` | `{"mandatory":true}` | 接受 | 否 |
| **改题目代码 `title`** | `{"title":true}` | **接受** | **是**（`14473790952867bd` → `6bc4ae9e8cf46bf4`） |
| **改子题代码 `title`** | `{"title":true}` | **接受** | **是**（→ `20e03d9c59df3173`） |
| 改题型 `type` | `ERR_NO_DATA` | 拒绝 | 否 |
| `import_question` | `ERR_SURVEY_ACTIVE` | 拒绝 | 否 |
| `add_group` | `ERR_SURVEY_ACTIVE` | 拒绝 | 否 |
| `delete_question` | `ERR_SURVEY_ACTIVE` | 拒绝 | 否 |
| 改问卷联系人 `admin` | `{"admin":true}` | 接受 | 否 |
| 改匿名化 `anonymized` | `ERR_NO_DATA` | 拒绝 | 否 |
| 改布局 `format` ＋ `questionindex` | `{"format":true,"questionindex":true}` | 接受 | 否 |

引擎侧的守卫分布很不均匀：

- `import_question`（`remotecontrol_handle.php:1608`）、`add_group`、`delete_group`、
  `delete_question` 都有 `isActive` 守卫；
- `set_survey_properties` 只在激活时摘掉 `anonymized`、`datestamp`、`savetimings`、
  `ipaddr`、`refurl`（`remotecontrol_handle.php:504-511`）；
- **`set_question_properties` 与 `set_group_properties` 完全没有 `isActive` 守卫**。
  `set_question_properties` 只摘掉 `qid`/`gid`/`sid`/`parent_qid`/`language`/`type`
  （`remotecontrol_handle.php:2034-2039`），`title` 不在其中。
- 后台界面同样如此：`QuestionAggregateService::checkDeletePermission()`（`:202`）和
  `SubQuestionsService::delete()`（`:77`）只挡删除，
  `SubQuestionsService::storeSubquestion()` 对已存在的子题无条件执行
  `$subquestion->title = $data['code']`（`:178`），激活状态下照样改名。

**这次漂移是“静默”的**：因为字段名由 qid 派生，改代码不会改动答卷表的列。
实测断言「代码漂移不改动答卷表列」通过——这正是危险所在：库层面看不出任何异常，
只有平台的代码映射悄悄失配，导出的答卷表头和平台的题目语义对不上。指纹是目前唯一
低成本的检出手段。

### 场景四：激活失败与清理

| 断言 | 结果 |
|---|---|
| 无题组问卷激活 → `ERR_CONSISTENCY_CHECK` | 通过 |
| 有题组但无题目 → `ERR_CONSISTENCY_CHECK` | 通过 |
| 激活失败后：没有 `lime_responses_<sid>`，`surveys.active` 仍为 `N` | 通过 |
| 删掉单选题全部选项后仍被 RPC 激活（引擎缺口，见决定 5） | 通过（记录缺口） |
| 删除已激活问卷 → 答卷表与参与者表一并消失 | 通过 |
| 删除问卷 → 题组/题目/l10n/属性/选项/语言设置/权限行全部归零 | 通过 |

## 对发布流水线的影响

```
DSL ──生成──> .lss
  │  ① 设置不得留 "I"；主题名与题型主题名必须在目标实例存在
  │  ② 生成前自检：需要选项的题型必须有选项（引擎 RPC 不查）
  ├─ import_survey ─> 未激活问卷（sid 由引擎分配）
  │  ③ 立即 get_fieldmap 回读，校验代码集合 == DSL；不一致 → delete_survey 回滚
  ├─ activate_survey
  │  ④ 再次 get_fieldmap，计算并存档结构指纹；重建「题目代码 → 字段名」映射
  ├─ activate_tokens + add_participants（名单不在 .lss 里）
  └─ 路由切换
        ⑤ 周期性重算指纹比对，检出激活后的代码漂移
```

## 已知限制与后续

- [ ] 指纹只覆盖 `get_fieldmap` 暴露的维度。答案选项的 `code` 变更不进入 `get_fieldmap`
      （选项只影响取值域不影响列），需要单独的选项字典校验。
- [ ] 后台 UI 的题目/子题改名路径已在代码中定位，但尚未做 HTTP 层实测；
      当前证据来自 RemoteControl 与源码阅读。
- [ ] `checkQuestions()` 未被 RPC 激活路径调用，属于引擎缺口。备选方案：平台自校验（已选），
      或登记一条核心补丁让 `activate_survey` 也跑 `checkQuestions()`。
- [ ] 发布网关本身（幂等、并发发布同一问卷、回滚时的答卷保护）未实现。
      注意 `delete_survey` 会连答卷一起删，回滚只能用于“从未接收过答卷”的新发布。
- [ ] 重新发布（已激活问卷的结构升级）没有验证。引擎在激活后禁止增删题目，
      现实路径只有「停用 → 改 → 重新激活」，而这会重建答卷表并轮换代次
      （[ADR 0003](0003-runtime-events.md)），必须与事件链路一起设计。
- [ ] `.lss` 生成器与题型覆盖依赖 P0-00.3 的题型纵切结论。
- [ ] 多语言问卷只验证了两种语言的 l10n 行数一致，未做语言级内容校验。
