# PHP 依赖清单（SBOM）

- 任务：P0-00.2 引擎认证
- 采集日期：2026-09-20
- 对象：引擎仓库 `master` @ `4c20c680`（LimeSurvey 7.1.2），工作分支 `mjy/main` @ `60600bc1`
- 数据来源：`composer.lock`（`content-hash a33ab0ef5816c4172c683747e68a37fe`）＋开发容器 `survey-web` 内 `composer licenses --format=json`、`composer show --format=json`、`composer audit`、`composer show --outdated --direct`
- 采集方式：仓库挂载在容器 `/var/www/html`，全部为只读命令；本次采集 **packagist 网络可达**（`composer audit` 与 `composer outdated` 均正常返回），因此过期与漏洞数据为实时结果，许可证数据来自锁文件与本地 `installed.json`

## 1. 总览

| 指标 | 数值 |
|---|---|
| 运行时依赖（`--no-dev`） | 45 |
| 开发依赖 | 64 |
| 合计 | 109 |
| `composer audit`（含 dev） | 无安全公告 |
| 锁文件中标记 `abandoned` 的包 | 0 |
| 未声明许可证的包 | 0 |
| composer 平台锁 | `php 8.1.29`（实际运行 PHP 8.3.33，见 ADR 0001） |
| `minimum-stability` | `stable`（`prefer-stable: false`） |

**重要前提：`vendor/` 目录本身已提交进仓库**（`git ls-files vendor` 共 7,553 个文件，覆盖上述 45 个运行时包，不含 dev 包）。也就是说，仓库分发行为本身就在再分发这些第三方代码，许可证义务在克隆仓库那一刻即已触发，而不仅仅在私有化交付时触发。

### 运行时许可证分布

| 许可证 | 包数 | 性质 |
|---|---|---|
| MIT | 35 | 宽松 |
| BSD-3-Clause | 4 | 宽松 |
| ISC | 1 | 宽松 |
| GPL-3.0-or-later | 1 | **强 copyleft，严于引擎自身的 GPL-2.0-or-later** |
| GPL-2.0-or-later | 1 | 与引擎同级 copyleft |
| LGPL-3.0 | 1 | 弱 copyleft（v3 系） |
| LGPL-3.0-or-later | 1 | 弱 copyleft（v3 系） |
| LGPL-2.1-only | 1 | 弱 copyleft（**不含 or-later**） |

### 开发依赖许可证分布

| 许可证 | 包数 |
|---|---|
| BSD-3-Clause | 31 |
| MIT | 30 |
| ISC | 2 |
| OSL-3.0 | 1 |

## 2. 需要标记的包

### 2.1 copyleft 强于引擎自身 GPL-2.0-or-later

| 包 | 版本 | 许可证 | 引入方式 | 说明 |
|---|---|---|---|---|
| `greew/oauth2-azure-provider` | v1.0.4 | GPL-3.0-or-later | 直接 | **最关键的一条**。引擎根 `LICENSE` 与 `composer.json` 都声明 GPL-2.0-or-later，但这个包是 GPL-3.0-**or-later**。GPL-2 与 GPL-3 单向不兼容，组合作品只能沿"or later"升到 GPL-3.0-or-later。详见 [licence-analysis.md](licence-analysis.md) §2 |
| `khaled.alshamaa/ar-php` | v6.3.4 | LGPL-3.0 | 直接 | LGPL v3 系。同时是 ADR 0001 记录的 `ext-calendar` 需求来源 |
| `tecnickcom/tcpdf` | 6.11.4 | LGPL-3.0-or-later | 直接 | PDF 导出核心，无法剥离 |
| `phpmailer/phpmailer` | v6.12.0 | **LGPL-2.1-only** | 直接 | 没有 "or later"。LGPL-2.1 第 3 条允许把副本转用 GPL v2 或**任何更新版本**，据此可进入 GPL-3 组合；但这是一条需要主动行使的条款，属于律师确认项 |
| `html2text/html2text` | 4.3.2 | GPL-2.0-or-later | 直接 | 与引擎同级，不引入新约束 |

这五个包全部是**直接依赖**且均为运行时依赖（`--no-dev`），无法通过"只用 dev"规避。

