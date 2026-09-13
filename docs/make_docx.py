# -*- coding: utf-8 -*-
"""把 docs/部署指南-CloudBase.md 转成给队友的 Word 文件。

用法（在仓库任意位置）：
    python docs/make_docx.py

只处理这份文档实际用到的 Markdown 子集：
标题 / 段落 / 表格 / 围栏代码块 / 引用 / 无序列表 / 有序列表 / 分隔线 / 行内粗体与反引号。
不追求通用性，改 md 后重跑即可。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

HERE = Path(__file__).resolve().parent
SRC = HERE / "部署指南-CloudBase.md"
DST = HERE / "Logic-Coloc-CloudBase部署说明.docx"

# 封面默认值（部署指南用）；其它文档用 --title/--subtitle/--meta 覆盖。
DEFAULT_COVER = {
    "title": "Logic-Coloc「跨学科知识同源翻译机」",
    "subtitle": "CloudBase 云托管部署说明",
    "meta": [
        ("交付给", "负责部署的同学"),
        ("代码仓库", "https://github.com/Scarlett-yzy/logic-coloc"),
        ("分支", "main"),
    ],
}

BODY_FONT = "微软雅黑"
CODE_FONT = "Consolas"
BODY_SIZE = 10.5
CODE_SIZE = 9.0

GRAY = RGBColor(0x59, 0x59, 0x59)
RULE_COLOR = "BFBFBF"
CODE_FILL = "F5F5F5"
HEAD_FILL = "EAEAEA"


# ---------- 底层排版工具 ----------

def set_font(run, name=BODY_FONT, size=BODY_SIZE, bold=None, color=None, italic=None):
    """设置字体，并且必须同时设 eastAsia —— 否则中文会掉回默认字体。"""
    run.font.name = name
    run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if italic is not None:
        run.italic = italic
    if color is not None:
        run.font.color.rgb = color
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for attr in ("w:eastAsia", "w:ascii", "w:hAnsi"):
        rfonts.set(qn(attr), name)


def shade(element, fill):
    """给段落或单元格加底色。"""
    pr = element._element.get_or_add_pPr() if hasattr(element, "paragraph_format") else element._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), fill)
    pr.append(shd)


def bottom_border(paragraph, color=RULE_COLOR, size=6):
    ppr = paragraph._element.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(size))
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), color)
    borders.append(bottom)
    ppr.append(borders)


def left_border(paragraph, color="9E9E9E", size=18):
    ppr = paragraph._element.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    left = OxmlElement("w:left")
    left.set(qn("w:val"), "single")
    left.set(qn("w:sz"), str(size))
    left.set(qn("w:space"), "8")
    left.set(qn("w:color"), color)
    borders.append(left)
    ppr.append(borders)


INLINE_RE = re.compile(r"(\*\*.+?\*\*|`[^`]+`)")


def add_inline(paragraph, text, base_size=BODY_SIZE, bold=False, color=None):
    """写入一段可能含 **粗体** / `代码` 的文本。

    粗体分支必须递归：像 **必须固定为 `1`** 这种「粗体里嵌代码」的写法，
    整段会被 INLINE_RE 当成一个粗体 token，不递归就会把反引号原样打出来。
    """
    for token in INLINE_RE.split(text):
        if not token:
            continue
        if token.startswith("**") and token.endswith("**") and len(token) > 4:
            add_inline(paragraph, token[2:-2], base_size, bold=True, color=color)
        elif token.startswith("`") and token.endswith("`") and len(token) > 2:
            run = paragraph.add_run(token[1:-1])
            set_font(run, CODE_FONT, base_size - 1.0, bold=bold, color=RGBColor(0xC7, 0x25, 0x4E))
            rpr = run._element.get_or_add_rPr()
            rfonts = rpr.find(qn("w:rFonts"))
            rfonts.set(qn("w:eastAsia"), BODY_FONT)  # 代码块里的中文走中文字体
        else:
            run = paragraph.add_run(token)
            set_font(run, BODY_FONT, base_size, bold=bold, color=color)


def add_heading(doc, text, level):
    sizes = {1: 20, 2: 15, 3: 12.5}
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(18 if level == 1 else (14 if level == 2 else 10))
    pf.space_after = Pt(4 if level == 1 else 6)
    pf.keep_with_next = True
    run = p.add_run(text)
    set_font(run, BODY_FONT, sizes[level], bold=True,
             color=RGBColor(0x1A, 0x1A, 0x1A) if level < 3 else RGBColor(0x33, 0x33, 0x33))
    if level == 1:
        bottom_border(p, "7F7F7F", 8)
    return p


def add_rule(doc):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(2)
    run = p.add_run("")
    set_font(run, BODY_FONT, 2)
    bottom_border(p, RULE_COLOR, 4)


def add_code_block(doc, lines):
    for i, line in enumerate(lines):
        p = doc.add_paragraph()
        pf = p.paragraph_format
        pf.space_before = Pt(6 if i == 0 else 0)
        pf.space_after = Pt(6 if i == len(lines) - 1 else 0)
        pf.left_indent = Cm(0.5)
        pf.line_spacing = 1.0
        run = p.add_run(line if line.strip() else " ")
        set_font(run, CODE_FONT, CODE_SIZE)
        rpr = run._element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        rfonts.set(qn("w:eastAsia"), BODY_FONT)
        shade(p, CODE_FILL)


def split_row(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def add_table(doc, rows):
    header, body = rows[0], rows[1:]
    table = doc.add_table(rows=1, cols=len(header))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    table.autofit = True

    for idx, text in enumerate(header):
        cell = table.rows[0].cells[idx]
        cell.text = ""
        p = cell.paragraphs[0]
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after = Pt(2)
        add_inline(p, text, base_size=BODY_SIZE - 0.5, bold=True)
        shade(cell, HEAD_FILL)

    for row in body:
        cells = table.add_row().cells
        for idx, text in enumerate(row[: len(header)]):
            cells[idx].text = ""
            p = cells[idx].paragraphs[0]
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.space_after = Pt(2)
            add_inline(p, text, base_size=BODY_SIZE - 0.5)

    doc.add_paragraph().paragraph_format.space_after = Pt(4)
    return table


# ---------- Markdown 解析 ----------

def build(src=None, out=None, cover=None):
    src = Path(src) if src else SRC
    out = Path(out) if out else DST
    # 与 DEFAULT_COVER 合并：只覆盖显式传了的字段。
    # 注意不能用 `cover or DEFAULT_COVER` —— cover 是 dict 恒为真，会导致
    # 未传 --meta 时（meta=None）整个封面信息块被吞掉。
    resolved = dict(DEFAULT_COVER)
    for key, value in (cover or {}).items():
        if value is not None:
            resolved[key] = value
    cover = resolved

    text = src.read_text(encoding="utf-8")
    lines = text.splitlines()

    doc = Document()
    section = doc.sections[0]
    section.top_margin = Cm(2.0)
    section.bottom_margin = Cm(2.0)
    section.left_margin = Cm(2.2)
    section.right_margin = Cm(2.2)

    normal = doc.styles["Normal"]
    normal.font.name = BODY_FONT
    normal.font.size = Pt(BODY_SIZE)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)

    # 封面块
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(2)
    run = p.add_run(cover["title"])
    set_font(run, BODY_FONT, 11, bold=True, color=GRAY)
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(10)
    run = p.add_run(cover["subtitle"])
    set_font(run, BODY_FONT, 24, bold=True)

    for label, value in cover.get("meta") or []:
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        run = p.add_run(f"{label}：")
        set_font(run, BODY_FONT, 10, bold=True, color=GRAY)
        run = p.add_run(value)
        set_font(run, BODY_FONT, 10, color=GRAY)
    add_rule(doc)

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        # 围栏代码块
        if stripped.startswith("```"):
            i += 1
            buf = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i].rstrip())
                i += 1
            i += 1
            add_code_block(doc, buf)
            continue

        # 表格
        if stripped.startswith("|") and i + 1 < n and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            rows = [split_row(stripped)]
            i += 2
            while i < n and lines[i].strip().startswith("|"):
                rows.append(split_row(lines[i]))
                i += 1
            add_table(doc, rows)
            continue

        # 分隔线（跳过首个，避免和封面重复）
        if re.match(r"^-{3,}$", stripped):
            if i > 3:
                add_rule(doc)
            i += 1
            continue

        # 标题
        m = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        if m:
            add_heading(doc, m.group(2).strip(), len(m.group(1)))
            i += 1
            continue

        # 引用
        if stripped.startswith(">"):
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.5)
            p.paragraph_format.space_before = Pt(6)
            p.paragraph_format.space_after = Pt(8)
            add_inline(p, stripped.lstrip(">").strip(), color=GRAY)
            left_border(p)
            i += 1
            continue

        # 无序列表
        if re.match(r"^[-*]\s+", stripped):
            p = doc.add_paragraph(style="List Bullet")
            p.paragraph_format.space_after = Pt(3)
            add_inline(p, re.sub(r"^[-*]\s+", "", stripped))
            i += 1
            continue

        # 有序列表：编号写死，避免 Word 跨列表连续编号
        m = re.match(r"^(\d+)\.\s+(.*)$", stripped)
        if m:
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.75)
            p.paragraph_format.first_line_indent = Cm(-0.75)
            p.paragraph_format.space_after = Pt(3)
            add_inline(p, f"{m.group(1)}. {m.group(2)}")
            i += 1
            continue

        # 空行
        if not stripped:
            i += 1
            continue

        # 缩进续行（列表项的补充说明）
        indent = len(line) - len(line.lstrip())
        p = doc.add_paragraph()
        if indent >= 2:
            p.paragraph_format.left_indent = Cm(0.75)
        p.paragraph_format.space_after = Pt(6)
        add_inline(p, stripped)

        # 章首的「给部署的同学」那句加个引用样式
        i += 1

    doc.save(out)
    return out


def _parse_args(argv):
    """极简参数解析：位置参数 src / out，其余为 --title / --subtitle / --meta K=V（可重复）。"""
    src = out = None
    cover = {"title": DEFAULT_COVER["title"], "subtitle": DEFAULT_COVER["subtitle"], "meta": []}
    positional = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--title":
            i += 1
            cover["title"] = argv[i]
        elif arg == "--subtitle":
            i += 1
            cover["subtitle"] = argv[i]
        elif arg == "--meta":
            i += 1
            key, _, value = argv[i].partition("=")
            cover["meta"].append((key, value))
        else:
            positional.append(arg)
        i += 1
    if positional:
        src = positional[0]
    if len(positional) > 1:
        out = positional[1]
    if not cover["meta"]:
        cover["meta"] = None
    return src, out, cover


if __name__ == "__main__":
    _src, _out, _cover = _parse_args(sys.argv[1:])
    result = build(_src, _out, _cover)
    print(f"已生成: {result}")
    print(f"大小: {result.stat().st_size / 1024:.1f} KB")
    sys.exit(0)
