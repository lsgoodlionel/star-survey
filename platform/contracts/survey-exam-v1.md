# 考试答案键 v1（`exam` 块，examVersion 1）

WP-09 切片 09.1「答案不下发」。实现：网关 `pubgw/exam/`（校验、编译、守门）→
插件 `plugins/MjyRuntimePolicy`（存放与判分）。实现与本文件不一致时以测试为准并修正本文件。

## 0. 这份契约要守住的唯一一件事

**正确答案绝不出现在发给浏览器的任何内容里。** 不在题目属性、不在选项的
`assessment_value`、不在页面 HTML、不在页面 JS、不在隐藏字段、不在任何表达式。

这不是一句口号，它有一条具体的、已经存在的泄漏通道要挡：

> 引擎的 ExpressionManager 会把 ExpressionScript **翻译成页面 JS**
> （`application/helpers/expressions/em_manager_helper.php:4351`，
> `GetJavaScriptEquivalentOfExpression()`，结果写成 `LEMrel<qid>()`）。

于是凡是被编译成引擎表达式的东西，作答者都读得到。这直接决定了两件事：

1. 答案键**不能**用 WP-03.3 的 `scoring` 块表达。`{"question":"Q1","points":{"A2":5}}`
   会展开成计算值题的 `equation`，内容就是 `sum(if(Q1 == "A2", 5, 0))`——那就是答案本身。
2. 答案键**不能**出现在逻辑条件里。`Q1 == "A2"` 当条件用，等于把正确答案摆进页面 JS。

两条都在发布期由 §4 的守门规则拒绝，不是写在文档里指望作者自觉。

**与既有做法的区别（重要）**：题型车道的 `mjy-psych-trial` 把正确按键放在题目属性
`mjy_psych_trials` 里，主题模板会把它渲染成 `data-mjy-trials`
（`themes/question/mjy-psych-trial/survey/questions/answer/longfreetext/answer.twig:23`）。
那里守的是**完整性**（副表里没有一列能让作答者自称答对），**不是保密性**——正确按键
本来就在页面上。考试不能照抄那个形状：本契约要的是保密性，所以答案键走
`plugin_settings`，那条载体从不渲染进作答页。

## 1. 位置与版本

定义（`definitionVersion` 1 或 2）的可选顶层键 `exam`。不带 `exam` 的定义编译结果逐字节不变。

```json
"exam": {
  "examVersion": 1,
  "answerKey": [
    {"question": "QCAP",  "correct": ["A2"], "points": 5},
    {"question": "QPICK", "correct": ["SQ001", "SQ003"], "points": 4},
    {"question": "QCITY", "correct": ["巴黎", "Paris"], "points": 3, "ignoreCase": true, "trim": true}
  ]
}
```

## 2. 字段

| 字段 | 取值 | 说明 |
|---|---|---|
| `examVersion` | `1` | 必填 |
| `answerKey[]` | 非空数组 | 每道客观题一条；同一道题只能出现一次（代码与 UUID 视为同一道） |
| `answerKey[].question` | 题目代码或 UUID | 必须是定义里的题；题型须是客观题（见下） |
| `answerKey[].correct` | 非空字符串数组，无重复 | 选择题写选项／子题代码；文本与数值题写可接受的答案文本 |
| `answerKey[].points` | `(0, 1000]`，可含小数 | 这道题的分值 |
| `answerKey[].ignoreCase` | 布尔，缺省 `false` | **仅文本／数值题**；用在选择题上是 `E_EXAM_MATCH` |
| `answerKey[].trim` | 布尔，缺省 `true` | 同上 |

**客观题**按 `pubgw/logic/scope.py` 已有的题型归类判定（考试不另造一套）：

| 归类 | 引擎题型 | `correct` 的含义 |
|---|---|---|
| `choice` | `L` `!` `5` `Y` `G` `O` | 恰好一个选项代码 |
| `set` | `M` `P` | 一个或多个子题代码；顺序无关（编译时排序） |
| `text` | `S` `T` `U` `D` | 一个或多个可接受的答案文本 |
| `number` | `N` | 同上 |

数组题（`A` `B` `C` `E` `F` `H` `K` `Q` `1`）、排序题等不是客观题，指定答案是
`E_EXAM_QUESTION_TYPE`：一道题有多行多尺度，没有单一的「正确答案」。