### 2.2 未声明许可证

无。`composer licenses` 对 109 个包全部返回了非空许可证字段。

### 2.3 已废弃（abandoned）

`composer.lock` 与本地 `installed.json` 中 `abandoned` 字段均为 `false`，`composer audit` 与 `composer outdated`（本次可联网）也未输出废弃告警。但下列包存在**事实上的维护风险**，属于需要单独跟踪的项：

| 包 | 版本 | 证据 | 风险 |
|---|---|---|---|
| `tiamo/spss` | `dev-master` @ `3dd8fde1d3` | `composer.json` 直接写 `"dev-master"`；`composer outdated` 输出 `= [none matched]` | **未打版本号的 dev 分支**。没有语义化版本、没有 tag，上游一次强推就会改变锁定内容的含义；升级路径不可预测 |
| `goldspecdigital/oooas` | `dev-fix-implicitely-marking-parameter-as-nullable-deprecations` @ `5e5bf420df` | `composer.json` 的 `repositories.oooas` 指向 `https://github.com/PrinsFrank/goldspecdigital-oooas`，**不是**原厂 `goldspecdigital/oooas` | **第三方 fork 的 dev 分支**，绕过 packagist 直接走 VCS。供应链来源与原厂脱钩，安全公告不会覆盖 |
| `yiisoft/yii` | 1.1.32 | Yii 1.1 系列上游已长期只做安全修复 | 框架层无长期演进路径，引擎升级时是最大的技术债 |
| `viniciusgava/google-translate-api` | 3.1.0 | 发布于 2022-01-11，锁内最旧的非 polyfill 包之一 | 低活跃度 |
| `mk-j/php_xlsxwriter` | 0.39 | 版本号固定为 `0.39`（`composer.json` 精确锁死，非区间） | 无自动升级通道 |

`ralouphie/getallheaders`（2019-03-08）与 `paragonie/random_compat`（2020-10-15）虽然更旧，但均为功能冻结的 polyfill，不视为风险。

### 2.4 可更新的直接依赖（`composer show --outdated --direct`，本次联网成功）

| 包 | 当前 | 可用 | 类型 |
|---|---|---|---|
| `greew/oauth2-azure-provider` | 1.0.4 | 2.0.0 | 大版本 |
| `khaled.alshamaa/ar-php` | 6.3.4 | 7.0.0 | 大版本 |
| `league/oauth2-client` | 2.9.0 | 2.9.1 | 补丁 |
| `league/oauth2-google` | 3.0.4 | 5.0.0 | 大版本 |
| `paragonie/sodium_compat` | 1.24.2 | 2.5.2 | 大版本 |
| `phpmailer/phpmailer` | 6.12.0 | 7.1.1 | 大版本 |
| `phpseclib/phpseclib` | 3.0.57 | 4.0.1 | 大版本 |
| `twig/twig` | 3.28.0 | 3.29.0 | 次版本 |
| `tiamo/spss` | dev-master | `[none matched]` | 无版本可匹配 |
| `phpunit/phpunit`（dev） | 9.6.36 | 10.5.64 | 大版本 |
| `squizlabs/php_codesniffer`（dev） | 3.13.6 | 4.0.4 | 大版本 |
| `vimeo/psalm`（dev） | 5.26.1 | 6.5.0 | 大版本 |

这些是上游 LimeSurvey 的选择，**本项目不应单方面升级**（会制造与上游的差异，违背 ADR 0001 的引擎变更门禁）。此处仅作记录，供引擎升级窗口参考。

## 3. 运行时依赖完整清单（45，按许可证排序）

