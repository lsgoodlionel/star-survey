# 问卷逻辑 DSL v1（definitionVersion 2）

WP-03 切片 03.1／03.2／03.3／03.4。平台作者端（编辑器、AI 草稿）只写本 DSL，**永远不直写引擎表达式**；
发布网关（`platform/tools/publish-gateway/pubgw/logic/`）负责校验并编译成 LimeSurvey
ExpressionScript 的受限子集。实现与本文件不一致时以测试为准并修正本文件。

## 1. 定义里的位置

`definitionVersion: 2` 的定义在 v1 基础上多出下列字段（v1 定义出现它们直接 400）：

| 位置 | 字段 | 类型要求 | 编译到 |
|---|---|---|---|
| 题组 | `condition` | 布尔 | `groups.grelevance` |
| 题目 | `condition` | 布尔 | `questions.relevance` |
| 题目 | `validation: {rule, message}` | `rule` 布尔，可用 `self` | 属性 `em_validation_q`（未作答时放行）、`em_validation_q_tip` |
| 计算值题（`type: "*"`） | `calculation` | 数值或文本 | 属性 `equation`＝`{表达式}`，`hidden=1`（可覆盖），数值再加 `numbers_only=1` |
| 题干、帮助、选项、子题、题组说明、校验提示 | 文本里的 `{{ 表达式 }}` | 数值或文本 | `{表达式}` |

v2 定义里**禁止**：`relevance` 字段（题目与题组）以及属性 `em_validation_q`、`em_validation_q_tip`、
`em_validation_sq`、`em_validation_sq_tip`、`equation`、`array_filter`、`array_filter_exclude`、
`array_filter_style`（`E_RAW_EXPRESSION`）。

文本里 `{{ }}` 之外的 `{`、`}` 一律写成 `&#123;`、`&#125;`，问卷标题、题组标题同样处理：
作者的原文永远不会被引擎当成表达式。作者写的 HTML 原样保留（作者内容的 HTML 净化不在本 DSL 范围）。

## 2. 文法

```
expression := or
or         := and ( "or" and )*
and        := not ( "and" not )*
not        := "not" not | comparison
comparison := additive [ ( "==" | "!=" | "<" | "<=" | ">" | ">=" ) additive
                       | "in" "[" additive ( "," additive )* "]"
                       | "in" additive ]                 -- 比较不能连写
additive   := term ( ( "+" | "-" ) term )*
term       := unary ( ( "*" | "/" ) unary )*
unary      := "-" unary | primary
primary    := NUMBER | STRING | "true" | "false" | "self"
            | "(" expression ")"
            | NAME "(" [ expression ( "," expression )* ] ")"      -- 函数
            | reference
reference  := ( CODE | "q" "(" STRING ")" ) [ "." CODE ] [ "[" DIGITS "]" ]
NUMBER     := DIGITS [ "." DIGITS ]
STRING     := '"' … '"' | "'" … "'"      -- 转义只有 \" \' \\
CODE/NAME  := 字母开头的字母数字串（不含下划线）
```

- 关键字：`and or not in true false self`，小写。
- 引用：`QCODE` 按题目代码，`q("uuid")` 按题目或子题 UUID（推荐编辑器使用 UUID）。
  `.SUB` 取子题或 `other`，`[0]`／`[1]` 取双尺度数组的尺度。
- 不认识的字符（`= & | ! ; { }` 等）、引擎字段名（`123X45X678`）、引擎后缀（`.NAOK`）一律报错。
- 长度上限 2000 字符、嵌套上限 32 层。

## 3. 类型

| 引用 | 类型 | 引擎变量 |
|---|---|---|
| 数值题 `N` | number | `Q.NAOK` |
| 文本题 `S T U`、日期 `D` | text | `Q.NAOK` |
| 单选 `L !` | choice（域＝选项代码，带其他时含 `-oth-`） | `Q.NAOK` |
| 单选 `.other` | text | `Q_other.NAOK` |
| 多选 `M P` 整题 | set（只能用于 `count()`、`answered()`、`"SQ" in Q`） | `Q_SQ.NAOK` 列表 |
| 多选 `.SQ` | bool（是否勾选） | `(Q_SQ.NAOK == "Y")` |
| 数组 `F` 的 `.ROW` | choice（尺度 0 的选项） | `Q_ROW.NAOK` |
| 双尺度 `1` 的 `.ROW[s]` | choice（尺度 s 的选项） | `Q_ROW_s.NAOK` |
| 计算值 `*` | 其公式的类型（choice 视为 text） | `Q.NAOK` |
| 说明 `X` | 无值，引用即报错 | — |
| 单选带评论 `O` | choice（域＝选项代码） | `Q.NAOK` |
| 五分制 `5`、是否 `Y`、性别 `G` | choice（域＝引擎固定代码 `1`–`5`／`Y N`／`F M`） | `Q.NAOK` |
| 数组 `H` 的 `.ROW` | choice（选项） | `Q_ROW.NAOK` |
| 数组 `A B C E` 的 `.ROW` | choice（引擎固定刻度） | `Q_ROW.NAOK` |
| 多项数值 `K` 的 `.SQ`／多项填空 `Q` 的 `.SQ` | number／text | `Q_SQ.NAOK` |
| 矩阵 `: ;`、排序 `R`、上传 `\|` | 暂不可引用（`E_EXPR_NOT_A_VALUE`） | — |

