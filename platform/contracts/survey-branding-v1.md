# 问卷品牌与多语言 v1（`branding` 与 `translations` 块）

WP-19 切片 19.3，覆盖需求 R19-06（主题／页眉页脚）、R19-07（定制的沙箱边界）、R19-10（多语言与去广告）。
实现：平台草稿原样保存 → 网关 `pubgw/branding/`（校验、编译）→ 引擎主题 `themes/survey/zh-business`。
实现与本文件不一致时以测试为准并修正本文件。

## 1. 位置与版本

定义（`definitionVersion` 1 或 2）的两个可选顶层键 `branding`、`translations`。
**两个键都不出现时，编译结果与本切片之前逐字节相同**，结构指纹也不变——
品牌与译文都不进入指纹的输入（指纹只看题型／题目代码／aid／尺度，见 `fieldmap.py`）。

```json
"branding": {
  "brandingVersion": 1,
  "primaryColor": "#1F6FEB",
  "pageTitle":  {"en": "Acme feedback", "zh-Hans": "Acme 意见征集"},
  "footerText": {"en": "© Acme Ltd.", "zh-Hans": "© 艾克姆有限公司"},
  "logoFile": "logo.png",
  "logoAlt": "Acme",
  "faviconFile": "favicon.ico"
},
"translations": {
  "zh-Hans": {
    "title": "满意度调查",
    "description": "大约需要两分钟",
    "groups":       {"<group-uuid>": {"title": "基本情况", "description": "…"}},
    "questions":    {"<question-uuid>": {"text": "你的性别？", "help": "用于分组统计"}},
    "subquestions": {"<subquestion-uuid>": {"text": "第一行"}},
    "answers": [{"question": "<question-uuid>", "code": "A1", "scale": 0, "text": "男"}]
  }
}
```

## 2. `branding` 字段

| 字段 | 取值 | 编译目标（主题选项） | 说明 |
|---|---|---|---|
| `brandingVersion` | `1` | — | 必填 |
| `primaryColor` | `#rrggbb` 或 `#rgb`，大小写不限 | `mjybrandprimary` | 主色，注入为 CSS 变量 `--mjy-brand-primary` |
| `pageTitle` | 字符串，或 语言→字符串 的对象 | `mjybrandtitle` ＋ `mjybrandtitle__<语言>` | 浏览器标题；缺省用问卷标题 |
| `footerText` | 字符串，或 语言→字符串 的对象 | `mjybrandfooter` ＋ `mjybrandfooter__<语言>` | 页脚文本，**按纯文本转义**后渲染 |
| `logoFile` | `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`，且带图片扩展名 | `mjybrandlogo`＝`on`、`mjybrandlogofile`＝`./files/<名>` | 主题 `files/` 目录下的文件名 |
| `logoAlt` | 字符串 | `mjybrandlogoalt` | 图片替代文本 |
| `faviconFile` | 同 `logoFile`，另允许 `.ico` | `mjybrandfavicon`＝`./files/<名>` | |

`branding` 出现时定义的 `theme` 必须是 `zh-business`（当前唯一带品牌位的主题），否则 422。

语言→字符串 形式的键必须出现在定义的 `language` ＋ `additionalLanguages` 里；渲染时按
`当前语言 → 默认（`language` 的值）→ 空` 回退。

### 2.1 不允许的东西（R19-07 的沙箱边界）

