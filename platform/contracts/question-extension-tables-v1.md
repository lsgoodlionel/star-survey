# 题型扩展副表契约 v1

适用于 `MjyQuestionExtensions` 插件写入的结构化作答副表（[ADR 0006](../docs/adr/0006-question-types.md) 决定 4 的 C 档题型）。
本文是**读端**的依据：平台的答卷读取、导出与对账都按这里描述的列与语义解释副表，
不再去看插件源码。

状态：v1（WP-02 切片 02.3 引入 `structure_version`，补上 ADR 0006 限制 9 与 10 的一半；
切片 02.4 给列字典加了 `options` 与 `distinct`，表结构不变）。

## 一、两张表

> 字典车道另加了两张表（`_dict_version` / `_dict_node`）存层级字典的快照。
> 它们不存任何作答，属于[平台字典契约](platform-dictionary-v1.md)第五节，本文不重复。

```
{prefix}mjyquestionextensions_answer_cell      一个单元格一行
  engine_instance_id  string(64)   引擎实例 id（ADR 0003 的自然键第一段）
  survey_id           integer      引擎侧 sid
  generation          string(36)   代次，来自 MjyPlatformBridge 的代次表
  response_id         integer      引擎答卷表主键
  question_code       string(64)   题目代码（不是字段名，见下）
  structure_version   string(32)   写入这一格时的结构版本
  row_index           integer      行序号，从 0 开始，连续
  column_code         string(64)   列代码，来自题目属性 mjy_table_columns
  cell_value          text NULL    归一化后的单元格值
  updated_at          datetime     UTC
  唯一索引 (engine_instance_id, survey_id, generation, response_id,
            question_code, row_index, column_code)

{prefix}mjyquestionextensions_answer_state     一次作答一行
  engine_instance_id, survey_id, generation, response_id, question_code  （同上）
  structure_version   string(32)   这次作答被判定时的结构版本
  row_count           integer      归一化后的行数（校验不通过时为 0）
  is_valid            integer      1 = 通过插件闸门；0 = 没通过
  errors              text NULL     消毒后的拒绝理由（渲染时仍需自行转义）
  updated_at          datetime     UTC
  唯一索引 (engine_instance_id, survey_id, generation, response_id, question_code)
```

`structure_version` **不在唯一索引里**：一条答卷的一道题只有一个当前版本，
`replaceRows()` 每次整体替换该答卷该题的全部单元格。

### 为什么键是题目代码而不是字段名

LimeSurvey 7 的答卷列名是 `Q<qid>`，只由数字主键决定
（`application/helpers/common_helper.php:1759`），跨实例、跨导入批次都会变
（[ADR 0005](../docs/adr/0005-publishing.md) 场景二）。字段名只在本次激活期内现算。

### 为什么带代次

停用再激活会重建 `responses_<sid>`，答卷 id 从 1 重新计数。

## 二、结构版本

### 定义

结构版本是**平台在发布时声明的一个标识**，形如 `[A-Za-z0-9][A-Za-z0-9._-]{0,31}`
（首字符必须是字母或数字）。它随 `.lss` 的题目属性 `mjy_structure_version` 下发，
由发布网关写入（`pubgw/questions/extensions.py`）。

它回答一个问题：**这些单元格是按哪一版列定义写下来的。**
换了列定义（改列代码、改列序、改类型、加列、删列）就必须换一个结构版本，
否则半年前的答卷会被按今天的列字典解释。

### 谁来定，谁来写

| 角色 | 做什么 |
|---|---|
| 平台（发布网关） | 按题目的扩展声明算出结构版本，写进 `mjy_structure_version` 属性，并在绑定记录里同时留下版本与结构摘要 |
| 插件 | 每次写副表时读这个属性，**消毒后**写进两张表的 `structure_version` 列 |
| 读端 | 先看 `answer_state.structure_version`，据此取对应版本的列字典，再解释单元格 |

插件**不信任**属性原文：`MjyStructuredAnswerStore::normaliseStructureVersion()`
把不匹配上述字符集的值一律压成 `0`（见下），属于失败关闭。

### 三个保留取值

| 值 | 含义 |
|---|---|
| `0` | **不知道是哪一版**。要么是本契约出现之前写下的行（迁移回填），要么是题目属性缺失／被改坏。读端不得猜测列字典，只能按 `column_code` 原样呈现 |
| 其他合法值 | 平台声明的版本，与绑定记录里的 `sideTables[].structureVersion` 一一对应 |

`0` 不是「第 0 版」。它是「这行的列定义已经无从考证」。

### 迁移路径

`MjyStructuredAnswerStore::ensureSchema()` 先建表、再升级：