规则：`and or not` 只接受布尔；算术只接受数值；`== !=` 两侧同族（数值／文本与单选／布尔），
单选与字符串字面量比较时字面量必须在选项域内；`< <= > >=` 只比两个数值或两个文本；
`in [...]` 同 `==`。字符串字面量不得含 `{ } < > & \` 与控制字符（引擎比较的是转义后的文本答案）。

### 函数

| 函数 | 说明 | 编译到 |
|---|---|---|
| `answered(ref)` | 是否作答（多选：至少选一项） | `(!is_empty(X))` ／ `(count(...) > 0)` |
| `count(set)` ／ `count(ref, …)` | 已选项数／已作答题数 | `count(...)` |
| `sum(n, …)` | 求和，**未作答按 0** | `sum(...)` |
| `abs floor ceil round(n[, 位数])` | 数值函数，参与空值传播 | 同名 |
| `if(cond, a, b)` | 两支同族 | `if(C, A, B)` |
| `coalesce(ref, 缺省)` | 未作答时取缺省 | `if(is_empty(X), D, X)` |
| `length(text)` | 字符数 | `strlen(X)` |
| `join(a, b, …)` | 文本拼接 | `join(...)` |
| `label(ref)` | 单选／数组行的选项文字（只建议用于引用） | `Q.shown` |

## 4. 语义

- **隐藏即空**：所有引用编译成 `.NAOK`，被条件隐藏的题读作空串，而不是让整个表达式失效。
- **算术空值传播**：`+ - * /`、一元负号与数值函数构成的子树，任一可能为空的操作数为空、
  或任一非字面量除数为 0 时结果为空：`if(is_empty(A) or (D) == 0, "", 算式)`。
  加法编译成 `sum(A, B)`（引擎的 `+` 遇到非数字会拼接字符串）。除以字面量 0 在发布前拒绝。
- **比较**：与空值做 `< <= > >= ==` 比较为假、`!=` 为真（引擎原生语义）；需要时用 `answered()`。
- **校验规则**只在作答后生效：`em_validation_q = (is_empty(self) or 规则)`；只支持单列题型
  `L ! S T U N D`。
- **隐藏必答**：条件为假时引擎不检查必答（`em_manager_helper.php` `_ValidateQuestion`：
  `$qrel && !$qhidden` 才查必答），并在该页提交时把隐藏题的值置 NULL（`deletenonvalues`，
  引擎缺省为 1；网关无法经 RemoteControl 读取此实例级配置，部署须保持缺省）。
  题组条件为假时组内所有题（含计算值）置 NULL。e2e 覆盖「先答后隐藏」被清空。
- **引用转义**：文本答案经引擎 `htmlSpecialCharsUserValue()` 转义 `< > & { }` 后才被替换进页面；
  e2e 用 `<b>Tom</b>{QAGE}` 验证页面只出现转义后的文本。

## 5. 顺序与循环（发布前报错，422 `validate`）

| 错误码 | 含义 |
|---|---|
| `E_EXPR_SYNTAX` | 解析失败，消息带列号 |
| `E_EXPR_TYPE` / `E_EXPR_ARITY` / `E_EXPR_UNKNOWN_FUNCTION` | 类型／参数个数／函数不在白名单 |
| `E_EXPR_UNKNOWN_REFERENCE` / `E_EXPR_UNKNOWN_SUBQUESTION` / `E_EXPR_UNKNOWN_ANSWER_CODE` | 题、子题、选项代码不存在 |
| `E_EXPR_MEMBER_REQUIRED` / `E_EXPR_SCALE_REQUIRED` / `E_EXPR_NOT_A_VALUE` | 数组缺行、双尺度缺尺度、引用说明题 |
| `E_EXPR_STRING_CHAR` / `E_EXPR_DIVISION_BY_ZERO` / `E_EXPR_SELF_NOT_ALLOWED` | 字面量字符、除以 0、`self` 用在校验规则之外 |
| `E_EXPR_FORWARD_REFERENCE` | 引用同一页上后面的题（或文本引用自身） |
| `E_EXPR_LATER_PAGE` | 引用后面页上的题（页＝`format`：G 每组一页、Q 每题一页、A 全部一页） |
| `E_EXPR_CYCLE` | 循环依赖，消息给出路径，如 `CA -> CB -> CA` |
| `E_CALCULATION_MISSING` / `E_CALCULATION_UNEXPECTED` / `E_CALCULATION_MANDATORY` | 计算值题的结构约束 |
| `E_VALIDATION_UNSUPPORTED_TYPE` / `E_RAW_EXPRESSION` | 校验题型不支持／直写引擎表达式 |

题目的条件、计算公式、校验规则、文本引用只能引用**文档顺序在它之前**的题（校验规则可用 `self`）；
题组条件只能引用**更早题组**里的题。依赖图（题目 → 条件与公式引用的题、题目 → 所在题组、
题组 → 题组条件引用的题）上的强连通分量报为循环，循环内的边不再重复报顺序错误。

失败条目与既有格式一致：`"<错误码> <路径>: <消息>"`，路径如
`groups[1].questions[0].condition`、`groups[1].questions[2].validation.rule`、`groups[0].questions[1].answers[0].text`。

## 6. 计分（WP-03.3）

顶层 `scoring` 是一个数组，每项是一份计分表（v1 定义里出现直接 400）。计分**不是第二套编译器**：
它只把计分表展开成本文件前几节的 DSL，再走同一条解析、类型检查、依赖检查与 emit 的链路。

```json
"scoring": [{
  "uuid": "…", "code": "STOTAL", "title": "养宠投入分", "groupTitle": "你的结果",
  "items": [
    { "question": "QPET",   "points": { "A1": 2, "A2": 5 } },
    { "question": "QCARE",  "points": { "SQ001": 1, "SQ002": 2 } },
    { "question": "QARR",   "member": "R1", "points": { "Y1": 4 } },
    { "question": "QHOURS", "weight": 1 }
  ],
  "bands": [
    { "code": "LOW", "upTo": 4, "text": "投入不多。" },
    { "code": "MID", "upTo": 9, "text": "得分 {{ STOTAL }}，还不错。" },
    { "code": "HIGH", "text": "非常投入！" }
  ]
}]
```

- `question` 写题目代码或题目 UUID；数组题另写 `member` 指定行。
- 按选项计分的题（单选、数组行、多选）写 `points`（选项／子题代码 → 分数）；
  数值题写 `weight`（分数＝答案 × weight）。两者不能混写，也不能都不写。
- **未作答的计分项按 0 分**：`points` 展开成 `if(引用 == "代码", 分数, 0)`，
  `weight` 展开成 `coalesce(引用, 0) * 权重`，总分是它们的 `sum(…)`，所以总分永远不是空值。
- `bands` 按顺序从低到高，`upTo` 是这一段的上界（含），**只有最后一段不写 `upTo`**，表示「及以上」。

### 展开结果

计分展开成一个**追加在最后的题组**（标题取 `groupTitle`）。它不带条件，分数不会被别人的题组条件
清空；排在最后也让「只能引用前面的题」自动成立——反过来说，**前面的题不能引用分数**
（会报 `E_EXPR_LATER_PAGE`）。组里依次是：

| 生成的题 | 代码 | 类型 | 内容 |
|---|---|---|---|
| 总分 | `<CODE>` | `*` | `sum(if(…), …, coalesce(…, 0) * w)`，数值 |
| 分段 | `<CODE>B` | `*` | 按上界嵌套的 `if`，值是分段代码 |
| 分段文案 | `<CODE>R1`、`R2`… | `X` | 条件 `<CODE>B == "<分段代码>"`，文本是作者写的 `text` |

于是「按分数分支」「展示结果」就是普通的 v2 条件与文本引用，没有新机制：作者也可以自己写
`condition: "STOTAL > 40"`。分数代码最长 16 字符（题目代码上限 20，要给后缀留位）。

### 作者文本的安全性

生成的表达式里只有数字、题目引用，以及两种平台校验过字符集的代码（选项／子题代码、分段代码），
**没有一处是作者的自由文本**。作者的自由文本（`title`、分段 `text`）全部落在题目的文本上，由第 1 节
那条模板规则转义（`{` `}` → `&#123;` `&#125;`），只有 `{{ }}` 里的才编译成表达式。