| 包 | 版本 | 许可证 | 引入方式 | 发布日期 |
|---|---|---|---|---|
| `shardj/zf1-future` | 1.25.2 | BSD-3-Clause | 直接 | 2026-08-21 |
| `twig/twig` | v3.28.0 | BSD-3-Clause | 直接 | 2026-07-03 |
| `vintagesucks/twig-renderer` | v3.0.2 | BSD-3-Clause | 直接 | 2023-01-28 |
| `yiisoft/yii` | 1.1.32 | BSD-3-Clause | 直接 | 2025-12-23 |
| `html2text/html2text` | 4.3.2 | GPL-2.0-or-later | 直接 | 2024-08-20 |
| `greew/oauth2-azure-provider` | v1.0.4 | GPL-3.0-or-later | 直接 | 2024-04-09 |
| `paragonie/sodium_compat` | v1.24.2 | ISC | 直接 | 2026-08-18 |
| `phpmailer/phpmailer` | v6.12.0 | LGPL-2.1-only | 直接 | 2025-10-15 |
| `khaled.alshamaa/ar-php` | v6.3.4 | LGPL-3.0 | 直接 | 2023-04-04 |
| `tecnickcom/tcpdf` | 6.11.4 | LGPL-3.0-or-later | 直接 | 2026-08-28 |
| `anper/iuliia` | v1.3.0 | MIT | 直接 | 2026-06-27 |
| `composer/pcre` | 3.4.0 | MIT | 传递 | 2026-06-07 |
| `goldspecdigital/oooas` | dev-fix-implicitely-marking-parameter-as-nullable-deprecations | MIT | 直接 | 2025-10-28 |
| `guzzlehttp/guzzle` | 7.15.5 | MIT | 传递 | 2026-08-24 |
| `guzzlehttp/promises` | 2.5.3 | MIT | 传递 | 2026-08-24 |
| `guzzlehttp/psr7` | 2.13.1 | MIT | 传递 | 2026-08-24 |
| `laravel/serializable-closure` | v2.0.16 | MIT | 传递 | 2026-08-18 |
| `league/oauth2-client` | 2.9.0 | MIT | 直接 | 2025-11-25 |
| `league/oauth2-google` | 3.0.4 | MIT | 直接 | 2021-01-27 |
| `maennchen/zipstream-php` | 3.1.1 | MIT | 传递 | 2024-10-10 |
| `markbaker/complex` | 3.0.2 | MIT | 传递 | 2022-12-06 |
| `markbaker/matrix` | 3.0.1 | MIT | 传递 | 2022-12-02 |
| `mk-j/php_xlsxwriter` | 0.39 | MIT | 直接 | 2023-05-31 |
| `paragonie/constant_time_encoding` | v3.1.3 | MIT | 传递 | 2025-09-24 |
| `paragonie/random_compat` | v9.99.100 | MIT | 传递 | 2020-10-15 |
| `php-di/invoker` | 2.3.7 | MIT | 传递 | 2025-08-30 |
| `php-di/php-di` | 7.1.1 | MIT | 直接 | 2025-08-16 |
| `phpoffice/phpspreadsheet` | 5.8.1 | MIT | 直接 | 2026-07-12 |
| `phpseclib/bcmath_compat` | 2.0.3 | MIT | 直接 | 2024-06-06 |
| `phpseclib/phpseclib` | 3.0.57 | MIT | 直接 | 2026-08-26 |
| `psr/container` | 2.0.2 | MIT | 传递 | 2021-11-05 |
| `psr/http-client` | 1.0.3 | MIT | 传递 | 2023-09-23 |
| `psr/http-factory` | 1.1.0 | MIT | 传递 | 2024-04-15 |
| `psr/http-message` | 1.1 | MIT | 传递 | 2023-04-04 |
| `psr/simple-cache` | 3.0.0 | MIT | 传递 | 2021-10-29 |
| `ralouphie/getallheaders` | 3.0.3 | MIT | 传递 | 2019-03-08 |
| `symfony/deprecation-contracts` | v3.7.1 | MIT | 传递 | 2026-06-05 |
| `symfony/polyfill-ctype` | v1.37.0 | MIT | 传递 | 2026-04-10 |
| `symfony/polyfill-mbstring` | v1.38.2 | MIT | 直接 | 2026-05-27 |
| `symfony/polyfill-php80` | v1.37.0 | MIT | 传递 | 2026-04-10 |
| `symfony/polyfill-php81` | v1.38.1 | MIT | 传递 | 2026-05-26 |
| `symfony/polyfill-php82` | v1.38.1 | MIT | 传递 | 2026-05-26 |
| `symfony/polyfill-php83` | v1.41.0 | MIT | 传递 | 2026-07-01 |
| `tiamo/spss` | dev-master | MIT | 直接 | 2026-07-31 |
| `viniciusgava/google-translate-api` | 3.1.0 | MIT | 直接 | 2022-01-11 |

