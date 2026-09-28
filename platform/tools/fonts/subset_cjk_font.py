#!/usr/bin/env python3
"""从 Noto Sans CJK SC 生成随交付自带、可嵌入 PDF 的中文字体子集。

产物是 `platform/services/business/src/main/resources/fonts/MJYSansSC-Regular.ttf`，
由 `PdfFontProgram` 在运行期读出、按用到的字形再做一次裁剪后嵌进 PDF（ADR 0015 增补六）。

## 为什么要有这一步

1. **体积**：Noto Sans CJK 全字集 65 535 个字形、CFF 约 16 MB，不能随仓库交付，更不能每份 PDF 嵌一遍。
2. **轮廓格式**：上游是 CFF（`sfntVersion == "OTTO"`）。PDF 里嵌 CFF 要走 CIDFontType0／FontFile3，
   而 PDF 1.4 时代就通行、各家阅读器实现最一致的是 CIDFontType2／FontFile2（TrueType `glyf`）。
   所以这里把三次贝塞尔曲线转成二次曲线（cu2qu），换成 `glyf` 轮廓。
3. **改名**：见下面「许可」。

## 子集策略

字符集 = GB/T 2312-80 的可打印区 ∪ ASCII ∪ 一小把排版符号。用 Python 自带的 `gb2312` 编解码器
枚举，不依赖任何外部字表，也不需要联网：

- 汉字区 0xB0–0xF7 × 0xA1–0xFE：一级字 3755 ＋ 二级字 3008，共 6763 字。
- 符号区 0xA1–0xA8：中文标点、数字序号（①②／ⅠⅡ）、全角 ASCII、希腊字母、西里尔字母、带调拼音。
- 制表符区 0xA9（┌─┐ 那一套）**不要**：导出的表格用真正的 PDF 线段画，不靠字符拼。
- 日文假名区 0xA4／0xA5 **不要**：本产品是中文本土化，假名只在极少数作答里出现，
  由下面的兜底处理（不是排版失败）。

**取舍**：子集外的字符（繁体专用字、假名、谚文、emoji、生僻字）在页面上画成 `〓`（U+3013，
GB2312 A1 区里就有），但 **ToUnicode 里仍然映射到它真实的码位**——所以复制／检索／第三方读回
拿到的还是原文，只有肉眼看到的那一个字是替代符。做法见 `PdfFontProgram`：编码是自己分配的
2 字节码，经 CIDToGIDMap 流指到字形，所以「两个不同字符共用一个字形」与「各自映射回自己的
Unicode」可以同时成立。

宁愿这样也不整份作业失败：作答内容是作答者给的，不能因为一个 emoji 就导不出来。

## 许可（SIL OFL 1.1）

上游：Noto Sans CJK（= 思源黑体 / Source Han Sans），Copyright © 2014-2021 Adobe，OFL 1.1。

这里做了**两件修改**（取子集、CFF→glyf），所以按 OFL 的保留字体名条款处理：

- **改名**：family / PostScript / full name 一律换成 `MJY Sans SC` / `MJYSansSC-Regular`，
  不含 Noto、Source、Adobe、Google 任何一个词。OFL 第 5 条只禁止用**保留字体名**做衍生作品的名字；
  我们查到的离线证据（字体 name ID 13 的许可声明、Debian `fonts-noto-cjk` 的 copyright 文件）
  都没有声明保留字体名，但 Source Han Sans 一脉历史上保留过 "Source"，而 "Noto" 是 Google 的商标
  （name ID 7 写着）。改名以后无论上游保留了什么，都不会踩到——不去赌哪个词被保留了。
- **保留原始声明**：name ID 0（版权）、7（商标）、8（厂商）、9（设计师）、13（许可）、14（许可地址）
  原样留在产物里，OFL 第 1、2 条要求版权声明与许可随字体一起走。许可全文另存 `OFL.txt`。

## 用法

需要 fontTools（≥ 4.20，cu2qu 已并入）。本机没装就在有 fontTools 的容器里跑，别联网装：

    python3 platform/tools/fonts/subset_cjk_font.py \\
        --source /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc \\
        --out platform/services/business/src/main/resources/fonts/MJYSansSC-Regular.ttf

来源与当时的取法记在同目录的 `PROVENANCE.md` 里。
"""

import argparse
import sys
from pathlib import Path

#: TTC 里 Noto Sans CJK SC 的下标（0=JP 1=KR 2=SC 3=TC 4=HK，5–9 是等宽的同五种）。
SC_FONT_NUMBER = 2

#: 改名后的名字。不含上游任何可能被保留的字体名。
FAMILY = "MJY Sans SC"
SUBFAMILY = "Regular"
POSTSCRIPT = "MJYSansSC-Regular"
VERSION = "Version 1.000"

#: 改名只动这些 name ID；版权（0）、商标（7）、厂商（8）、设计师（9）、许可（13）、地址（14）不动。
RENAMED = {1: FAMILY, 2: SUBFAMILY, 4: f"{FAMILY} {SUBFAMILY}", 5: VERSION, 6: POSTSCRIPT,
           16: FAMILY, 17: SUBFAMILY}
#: 唯一标识符要跟着改，否则与上游撞名。
UNIQUE_ID = 3

#: GB2312 里要取的区（高字节）。0xA9 制表符与 0xA4／0xA5 假名不取，理由见模块开头。
SYMBOL_ROWS = (0xA1, 0xA2, 0xA3, 0xA6, 0xA7, 0xA8)
HANZI_ROWS = range(0xB0, 0xF8)