## 3. 问题代码（422 `validate`）

`E_EXAM_INVALID` `E_EXAM_VERSION` `E_EXAM_UNKNOWN_KEY` `E_EXAM_TYPE` `E_EXAM_EMPTY`
`E_EXAM_RANGE` `E_EXAM_QUESTION` `E_EXAM_QUESTION_TYPE` `E_EXAM_DUPLICATE`
`E_EXAM_ANSWER` `E_EXAM_MATCH`

「不下发」守门另有两个：

| 代码 | 触发条件 | 为什么 |
|---|---|---|
| `E_EXAM_KEY_IN_SCORING` | 有答案键的题又出现在某个 `scoring` 项里 | 计分表展开成计算值题的 `equation`，会被翻成页面 JS |
| `E_EXAM_KEY_IN_LOGIC` | 某处条件／模板把有答案键的题与它**自己的正确答案**相比 | 条件会被翻成页面 JS |

`E_EXAM_KEY_IN_LOGIC` 只认「与自己的正确答案相比」这一种形态（`==`、`!=`、
`in [...]`、`"SQ001" in QMULTI`）。拿**错误**答案做分支（`Q1 == "A1"`，而 `A1` 不是答案）
是正常的问卷逻辑，不拦。

## 4. 插件载荷与回读

答案键编译成一行 LSS `plugin_settings`（`name=MjyRuntimePolicy`、`key=mjy_exam_key`），
值是键排序、紧凑、纯 ASCII 的 JSON。条目按题目代码排序，于是同一份答案键无论作者
怎么排，摘要都一样。

```json
{"answerKey":[{"correct":["A2"],"kind":"choice","points":5.0,"question":"QCAP"},
              {"correct":["SQ001","SQ003"],"kind":"set","points":4.0,"question":"QPICK"},
              {"correct":["巴黎"],"kind":"text","match":{"ignoreCase":true,"trim":true},
               "points":3.0,"question":"QCITY"}],
 "schema":"mjy-exam-key/1"}
```

`match` 只在 `text` / `number` 条目上出现。

**这是整套里唯一一份正确答案。** 编译产物里没有 `native_settings`：答案键不写进任何
引擎原生设置、题目属性、选项 `assessment_value` 或表达式——引擎渲染作答页时会碰到的
每一样东西都不碰。`plugin_settings` 从不渲染进作答页，这正是选它当载体的理由。

发布 `apply` 阶段（激活前）网关可回读
`GET <engine>/index.php/plugins/direct?plugin=MjyRuntimePolicy&function=examStatus&sid=<sid>`，
期望 `{"plugin":"MjyRuntimePolicy","active":true,"surveyId":<sid>,"rows":1,"valid":true,
"examDigest":<载荷 SHA-256>,"questions":<题数>}`。

**这个端点只回摘要，绝不回答案内容。** `newDirectRequest` 是公开路由、没有鉴权，
把答案回出去等于换个地方下发。题数不是秘密（作答者数得出来），回它是为了让平台
确认「下发的那份确实被认了」。

插件侧解析一律 fail closed：载荷有任何一处不认识（未知键、题型不认识、分值非正、
同一题两次）就整份拒绝。理由是「半份答案键」比「没有答案键」更糟——没有答案键判不了分
（看得见），半份答案键会把一部分题判成「怎么答都对」（看不见）。同一个 sid 下多于一行
也是错误，不是「挑一份用」。

## 5. 证据

- 网关侧：`tests/test_exam_schema.py`（校验与两条守门规则）、`tests/test_exam_compile.py`
  （载荷、摘要，以及 `NoSecondOutletTest`——把整份 LSS 挖掉那一行载荷之后再扫一遍，
  任何地方出现答案都算失败）。
- 插件侧：`plugins/MjyRuntimePolicy/tests/MjyExamKeyTest.php`、`MjyExamKeyStoreTest.php`
  （含「状态端点不泄漏答案」）。
- **真引擎侧**：`platform/tests/e2e/exam_key.py`。单元测试看的是编译产物，看不到引擎
  渲染页面时又加了什么——而泄漏恰恰发生在渲染期（EM 翻 JS）。端到端抓作答页的完整
  HTML **与它引用的每一个 JS／CSS**，再做三件事：哨兵扫描、泄漏对照、差分不可区分。
  做法见该文件头部说明。