## 4. 开发依赖完整清单（64，按许可证排序）

开发依赖**不随产品分发**（`vendor/` 中只提交了 `--no-dev` 的 45 个包），因此其许可证义务仅限于内部构建环境。唯一值得注意的是 `netresearch/jsonmapper` 的 OSL-3.0（Open Software License 3.0）——OSL 含网络分发条款且被 FSF 认定与 GPL 不兼容，但它是 `vimeo/psalm` 的传递依赖，仅在静态分析时加载，不进入任何交付物。

| 包 | 版本 | 许可证 | 引入方式 | 发布日期 |
|---|---|---|---|---|
| `hamcrest/hamcrest-php` | v3.0.0 | BSD-3-Clause | 传递 | 2026-03-17 |
| `mockery/mockery` | 1.6.15 | BSD-3-Clause | 直接 | 2026-08-19 |
| `nikic/php-parser` | v4.19.5 | BSD-3-Clause | 传递 | 2025-12-06 |
| `pdepend/pdepend` | 2.16.2 | BSD-3-Clause | 传递 | 2023-12-17 |
| `phar-io/manifest` | 2.0.4 | BSD-3-Clause | 传递 | 2024-03-03 |
| `phar-io/version` | 3.2.1 | BSD-3-Clause | 传递 | 2022-02-21 |
| `phpmd/phpmd` | 2.15.0 | BSD-3-Clause | 直接 | 2023-12-11 |
| `phpunit/php-code-coverage` | 9.2.32 | BSD-3-Clause | 传递 | 2024-08-22 |
| `phpunit/php-file-iterator` | 3.0.6 | BSD-3-Clause | 传递 | 2021-12-02 |
| `phpunit/php-invoker` | 3.1.1 | BSD-3-Clause | 传递 | 2020-09-28 |
| `phpunit/php-text-template` | 2.0.4 | BSD-3-Clause | 传递 | 2020-10-26 |
| `phpunit/php-timer` | 5.0.3 | BSD-3-Clause | 传递 | 2020-10-26 |
| `phpunit/phpunit` | 9.6.36 | BSD-3-Clause | 直接 | 2026-08-11 |
| `sebastian/cli-parser` | 1.0.2 | BSD-3-Clause | 传递 | 2024-03-02 |
| `sebastian/code-unit` | 1.0.8 | BSD-3-Clause | 传递 | 2020-10-26 |
| `sebastian/code-unit-reverse-lookup` | 2.0.3 | BSD-3-Clause | 传递 | 2020-09-28 |
| `sebastian/comparator` | 4.0.10 | BSD-3-Clause | 传递 | 2026-01-24 |
| `sebastian/complexity` | 2.0.3 | BSD-3-Clause | 传递 | 2023-12-22 |
| `sebastian/diff` | 4.0.6 | BSD-3-Clause | 传递 | 2024-03-02 |
| `sebastian/environment` | 5.1.5 | BSD-3-Clause | 传递 | 2023-02-03 |
| `sebastian/exporter` | 4.0.9 | BSD-3-Clause | 传递 | 2026-08-11 |
| `sebastian/global-state` | 5.0.8 | BSD-3-Clause | 传递 | 2025-08-10 |
| `sebastian/lines-of-code` | 1.0.4 | BSD-3-Clause | 传递 | 2023-12-22 |
| `sebastian/object-enumerator` | 4.0.4 | BSD-3-Clause | 传递 | 2020-10-26 |
| `sebastian/object-reflector` | 2.0.4 | BSD-3-Clause | 传递 | 2020-10-26 |
| `sebastian/recursion-context` | 4.0.7 | BSD-3-Clause | 传递 | 2026-08-11 |
| `sebastian/resource-operations` | 3.0.4 | BSD-3-Clause | 传递 | 2024-03-14 |
| `sebastian/type` | 3.2.1 | BSD-3-Clause | 传递 | 2023-02-03 |
| `sebastian/version` | 3.0.2 | BSD-3-Clause | 传递 | 2020-09-28 |
| `squizlabs/php_codesniffer` | 3.13.6 | BSD-3-Clause | 直接 | 2026-08-06 |
| `theseer/tokenizer` | 1.3.1 | BSD-3-Clause | 传递 | 2025-11-17 |
| `felixfbecker/advanced-json-rpc` | v3.2.1 | ISC | 传递 | 2021-06-11 |
| `felixfbecker/language-server-protocol` | v1.5.3 | ISC | 传递 | 2024-04-30 |
| `amphp/amp` | v2.6.5 | MIT | 传递 | 2025-09-03 |
| `amphp/byte-stream` | v1.8.2 | MIT | 传递 | 2024-04-13 |
| `composer/semver` | 3.4.4 | MIT | 传递 | 2025-08-20 |
| `composer/xdebug-handler` | 3.0.5 | MIT | 传递 | 2024-05-06 |
| `dnoegel/php-xdg-base-dir` | v0.1.1 | MIT | 传递 | 2019-12-04 |
| `doctrine/deprecations` | 1.1.6 | MIT | 传递 | 2026-02-07 |
| `doctrine/instantiator` | 2.0.0 | MIT | 传递 | 2022-12-30 |
| `fidry/cpu-core-counter` | 1.3.0 | MIT | 传递 | 2025-08-14 |
| `misantron/dbunit` | 5.4.0 | MIT | 直接 | 2025-02-03 |
| `myclabs/deep-copy` | 1.14.0 | MIT | 传递 | 2026-08-11 |
| `php-webdriver/webdriver` | 1.16.0 | MIT | 直接 | 2025-12-28 |
| `phpdocumentor/reflection-common` | 2.2.0 | MIT | 传递 | 2020-06-27 |
| `phpdocumentor/reflection-docblock` | 5.6.7 | MIT | 传递 | 2026-03-18 |
| `phpdocumentor/type-resolver` | 1.12.0 | MIT | 传递 | 2025-11-21 |
| `phpstan/phpdoc-parser` | 2.3.4 | MIT | 传递 | 2026-08-30 |
| `psr/log` | 3.0.2 | MIT | 传递 | 2024-09-11 |
| `spatie/array-to-xml` | 3.4.4 | MIT | 传递 | 2025-12-15 |
| `symfony/config` | v6.4.44 | MIT | 传递 | 2026-08-20 |
| `symfony/console` | v6.4.45 | MIT | 传递 | 2026-08-25 |
| `symfony/dependency-injection` | v6.4.44 | MIT | 传递 | 2026-08-21 |
| `symfony/filesystem` | v6.4.45 | MIT | 传递 | 2026-08-23 |
| `symfony/polyfill-intl-grapheme` | v1.41.0 | MIT | 传递 | 2026-07-28 |
| `symfony/polyfill-intl-normalizer` | v1.42.0 | MIT | 传递 | 2026-08-07 |
| `symfony/process` | v6.4.45 | MIT | 传递 | 2026-08-20 |
| `symfony/service-contracts` | v3.7.3 | MIT | 传递 | 2026-07-27 |
| `symfony/string` | v6.4.43 | MIT | 传递 | 2026-07-28 |
| `symfony/var-exporter` | v6.4.45 | MIT | 传递 | 2026-08-23 |
| `symfony/yaml` | v6.4.45 | MIT | 传递 | 2026-08-30 |
| `vimeo/psalm` | 5.26.1 | MIT | 直接 | 2024-09-08 |
| `webmozart/assert` | 1.12.1 | MIT | 传递 | 2025-10-29 |
| `netresearch/jsonmapper` | v4.5.0 | OSL-3.0 | 传递 | 2024-09-08 |

