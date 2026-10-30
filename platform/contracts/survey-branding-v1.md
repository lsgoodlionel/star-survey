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
  "logoAsset": {"assetId": "作者资产 UUID"},
  "logoAlt": "Acme",
  "faviconAsset": {"assetId": "作者资产 UUID"},
  "coverAsset": {"assetId": "作者资产 UUID"},
  "backgroundAsset": {"assetId": "作者资产 UUID"},
  "watermarkText": {"en": "Internal", "zh-Hans": "内部使用"}
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
| `logoAsset` | 作者态 `{ "assetId": "<UUID>" }`；受信物化态 `{ "url", "assetVersion", "contentType", "sizeBytes", "sha256", "fileName": "mjy-brand-logo.<ext>" }` | 导入后写入 `image::survey::mjy-brand-logo.<ext>` 到 `mjybrandlogofile`，不写入 LSS | MIME 限定 PNG/JPEG/GIF/WebP，最大 5 MiB；作者不得提供物化字段 |
| `logoAlt` | 字符串 | `mjybrandlogoalt` | 图片替代文本 |
| `faviconFile` | 同 `logoFile`，另允许 `.ico` | `mjybrandfavicon`＝`./files/<名>` | |
| `faviconAsset` | 作者态 `{ "assetId": "<UUID>" }`；受信物化态 `{ "url", "assetVersion", "contentType", "sizeBytes", "sha256", "fileName": "mjy-brand-favicon.<ext>" }` | 导入后写入 `image::survey::mjy-brand-favicon.<ext>` 到 `mjybrandfavicon`，不写入 LSS | PNG/JPEG/GIF/WebP，最大 5 MiB |
| `coverFile` / `coverAsset` | 与 Logo 相同的旧文件名／作者态资产形状；固定物化名 `mjy-brand-cover.<ext>` | `mjybrandcover`，在导航下方渲染受控封面图 | PNG/JPEG/GIF/WebP，最大 5 MiB |
| `backgroundFile` / `backgroundAsset` | 与 Logo 相同的旧文件名／作者态资产形状；固定物化名 `mjy-brand-background.<ext>` | `mjybrandbackground`，仅作为同源作答页背景图 | PNG/JPEG/GIF/WebP，最大 5 MiB |
| `watermarkText` | 字符串，或语言→字符串的对象 | `mjybrandwatermark` ＋ `mjybrandwatermark__<语言>` | 纯文本转义，以不可交互的背景水印渲染 |

四个 `*Asset` 品牌槽位是管理端作者草稿的稳定引用。作者态对象只能含 `assetId`，不能直接进入网关编译。平台在预览或发布时按当前租户、资产状态和签名取件结果解析实际版本，生成受信物化态对象。网关只接受完整的物化态字段：URL 必须是规范 HTTPS 主机上的固定 `/a/<tenant>/<uuid>/<version>` 取件形状并只带 `exp`、`sig`，路径版本必须与 `assetVersion` 一致；`contentType` 必须与槽位匹配，`sizeBytes` 为 1 至 5 MiB，`sha256` 必须是该钉住版本的 64 位小写十六进制摘要，`fileName` 必须由槽位和 MIME 派生为固定名 `mjy-brand-{logo|favicon|cover|background}.<ext>`，不含资产 UUID。URL 仅是平台签名请求中的身份元数据，网关不会抓取它；真实字节必须由下述 bundle 携带并与 `sha256` 再绑定。网关不把 URL、版本、MIME、大小或摘要写入引擎输出。凭据、端口、非规范主机、路径分隔符、`..`、未知字段、超限或不匹配的文件名均拒绝。SVG 不允许。

### 2.1 品牌资源的网关到引擎落地

发布与预览的请求边界携带版本化、有限大小的 `brandAssets` 包。每一项由平台在资产服务中读取，并同时携带槽位、固定文件名、内容类型、字节数、SHA-256 与字节；网关先把每项同受信物化态的槽位、文件名、MIME、大小和摘要逐项比对，再调用 `MjyQuestionExtensions` 的签名直连端点。该端点只允许 `zh-business` 以及四个由槽位和 MIME 派生的固定名字，使用原子写入把字节落到 `upload/surveys/<sid>/images/`，再仅更新该问卷的主题选项为 `image::survey::<fixed-name>`。

LimeSurvey 的 RemoteControl `import_survey` 不会从 LSS/LSA 搬运任意主题文件，且主题配置在导入时会校验图片路径。因此含资源的选项不得在导入前写入 LSS；网关在导入获取 `sid` 后，必须通过上述端点一次性写入资源及对应选项。验签、包形状、摘要、大小、内容类型、写入或选项更新任一环节失败时，发布/预览失败并沿用既有回滚删除新建问卷。引擎侧不得接收作者 URL、资产 UUID、版本号、签名地址或任意文件路径。

同一个槽位不得同时设置旧 `*File` 与新 `*Asset`。兼容已有引擎态定义中的四个合法文件名字段；新管理端写入作者态资产引用。发送给引擎的品牌块只保留编译后的文件名与品牌文本，移除四个 `*Asset` 及任何作者态/物化元数据；不得包含 URL 或 `assetVersion`。

`branding` 出现时定义的 `theme` 必须是 `zh-business`（当前唯一带品牌位的主题），否则 422。

语言→字符串 形式的键必须出现在定义的 `language` ＋ `additionalLanguages` 里；渲染时按
`当前语言 → 默认（`language` 的值）→ 空` 回退。

### 2.1 不允许的东西（R19-07 的沙箱边界）

| 键 | 结果 |
|---|---|
| `customCss` | 422 `E_BRAND_CUSTOM_CSS_UNSUPPORTED` |
| `customJs`、`headHtml`、`footerHtml` 等任何 HTML／脚本片段 | 422 `E_BRAND_UNKNOWN_KEY` |
| 任一 `*File` 里带 `/`、`\`、`..`、`:` 或任何 URL；作者资产对象带路径、URL、版本或未知字段 | 422 `E_BRAND_ASSET` |

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
| `views/subviews/header/head.twig` | 去掉上游 generator 与 Google Analytics；favicon、标题、主色和同源背景图改读品牌选项 | 去广告（R19-10）和背景品牌（R19-06） |
| `views/subviews/header/nav_bar.twig` | logo 改读品牌选项，并在导航下方渲染受控封面 | 品牌（R19-06） |
| `views/subviews/footer/footer.twig` | 渲染本地化页脚和纯文本水印 | 页脚与水印（R19-06） |
| `css/mjy-brand.css` | 用 `--mjy-brand-primary` 上色，并约束封面、背景和水印布局 | 主色与广义品牌素材 |

主题必须先装进引擎（`lime_templates` ＋ 全局 `lime_template_configuration`，
`Template::getTemplateList()` 只认库里登记过且磁盘上存在的主题）。
按问卷的选项经 LSS 的 `<themes>` 小节下发（`import_helper.php` → `TemplateManifest::importManifestLss`）。

### 5.1 作答页不连外部来源

主题渲染出来的页面里，所有 `src` / `href` / `url()` 要么是相对路径，要么与作答页同源。
端到端用例把整页 HTML 抓下来，扫出所有绝对 URL，断言其中没有第三方主机。
问卷自己的设置（`googleanalyticsapikey`、`surveyls_url` 等）若填了外部地址仍然会生效——
这是租户自己的选择，不属于"第三方广告"，但 `zh-business` 不再渲染 Google Analytics 代码段。