这句话成立的前提是 **UUID 的字符集在解析期就受限**（见第 8 节）：计分把题目引用渲染成
`q("<uuid>")`，UUID 不受限时带引号的值能从字符串字面量里逃出来，上面这句就是假的。
各插值点另有一层断言（`ScoringError`）作为纵深防御。

### 错误码

| 错误码 | 含义 |
|---|---|
| `E_SCORING_CODE` / `E_SCORING_CODE_CONFLICT` | 分数代码非法或过长／生成的代码与已有题目或另一份计分表撞名 |
| `E_SCORING_UNKNOWN_QUESTION` / `E_SCORING_UNKNOWN_KEY` | 计分项指向不存在的题／不存在的选项或子题代码 |
| `E_SCORING_ITEM_SHAPE` | 该题型不能计分，或 `points` 与 `weight` 用反了 |
| `E_SCORING_BAND_CODE` / `E_SCORING_BAND_ORDER` | 分段代码非法或重复／上界没有递增、不是最后一段却不写 `upTo` |
| `E_SCORING_GROUP_TITLE` | 多份计分表共用一个计分组，`groupTitle` 却不一致（不静默丢弃其中一个） |

## 7. 双执行比对（WP-03.4）

本文件第 4 节的语义有**两份独立实现**，发布前用真引擎逐条比对：