## 5. composer 视野之外的 PHP 第三方代码

`composer.lock` 并不覆盖引擎全部的第三方 PHP 代码。下列组件以源码形式直接提交在仓库里，不受 composer 管理，**升级与安全公告都没有自动通道**，必须手工跟踪：

| 路径 | 组件 | 版本 | 许可证（来自文件内声明） |
|---|---|---|---|
| `application/core/plugins/TwoFactorAdminLogin/vendor/robthree/twofactorauth` | RobThree TwoFactorAuth | 1.6.5 | MIT（内嵌独立 `vendor/` 与 `installed.json`） |
| `application/core/plugins/TwoFactorAdminLogin/helper/phpqrcode.php` | PHP QR Code | — | **LGPL-3.0-or-later**（基于 LGPL-2.1 的 libqrencode） |
| `assets/packages/kcfinder` | KCFinder | 3.13.0-limesurvey（已被 LimeSurvey 改过） | **GPL-3.0 / LGPL-3.0 双许可**，`doc/LICENSE.GPL`＋`doc/LICENSE.LGPL` |
| `assets/packages/pchart` | pChart | 1.27d（2008-09-30） | **GPL（"version 1,2,3 or later"，措辞不规范）**，上游自 2008 年即无维护 |
| `application/extensions/yiiwheels` | YiiWheels（2amigos） | — | New BSD（文件头 `@license .../bsd-license.php`） |
| `application/extensions/bootstrap5` | Yii Bootstrap 扩展（C. Niska） | — | New BSD |
| `application/extensions/yii-jsoneditor/jsoneditor-2.3.6` | JSONEditor | 2.3.6 | **Apache-2.0** |
| `application/extensions/captchaExtended` | Captcha Extended | — | **自定义自由文本许可**（"free software … not obliged to retain any copyrights"），非 SPDX 标准，需要律师确认其真实边界 |
| `application/core/plugins/customToken` | customToken | — | MIT |
| `application/core/plugins/ExpressionAnswerOptions` | 引擎自带插件 | — | 目录内自带 **GPL-3.0 全文**（与根 `LICENSE` 的 GPL-2 不同） |
| `assets/fonts/`（DejaVu、FreeSans、IBM Plex、Noto、fireflysung、UnBatang、TlwgTypist） | 字体 | — | DejaVu License、SIL OFL 等，各自附带许可文本 |

