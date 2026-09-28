# MJYSansSC-Regular.ttf 的来源、子集策略与许可

PDF 导出（ADR 0015 增补五，R06-05）要求**随交付自带并嵌入**中文字体，靠阅读器自带 CJK 字体包
那条备选已被冻结工作表 P-10 否决。这个目录就是那份自带字体。

## 一、来源

| 项 | 值 |
|---|---|
| 上游字体 | Noto Sans CJK SC（即思源黑体 / Source Han Sans，简体中文那一支） |
| 上游版本 | 2.004（`name` ID 3：`2.004;GOOG;NotoSansCJKsc-Regular;ADOBE`） |
| 版权 | © 2014-2021 Adobe (http://www.adobe.com/) |
| 许可 | SIL Open Font License 1.1（全文见同目录 `OFL.txt`） |
| 取得方式 | Debian 包 `fonts-noto-cjk` 里的 `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc`，TTC 内下标 2（SC）。该文件已在本机的容器镜像里，**没有联网抓取**——本车道禁止抓任何 URL。 |
| 上游仓库 | https://github.com/notofonts/noto-cjk（仅作记录，构建过程不访问它） |

生成命令（在带 fontTools 4.63.0 的容器里、`--network none` 下跑的）：

```
python3 platform/tools/fonts/subset_cjk_font.py \
    --source .../NotoSansCJK-Regular.ttc \
    --out platform/services/business/src/main/resources/fonts/MJYSansSC-Regular.ttf
```

脚本本身是可复现的说明书：子集字符集怎么枚举、为什么转 glyf、改哪些 `name` ID，都写在它的
模块注释里。要重新生成，把上游 `.ttc` 找来再跑一遍即可；`fontTools` 只是**构建期**依赖，
运行期与测试期都不需要它（Java 侧只读这个 `.ttf`）。

## 二、子集策略与取舍

上游 65 535 个字形、CFF 约 16 MB，不能随仓库交付。产物 **7 300 个字形、1 994 108 字节**。

保留（用 Python 自带的 `gb2312` 编解码器枚举，不依赖外部字表）：

- **GB/T 2312-80 汉字区**（0xB0–0xF7）：一级字 3 755 ＋ 二级字 3 008 = 6 763 字。
- **GB2312 符号区** 0xA1（中文标点）、0xA2（①②／ⅠⅡ 等序号）、0xA3（全角 ASCII）、
  0xA6（希腊字母）、0xA7（西里尔字母）、0xA8（带调拼音）。
- ASCII 可打印区，以及 `§ ° · × ÷ – — ‘ ’ “ ” … ‰` 这一小把中文排版里常见、GB2312 又没有的符号。

**故意不要的**：

- **0xA9 制表符区**（`┌─┐` 那一套）：导出表格用真正的 PDF 线段画，不靠字符拼。
- **0xA4／0xA5 日文假名区**：本产品是中文本土化。假名落进兜底，不是排版失败。
- **繁体专用字、谚文、emoji、生僻字**：同上。要覆盖它们就得把 GBK（21 886 字）甚至全字集搬进来，
  体积是另一个量级。

### 子集外的字符怎么办

页面上画成 `〓`（U+3013，GB2312 A1 区内，所以一定画得出来），但**ToUnicode 里映射到它真实的码位**
——复制、检索、第三方读回拿到的仍是原文，只有肉眼看到的那一个字是替代符。

能做到这一点是因为 `PdfFontProgram` 用的是**自己分配的 2 字节编码**，经 CIDToGIDMap 流指向字形：
「两个不同字符共用一个字形」与「各自映射回自己的 Unicode」于是可以同时成立。

宁愿这样也不整份作业失败——作答内容是作答者给的，不能因为一个 emoji 就导不出来。
这一条由 `PdfExportFormatTest` 钉住。

## 三、轮廓格式为什么换了

上游是 CFF（`sfntVersion == "OTTO"`）。PDF 里嵌 CFF 要走 CIDFontType0 / FontFile3；而通行最久、
各家阅读器实现最一致的是 CIDFontType2 / FontFile2（TrueType `glyf`）。所以子集之后用 cu2qu
把三次贝塞尔曲线转成二次曲线，换成 `glyf` 轮廓（`max_err = 1.0` 字体单位，unitsPerEm = 1000，
即千分之一 em 的误差上限，正文字号下看不出来）。

这是对字体的**修改**，许可上的处理见下一节。

## 四、OFL 1.1 合规

做了两件修改：取子集、CFF→glyf。对应 OFL 的三条要求：

1. **第 1、2 条（版权声明与许可随字体走）**：`OFL.txt` 是 OFL 1.1 全文，开头带上游的版权声明；
   字体自己的 `name` 表里 ID 0（版权）、7（商标）、8（厂商）、9（设计师）、11（上游地址）、
   13（许可声明）、14（许可地址）**原样保留**，没有删改。
2. **第 5 条（保留字体名）**：family / PostScript / full name 一律换成 `MJY Sans SC` /
   `MJYSansSC-Regular`，**不含 Noto、Source、Adobe、Google 任何一个词**（改动的只有
   name ID 1、2、3、4、5、6、16、17）。

   我们能查到的**离线证据**里都没有声明保留字体名：字体 `name` ID 13 的许可声明是不带保留名的
   标准措辞；Debian `fonts-noto-cjk` 的 `copyright` 文件写的是 `Copyright: 2010-2012, Google
   Corporation` / `License: SIL-1.1`，同样没有 "with Reserved Font Name" 后缀。但 Source Han Sans
   一脉历史上保留过 "Source"，而 "Noto" 是 Google 的注册商标（name ID 7 就写着）。
   **改名之后，上游到底保留了什么都不影响**——不去赌哪个词被保留了，也不让一份被改过的字体
   继续挂着别人的商标。
3. **第 4 条（不得用版权持有人的名义做推广）**：本仓库的文档只把 Adobe / Google 写在「来源」里，
   没有拿它们背书。

产物与上游一样处于 OFL 1.1 之下（第 3 条要求衍生作品不得改许可），OFL 明确允许嵌入、再分发与
商用，无须另行授权。