1. **平台**：`pubgw/logic/evaluate.py` 直接在语法树上按本文件求值；
2. **引擎**：真 LimeSurvey 的 ExpressionManager 跑 `emit.py` 编译出来的 ExpressionScript
   （`platform/tests/e2e/logic_parity.php` 把同一份答案注入 `$_SESSION`）。

解释器**只实现本文件写下来的语义，不模仿 PHP 的类型杂耍**：引擎在契约之外的角落有自己的脾气时，
比对必须把它暴露出来，而不是靠两边一起装傻盖住。

每条用例（一处表达式 × 一份答案向量）都带一个**照本文件人工写下的期望值**，它是分歧时的裁判：

| 情况 | 归责 |
|---|---|
| 引擎报错 | `compiler`（我们编出了引擎跑不了的表达式） |
| 两边一致且合期望 | `none` |
| 两边一致但不合期望 | `both`（编译器与解释器一起错，或本文件错） |
| 分歧，引擎合期望 | `platform`（解释器错） |
| 分歧，平台合期望 | `compiler`（编译器错） |
| 分歧，都不合期望 | `both` |
| 分歧，没有期望值 | `unknown`——必须人工裁决，**不许当成通过** |

跑法：`[TEST_DB=mysql|pgsql] platform/deploy/test/run-publish-gateway-parity.sh`。
除了逐条比对，它还做两件事，否则「全对」什么都证明不了：

- **反向对照（mutation）**：故意把编译产物改坏一次、把平台的值谎报一次，比对必须抓住，
  并且判对是哪一侧；
- **场景级锚定**：再跑两次真实 HTTP 作答，把引擎**真正存进答卷表**的分数与分段同平台算的比，
  证明注入会话那一套不是自说自话。

## 8. UUID 的字符集

定义里所有 `uuid`（问卷、题组、题目、子题、计分表）都必须匹配
`^[A-Za-z0-9][A-Za-z0-9_-]*$`，最长 128 字符。取值范围同时容纳现存的两种写法：
标准 UUID（`11111111-1111-4111-8111-111111111111`）与平台的 slug（`q-single`、`sq-m2`）。
不合规直接 400（`DefinitionError`）。

为什么钉在解析期而不是等到用的地方再转义：UUID 会被**插进生成的 DSL 文本**（第 6 节的
`q("<uuid>")`）。**这不是提权**——生成的文本随后由标准 v2 解析器重新解析，走同样的类型检查、
引用检查与环检测，而作者本来就能直接写 v2 DSL 表达式，注入者拿不到他原本没有的能力。
真正坏掉的是另外两件事：

1. **正确性**：校验时看的是原始引用，渲染出来的文本却可能指向**另一道题**；
2. **不变式**：第 6 节「作者文本的安全性」那句话会变成假的，而且是**潜伏的危险**——
   将来只要有一个插值点不再被重新解析，它立刻变成真注入。

同理，`scoring` 里的所有数值（`points`、`weight`、`upTo`）必须是**有限**数值：
`json.loads` 默认认 `Infinity` / `-Infinity` / `NaN` 三个字面量，放进来会被渲染成
`inf` / `nan` 写进表达式，再被引擎当成标识符去查变量。