| 键 | 结果 |
|---|---|
| `customCss` | 422 `E_BRAND_CUSTOM_CSS_UNSUPPORTED` |
| `customJs`、`headHtml`、`footerHtml` 等任何 HTML／脚本片段 | 422 `E_BRAND_UNKNOWN_KEY` |
| `logoFile` / `faviconFile` 里带 `/`、`\`、`..`、`:` 或任何 URL | 422 `E_BRAND_ASSET` |

**租户不能提供 CSS 或 JS，一行都不行**，这是本切片的安全默认值，理由：

1. 一段 `background-image: url(https://…)` 就能把作答页变成对外请求，既破坏"去广告／不连第三方"，
   又把答题者的 IP 与 Referer 泄漏给第三方；属性选择器还能逐字符外传已填内容。
   要挡住这些就得写一个 CSS 解析器＋属性白名单，攻击面比它带来的价值大。
2. 任何 JS 都与作答页同源。作答页与管理端同域部署时，租户 JS 可读到管理会话
   （蓝图"客户 JS 读不到管理会话"）。没有 JS 就没有这个问题，不依赖 Cookie 属性配置是否正确。

品牌能力因此收敛成一组**封闭的取值**（颜色、文本、主题内的图片文件名），
每一个都由网关按固定形状校验，再由主题按纯文本／属性转义渲染。
需要更自由的样式时应新增主题（平台侧受控），不是放开租户输入。

## 3. `translations` 字段

`translations` 的每个键是一个语言代码，必须出现在 `additionalLanguages` 里（基础语言 `language`
不能出现——它的文本就在定义本体里）。每个语言对象的键只允许
`title`、`description`、`groups`、`questions`、`subquestions`、`answers`。

- `groups` / `questions` / `subquestions` 是 **uuid → 字段对象** 的映射，uuid 必须在定义里存在；
- `answers` 是数组，每项 `{question, code, scale?, text}`，`(question, code, scale)` 必须在定义里存在，
  且同一语言内不能重复；
- 任何未列出的键、类型不对、uuid 或选项对不上，都是 422（问题代码见 §4）。

**回退**：没有译文的条目按基础语言的文本编译。因此一份只翻译了标题的定义也是合法的，
答题者切到该语言时题目仍以基础语言显示，而不是空白。

编译产物：每个附加语言各一套 `group_l10ns` / `question_l10ns` / `answer_l10ns` 行与一行
`surveys_languagesettings`。没有 `translations` 时附加语言仍会得到与基础语言同文的各套行——
这是本切片修掉的既有缺陷：在此之前附加语言只有 `surveys_languagesettings` 一行，
题目一个字都没有，作答页切换语言后是空白。

## 4. 问题代码（422 `validate`）

`E_BRAND_INVALID` `E_BRAND_VERSION` `E_BRAND_UNKNOWN_KEY` `E_BRAND_TYPE` `E_BRAND_COLOR`
`E_BRAND_ASSET` `E_BRAND_LANGUAGE` `E_BRAND_THEME` `E_BRAND_CUSTOM_CSS_UNSUPPORTED`
`E_TRANSLATION_INVALID` `E_TRANSLATION_UNKNOWN_KEY` `E_TRANSLATION_TYPE` `E_TRANSLATION_LANGUAGE`
`E_TRANSLATION_TARGET` `E_TRANSLATION_DUPLICATE`

## 5. 主题 `zh-business`

`themes/survey/zh-business` 继承引擎自带的 `vanilla`（`<extends>vanilla</extends>`），
只覆盖三个视图与一份样式表。继承而不是复制，是为了让上游修的作答页缺陷继续生效。

覆盖的位置与理由：

| 文件 | 改动 | 理由 |
|---|---|---|
| `views/subviews/header/head.twig` | 去掉 `<meta name="generator" content="LimeSurvey http://www.limesurvey.org">`；不再 include `google_analytics.twig`；favicon 与 `<title>` 改读品牌选项；注入 `--mjy-brand-primary` | 去广告（R19-10）：作答页不得出现上游标识，也不得向任何非租户来源发请求 |
| `views/subviews/header/nav_bar.twig` | logo 改读 `mjybrandlogofile`／`mjybrandlogoalt` | 品牌（R19-06） |
| `views/subviews/footer/footer.twig` | 渲染 `mjybrandfooter`（按当前语言取，转义） | 页脚（R19-06） |
| `css/mjy-brand.css` | 用 `--mjy-brand-primary` 上色 | 主色 |

主题必须先装进引擎（`lime_templates` ＋ 全局 `lime_template_configuration`，
`Template::getTemplateList()` 只认库里登记过且磁盘上存在的主题）。
按问卷的选项经 LSS 的 `<themes>` 小节下发（`import_helper.php` → `TemplateManifest::importManifestLss`）。

### 5.1 作答页不连外部来源

主题渲染出来的页面里，所有 `src` / `href` / `url()` 要么是相对路径，要么与作答页同源。
端到端用例把整页 HTML 抓下来，扫出所有绝对 URL，断言其中没有第三方主机。
问卷自己的设置（`googleanalyticsapikey`、`surveyls_url` 等）若填了外部地址仍然会生效——
这是租户自己的选择，不属于"第三方广告"，但 `zh-business` 不再渲染 Google Analytics 代码段。
