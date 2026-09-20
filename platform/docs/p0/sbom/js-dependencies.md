# 前端依赖清单（SBOM）

- 任务：P0-00.2 引擎认证
- 采集日期：2026-09-20
- 对象：引擎仓库 `master` @ `4c20c680`（LimeSurvey 7.1.2），工作分支 `mjy/main` @ `60600bc1`
- 数据来源：根 `package.json`、根 `yarn.lock`、`.bowerrc`、`application/config/vendor.php`、以及**已提交进 git 的**前端产物（`node_modules/`、`assets/`）
- **未执行 `yarn install` / `npm install`**（网络不稳、磁盘受限）。因此：凡是仓库里没有提交对应文件的包，其许可证一律标注"**未核实**"，不做推断填写

## 1. 关键结论：前端依赖是"已 vendoring 的"

与常规 Node 项目不同，本引擎把运行时前端资产**直接提交进仓库**：

| 目录 | git 跟踪文件数 | 内容 |
|---|---|---|
| `node_modules/` | 3,055 | 只有 22 个运行时包的分发文件（`dist/`、`min/`），**不含任何 devDependency** |
| `assets/` | 5,942 | 构建产物、CKEditor、KCFinder、字体、主题 |

`application/config/vendor.php` 把浏览器加载的每个资源包都映射到这两个目录（`node_modules/jquery/dist`、`assets/bootstrap_5/build` 等），因此：

1. **`yarn install` 不是运行前提**，磁盘上现有的 `node_modules/` 就是 git 里那份；本次核查确认磁盘上除这 22 个包外没有其它已安装包。
2. **根 `yarn.lock` 里的 622 个包中，绝大多数只在构建期出现**（gulp/browserify/babel/sass 工具链），不进入交付物，许可证暴露面有限。
3. 但 **`.bowerrc` 把 bower 的安装目录设为 `vendor`**——与 composer 的 `vendor-dir` 相同。仓库内**没有** `bower.json`，这是一条遗留配置；如果有人误跑 `bower install`，会把前端包写进 PHP 的 `vendor/` 目录。建议在私有化构建脚本里显式禁止 bower。

## 2. 运行时前端依赖（随产品分发）

"许可证来源"列说明证据出处：`package.json` 指该包在 `node_modules/` 下被提交的清单文件；`banner` 指被提交的 JS/CSS 文件头部的版权声明；`LICENSE` 指被提交的许可证全文文件。

| 包 | `package.json` 声明 | `yarn.lock` 解析 | 许可证 | 许可证来源 | 引入方式 |
|---|---|---|---|---|---|
| `ace-builds` | ^1.10.0 | 1.44.0 | BSD-3-Clause | package.json ＋ LICENSE | 直接 |
| `chart.js` | ^4.4.7 | 4.5.1 | MIT | LICENSE.md ＋ banner | 直接 |
| `datatables.net` | （传递） | 1.13.11 | MIT | package.json | 传递（`datatables.net-bs5`） |
| `datatables.net-bs5` | ^1.12.1 | 1.13.11 | MIT | package.json | 直接 |
| `decimal.js` | ^10.4.0 | 10.6.0 | MIT | package.json | 直接 |
| `devbridge-autocomplete` | ^2.0.4 | 2.0.4 | MIT | package.json | 直接 |
| `dom-to-image` | ^2.6.0 | 2.6.0 | MIT | package.json | 直接 |
| `jquery` | ^3.6.1 | 3.7.1 | MIT | package.json | 直接 |
| `jquery-migrate` | ^3.4.0 | 3.6.0 | MIT | banner（"Released under the MIT license"） | 直接 |
| `jquery-ui-dist` | ^1.13.2 | 1.13.3 | MIT | package.json | 直接 |
| `jquery-ui-touch-punch` | ^0.2.3 | 0.2.3 | **MIT 或 GPL-2（自由文本，非 SPDX）** | package.json：`"Dual licensed under the MIT or GPL Version 2 licenses."` | 直接 |
| `jquery.actual` | ^1.0.19 | 1.0.19 | MIT | package.json | 直接 |
| `js-cookie` | ^3.0.1 | 3.0.8 | MIT | banner（`/*! js-cookie v3.0.8 \| MIT */`） | 直接 |
| `jspdf` | ^4.2.1 | 4.2.1 | MIT | package.json ＋ banner | 直接 |
| `jsuri` | ^1.3.1 | 1.3.1 | **清单未声明**；`LICENSE` 为 MIT 全文 | LICENSE | 直接 |
| `jszip` | ^3.10.1 | 3.10.1 | **`(MIT OR GPL-3.0-or-later)` 双许可** | package.json | 直接 |
| `leaflet` | ^1.8.0 | 1.9.4 | **未核实**（仓库内只提交 `dist/`，无 LICENSE，banner 无许可声明；上游为 BSD-2-Clause） | — | 直接 |
| `moment` | ^2.29.4 | 2.30.1 | **未核实**（只提交 `min/`，无 LICENSE，压缩产物无 banner；上游为 MIT） | — | 直接 |
| `nestedSortable` | `git+https://github.com/ilikenwf/nestedSortable#d0173995…` | 1.3.4 | **清单未声明**；源码头部写 "Licensed under the MIT License" | 源码 banner | 直接（**git 依赖**） |
| `select2` | ^4.1.0 | 4.1.0 | MIT | banner | 直接 |
| `select2-bootstrap-5-theme` | ^1.3.0 | 1.3.0 | **未核实**（只提交 `dist/`，banner 仅有版本号；上游为 MIT） | — | 直接 |
| `tablesorter` | ^2.31.3 | 2.32.0 | **MIT 与 GPL 双许可（自由文本，非 SPDX）** | banner："Dual licensed under the MIT and GPL licenses" | 直接 |