#: 额外的排版符号：GB2312 没有，但中文排版里常见。
EXTRA_CHARS = " §°·×÷–—‘’“”…‰﻿"

#: 子集外字符的替代字形（〓，GB2312 A1 区内）。必须在子集里，否则兜底也画不出来。
SUBSTITUTE = "〓"

#: 丢掉的表：版面（GSUB/GPOS/GDEF/BASE）与竖排（vhea/vmtx/VORG）都用不上——
#: PDF 写出器自己排版，只横排。留着它们白占体积，而且引用了被裁掉的字形。
DROPPED_TABLES = ["BASE", "GDEF", "GPOS", "GSUB", "VORG", "vhea", "vmtx", "DSIG", "LTSH", "hdmx", "VDMX"]


def gb2312_charset():
    """按 GB2312 编码枚举要保留的字符，返回码位集合。"""
    chars = {chr(c) for c in range(0x20, 0x7F)}
    chars.update(EXTRA_CHARS)
    chars.add(SUBSTITUTE)
    for high in list(SYMBOL_ROWS) + list(HANZI_ROWS):
        for low in range(0xA1, 0xFF):
            try:
                chars.add(bytes((high, low)).decode("gb2312"))
            except UnicodeDecodeError:
                continue  # 区内的空位
    return {ord(c) for c in chars}


def subset(font, unicodes):
    """按码位裁剪字形，顺带丢掉用不上的表。"""
    from fontTools import subset as ft_subset

    options = ft_subset.Options()
    options.drop_tables += DROPPED_TABLES
    options.layout_features = []
    options.name_IDs = ["*"]
    options.name_legacy = True
    options.notdef_outline = True
    options.recalc_bounds = True
    options.recommended_glyphs = True
    # CFF 的子程序（subr）在转 glyf 之前先展开，省掉一层间接。
    options.desubroutinize = True
    subsetter = ft_subset.Subsetter(options=options)
    subsetter.populate(unicodes=sorted(unicodes))
    subsetter.subset(font)


def to_truetype(font, max_err=1.0):
    """CFF（三次贝塞尔）→ glyf（二次贝塞尔）。PDF 侧因此能走 CIDFontType2 / FontFile2。"""
    from fontTools.pens.cu2quPen import Cu2QuPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib import newTable

    if font.sfntVersion != "OTTO":
        raise SystemExit(f"源字体不是 CFF（sfntVersion={font.sfntVersion!r}），不需要转换")

    glyph_order = font.getGlyphOrder()
    glyph_set = font.getGlyphSet()
    glyf = newTable("glyf")
    glyf.glyphOrder = glyph_order
    glyf.glyphs = {}
    for name in glyph_order:
        pen = TTGlyphPen(glyph_set)
        glyph_set[name].draw(Cu2QuPen(pen, max_err, reverse_direction=True))
        glyf.glyphs[name] = pen.glyph()

    font["loca"] = newTable("loca")
    font["glyf"] = glyf
    font["maxp"] = maxp = newTable("maxp")
    maxp.tableVersion = 0x00010000  # 1.0：字形数据在 glyf 里；其余字段 compile 时由 glyf 重算
    maxp.maxZones = 1
    maxp.maxTwilightPoints = 0
    maxp.maxStorage = 0
    maxp.maxFunctionDefs = 0
    maxp.maxInstructionDefs = 0
    maxp.maxStackElements = 0
    maxp.maxSizeOfInstructions = 0
    maxp.maxComponentElements = 0
    maxp.numGlyphs = len(glyph_order)
    # post 3.0：不带字形名。字形名对 PDF 无用（走 GID），留着反而占几十 KB。
    font["post"].formatType = 3.0
    del font["CFF "]
    font.sfntVersion = "\000\001\000\000"
    font.setGlyphOrder(glyph_order)


def rename(font):
    """改名，并保留上游的版权、商标、设计师与许可声明（OFL 第 1、2 条）。"""
    name = font["name"]
    for name_id, value in RENAMED.items():
        name.setName(value, name_id, 3, 1, 0x409)
        name.setName(value, name_id, 1, 0, 0)
    name.setName(f"{VERSION};{POSTSCRIPT}", UNIQUE_ID, 3, 1, 0x409)
    kept = set(RENAMED) | {UNIQUE_ID, 0, 7, 8, 9, 11, 13, 14}
    name.names = [n for n in name.names if n.nameID in kept
                  and (n.platformID, n.platEncID, n.langID) in ((3, 1, 0x409), (1, 0, 0))]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, type=Path,
                        help="NotoSansCJK-Regular.ttc（Debian fonts-noto-cjk 里那一份）")
    parser.add_argument("--out", required=True, type=Path, help="产物 .ttf 的路径")
    parser.add_argument("--font-number", type=int, default=SC_FONT_NUMBER, help="TTC 内下标，默认 SC")
    args = parser.parse_args(argv)

    try:
        from fontTools.ttLib import TTFont
    except ImportError:  # pragma: no cover - 环境问题，不是逻辑问题
        raise SystemExit("需要 fontTools（≥4.20）。别联网装：在有它的容器里跑这个脚本。")

    unicodes = gb2312_charset()
    font = TTFont(args.source, fontNumber=args.font_number)
    family = font["name"].getDebugName(1)
    print(f"源字体：{family}（{args.source}），字形数 {font['maxp'].numGlyphs}")
    print(f"子集字符数：{len(unicodes)}")

    subset(font, unicodes)
    to_truetype(font)
    rename(font)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    font.save(args.out)
    print(f"产物：{args.out}（{args.out.stat().st_size} 字节，字形数 {font['maxp'].numGlyphs}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
