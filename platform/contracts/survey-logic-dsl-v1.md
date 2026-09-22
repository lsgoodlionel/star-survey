# 问卷逻辑 DSL v1（definitionVersion 2）

WP-03 切片 03.1／03.2。平台作者端（编辑器、AI 草稿）只写本 DSL，**永远不直写引擎表达式**；
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