### 2.1 以构建产物形式分发、不在 `node_modules/` 的运行时依赖

| 组件 | 版本（产物内声明） | `yarn.lock` 版本 | 许可证 | 产物路径 |
|---|---|---|---|---|
| Bootstrap | — | 5.1.3（`resolutions` 锁死） | MIT（上游；**产物内无 banner**） | `assets/bootstrap_5/build/js/bootstrap_5.min.js`、`build/css/*` |
| Tempus Dominus | **v6.9.4** | **6.10.4** | MIT（非压缩产物保留 banner） | `assets/packages/datetimepicker/build/popper-tempus.js` |
| `@popperjs/core` | v2.11.6 | 2.11.x | MIT（非压缩产物保留 banner） | 同上（与 Tempus Dominus 打进同一个 bundle） |

**漂移告警**：`popper-tempus.js` 内声明 Tempus Dominus **v6.9.4**，而 `yarn.lock` 解析为 **6.10.4**。提交的产物比锁文件旧，重新执行 `yarn gulp` 会静默改变分发内容。私有化交付前必须确认以哪一份为准。

**注意**：`@popperjs/core` 在根 `package.json` 里被列为 `devDependencies`，但它的代码确实随产物分发。"dev 依赖不分发"这条一般规则在本仓库**不成立**，判断依据必须是"产物里有没有它"，而不是 `package.json` 的分组。

## 3. 引擎自带的前端子工程（产物已提交）

`assets/packages/` 下有 8 个独立的前端子工程，各自带 `package.json` 与 `yarn.lock`，其 `build/` 产物已提交（共 46 个构建产物文件）。这些产物把各自的运行时依赖**打包进去了**。

| 子工程 | `package.json` 声明的许可证 | 打包进产物的运行时依赖 | 自身 `yarn.lock` 条目数 |
|---|---|---|---|
| `adminbasics` | **GPL-3.0** | `lodash` ^4.18.1（MIT） | 493 |
| `adminsidepanel` | 未声明 | `core-js` ^3.33.0（MIT） | 358 |
| `embeddables` | **GPL3.0**（非 SPDX 写法） | `embedo` ^1.12.0（MIT） | 211 |
| `globalsidepanel` | 未声明 | `core-js` ^3.38.1（MIT） | 234 |
| `lslog` | **GPL-3.0** | 无 | 187 |
| `lstutorial` | **GPL-3.0** | `popper.js` ^1.15.0（MIT） | 291 |
| `pjax` | **GPL3.0**（非 SPDX 写法） | 无 | 5 |
| `questions/upload` | **GPL-3** | 无 | 281 |
| `surveymenufunctions` | **GPL2.0**（非 SPDX 写法） | 无 | 无 lock |

两条要点：

1. **许可证自相矛盾**：根 `LICENSE` 与根 `composer.json` 声明 GPL-2.0-or-later，而引擎自己的 8 个前端子工程里有 6 个声明 GPL-3.0（含变体写法），1 个声明 GPL2.0，2 个完全未声明。这是引擎内部的声明不一致，详见 [licence-analysis.md](licence-analysis.md) §2。
2. **署名缺失**：压缩产物被 terser/uglify 剥掉了全部注释。核查结果：
   - `assets/packages/adminbasics/build/adminbasics.js` 打包了 lodash（bundle 内 80 处 lodash 内部标识），但整个文件只有一条 `Copyright (C) 2007-2026 The LimeSurvey Project Team`，**没有 lodash 的 MIT 版权声明**。
   - `adminbasics.min.js`、`embeddables.min.js`、`popper-tempus.min.js`、`bootstrap_5.min.js` 中 `Copyright` 出现次数为 **0**。

   MIT 要求"在软件的所有副本或实质部分中保留版权声明与许可声明"。压缩产物里一条都没有，是私有化交付时可被追责的具体缺口，见 [licence-analysis.md](licence-analysis.md) §5。

## 4. 不由 npm 管理的前端第三方代码

这部分完全在 `package.json` 与 `yarn.lock` 的视野之外，只能靠仓库内文件识别，**没有任何自动升级或安全公告通道**：