1. 表已存在且没有 `structure_version` → `ALTER TABLE ... ADD COLUMN`，
   默认值 `'0'`；
2. 紧接着对 `NULL` 或空串的行显式 `UPDATE ... SET structure_version = '0'`
   （`DEFAULT` 在 MariaDB 上会回填既有行，在别的引擎上未必，不靠它）；
3. 刷新 schema 缓存。

升级是幂等的：列已存在时 `upgradeSchema()` 什么都不做并返回空数组。
唯一索引不含该列，所以加列不会碰索引。

`ensureSchema()` 在插件激活（`beforeActivate`）与每次首用（`ensureSchemaOnce()`）
时都会跑，因此旧实例不需要单独的迁移命令。

### 读端怎么用

```
state = fetchState(sid, generation, responseId, questionCode)
if state is null:            这道题这次作答没有被插件处理过
if state.is_valid == 0:      引擎答卷列里的原文不可信，副表是空的，不要当成「没填」
version = state.structure_version
columns = 平台侧按 (题目, version) 取到的列字典     # version == '0' 时没有字典
rows = fetchRows(sid, generation, responseId, questionCode)
```

### 列字典里有什么

绑定记录的 `sideTables[].columns` 就是这一版的列字典，每一列至少有
`code`、`label`、`type`、`required`，按类型另有：

| 键 | 出现在 | 含义 |
|---|---|---|
| `maxLength` | 文本列 | 按字符计的长度上限 |
| `min` / `max` | 数值列 | 闭区间 |
| `options` | `type: "enum"` | **取值集合**，形如 `[{"code":"1","label":"差"}]`；读端按它把单元格里的代码翻成标签 |
| `distinct` | 任意列 | `true` ＝ 同一列的非空取值在一次作答里不重复（WP-02 切片 02.4 起） |
| `dictionary` / `dictionaryVersion` / `level` | `type: "dict"` | 取值集合**不在列定义里**：它是这本字典这一版的第 `level` 层（R02-03，见 [platform-dictionary-v1](platform-dictionary-v1.md)）。读端要翻标签得去查字典 |

`type: "dict"` 是 WP-02 字典车道新增的列类型。它与 `enum` 只差一件事：取值集合太大，
放不进题目属性（行政区划有三千多个节点）。因此 `dict` 列不得声明 `options`；
引擎侧的取值集合由插件从随 `.lss` 下发的字典快照物化而来，服务端按**整条路径**判定
（单看一格判不出「这个市在不在这个省下面」）。

`options` 与 `distinct` 都算**结构**：改了可选项或唯一约束就必须换结构版本，
`structureDigest` 把两者都算进去（标签不算，改标签不影响读回）。

字典列引用的 **(字典, 版本)** 同样算结构：字典换了一版，单元格里那些代码的含义就变了，
与改了枚举选项是同一回事。它在原有八个字段**之后**追加一段 `|<字典>@<版本>`，
且只在字典列上出现，所以既有题型的 `sd1:` 摘要逐字节不变。

`fetchStructureVersions()` 返回这条答卷这道题在单元格表里出现过的全部版本。
正常只有一个；返回多个说明有人绕过 `replaceRows()` 直接写库，读端应当报警而不是合并。

## 三、语义约定

1. **校验不通过时单元格表是空的、状态表记 `is_valid=0` 与原因。**
   引擎答卷表里还留着作答者的原文，平台必须能一眼看出「这条不可信」，
   而不是看到一张空表就以为作答者没填（ADR 0006 决定 4）。
2. **`errors` 存的是消毒后的文案**，但渲染这一列的界面仍应自行转义。
3. **一次保存＝先删后插。** 行数硬上限 500（`MjyRepeatingTableValidator::HARD_MAX_ROWS`）
   把最坏情况框住；批量插入是后续优化（ADR 0006 限制 11）。
4. **`update_response`（RemoteControl）绕过所有闸门**，但会触发
   `afterSurveyDynamicSave`，插件会把这条重新判定并标成 `is_valid=0`（ADR 0006 限制 5）。

## 四、已知限制

1. 结构版本是**声明**而不是推导：在引擎后台直接改了 `mjy_table_columns`
   却没改 `mjy_structure_version`，新旧单元格会共用一个版本号。
   平台发布的问卷不允许在引擎后台改结构（与 ADR 0005 的漂移口径一致），
   发布网关的绑定记录里同时存了结构摘要 `structureDigest`，重新发布时可据此发现未申报的结构变更。
2. 副表本身仍然没有迁移版本号表；`ensureSchema()` 现在会升级列，但更复杂的
   变形（改列宽、拆表）仍需要单独的迁移（ADR 0006 限制 10 只解决了一半）。
3. 大答卷量下的写入未压测（ADR 0006 限制 11）。