其中 **`application/extensions/yii-jsoneditor` 的 Apache-2.0** 与 **`assets/packages/kcfinder` 的 GPL-3.0/LGPL-3.0** 是与 `greew/oauth2-azure-provider` 并列的、把组合作品推向 GPL-3 的第二、三条独立证据（Apache-2.0 与 GPL-2 单向不兼容，与 GPL-3 兼容）。

`assets/packages/ckeditor`（CKEditor 4.22.1）与 `application/extensions/yiiwheels/widgets/fileupload/assets/js/vendor` 属于前端范畴，见 [js-dependencies.md](js-dependencies.md)。

## 6. 复现方式

```sh
# 容器内，全部只读
docker exec survey-web sh -c 'cd /var/www/html && composer licenses --no-dev --format=json'
docker exec survey-web sh -c 'cd /var/www/html && composer licenses --format=json'
docker exec survey-web sh -c 'cd /var/www/html && composer show --format=json'
docker exec survey-web sh -c 'cd /var/www/html && composer audit'
docker exec survey-web sh -c 'cd /var/www/html && composer show --outdated --direct'
```

网络说明：本次采集中 packagist 可达，`composer audit` 与 `composer outdated` 均在约 1 分钟内返回。ADR 0001 与 progress.md（00.8）记录过 packagist 超时问题，若复现时命令无法联网，应**退回到 `composer.lock` 的元数据**（许可证、版本、发布时间都在锁文件里），并在文档中标注"过期/漏洞数据未采集"，不得凭记忆填写。

## 7. 待办

- [ ] `tiamo/spss` 与 `goldspecdigital/oooas` 的 dev 分支锁定：评估是否在私有镜像中固定为不可变快照（关联 00.8 的内网镜像源方案）。
- [ ] 为第 5 节的非 composer 组件建立手工跟踪表（CKEditor 4 已 EOL，pChart 自 2008 年无维护）。
- [ ] 律师确认 `phpmailer` LGPL-2.1-only 的升级路径与 `captchaExtended` 自定义许可的边界，见 [licence-analysis.md](licence-analysis.md) §6。