| 路径 | 组件 | 版本 | 许可证 | 备注 |
|---|---|---|---|---|
| `assets/packages/ckeditor` | CKEditor 4 | **4.22.1**（27 MB） | GPL-2.0-or-later **或** LGPL-2.1-or-later **或** MPL-1.1-or-later（三选一，见 `LICENSE.md`） | **开源版已于 2023-06 EOL**。仓库内 `CHANGES.md` 原文警告"CKEditor 4（开源版）不再维护"，后续安全补丁需购买 Extended Support Model 商业授权。许可证本身可用，**风险是安全维护而非授权** |
| `assets/packages/kcfinder` | KCFinder | 3.13.0-limesurvey（LimeSurvey 已改动） | GPL-3.0 **或** LGPL-3.0（`doc/LICENSE.GPL`、`doc/LICENSE.LGPL`） | 文件上传浏览器，PHP＋JS 混合 |
| `assets/packages/bootstrap` | Bootstrap 3 | **3.4.1**（2019） | MIT（banner 完整） | 旧版，与 `assets/bootstrap_5` 并存 |
| `assets/packages/sortablejs` | SortableJS | 1.15.0 | MIT（banner 完整） | |
| `assets/packages/pchart` | pChart | 1.27d（2008） | GPL（措辞为 "version 1,2,3 or later"，非标准） | 服务端绘图，上游 2008 年后无维护 |
| `application/extensions/yiiwheels` | YiiWheels（2amigos） | — | New BSD | 内含自己的 `widgets/fileupload/assets/js/vendor` 第三方 JS |
| `application/extensions/yii-jsoneditor/jsoneditor-2.3.6` | JSONEditor | 2.3.6 | **Apache-2.0** | 与 GPL-2 单向不兼容，与 GPL-3 兼容 |
| `application/extensions/bootstrap5` | Yii Bootstrap 扩展 | — | New BSD | |
| `application/extensions/captchaExtended` | Captcha Extended | — | **自定义自由文本许可** | 原文："free software … You are not obliged to retain any copyrights"。非 SPDX，边界需律师确认 |
| `assets/fonts/` | DejaVu、FreeSans、IBM Plex、Noto、fireflysung（中文）、UnBatang（韩文）、TlwgTypist（泰文） | — | DejaVu License、SIL OFL 等，各自附带许可文本 | 字体许可通常要求随分发附带许可全文；这些文本目前在 `assets/fonts/` 内，私有化打包时不得剔除 |

## 5. 构建期依赖（不分发）

| 锁文件 | 条目数 | 说明 |
|---|---|---|
| 根 `yarn.lock` | 622 | gulp 5、browserify、babel、sass、postcss、cssnano 等工具链 |
| `assets/packages/*/yarn.lock` | 合计 2,060 | rollup / webpack / jest / terser 等 |

**许可证：全部未核实。** 这些包的 `node_modules` 没有提交、本次也未执行安装，因此仓库内不存在可读的许可证证据。不做推断。

风险评估：构建期依赖不随交付物分发，普通 copyleft 不会传染。但需要注意两点——第 2.1 节已证明"dev 依赖不分发"在本仓库不成立（`@popperjs/core` 就是反例）；`gulp-rtlcss`、`sass`、`cssnano` 之类的工具其**输出**是否带有工具自身的许可证声明，需要在真正生成交付物时逐个确认。

**建议的后续动作**（需要可用网络时执行一次即可）：在隔离环境跑一次 `yarn install --frozen-lockfile` 后执行 `yarn licenses list`，把 622 ＋ 2,060 个包的许可证快照固化成文件提交到本目录，之后就不再需要联网。在此之前，本节保持"未核实"。

## 6. 复现方式

```sh
# 全部为只读，不执行任何安装
cat package.json yarn.lock .bowerrc
git ls-files node_modules | awk -F/ '{print $2}' | sort -u     # 已提交的运行时包
git ls-files vendor    | wc -l                                  # PHP vendor 也已提交
grep -o "node_modules/[a-zA-Z0-9@._/-]*" application/config/vendor.php | sort -u
```

## 7. 待办

- [ ] 固化前端许可证快照（`yarn licenses list`），消除第 5 节的"未核实"。
- [ ] 核实 `leaflet`、`moment`、`select2-bootstrap-5-theme` 的许可证并补齐仓库内证据（或在交付包中单独附带许可文本）。
- [ ] 解决 Tempus Dominus 的产物/锁文件漂移（6.9.4 vs 6.10.4）。
- [ ] 为压缩产物恢复第三方署名（terser `comments: 'some'` 或单独生成 `THIRD-PARTY-NOTICES`），见 [licence-analysis.md](licence-analysis.md) §5。
- [ ] CKEditor 4 EOL 的处置决策：购买 ESM、迁移 CKEditor 5，或接受无安全补丁的风险。这是**安全**议题，不是许可证议题，需单独立项。
- [ ] 清理或显式禁用遗留的 `.bowerrc`（`directory: vendor` 与 composer 的 vendor 目录冲突）。
