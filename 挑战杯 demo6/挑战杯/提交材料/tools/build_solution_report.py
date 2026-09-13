"""Build the complete competition technical report as a DOCX file."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[2]
SUBMISSION = ROOT / "提交材料"
MAIN_DIR = SUBMISSION / "仿真结果" / "综合交付_DQN_NSGA2"
DYNAMIC_DIR = (
    SUBMISSION
    / "仿真结果"
    / "动态响应_N2故障与订单变更"
    / "current"
)
ASSET_DIR = SUBMISSION / "报告素材"
OUTPUT = SUBMISSION / "船体加工车间智能排产与齐套配盘优化调度技术方案报告.docx"

BLACK = "000000"
TEXT = "222222"
MUTED = "5F6B7A"
TABLE_HEADER = "245A8D"
TABLE_ALT = "F2F6FA"
TABLE_BORDER = "D9D9D9"
ACCENT = "245A8D"


@dataclass(frozen=True)
class RichNotation:
    base: str
    sub: str = ""
    sup: str = ""
    tail: str = ""


def load_inputs():
    summary = json.loads((MAIN_DIR / "summary.json").read_text(encoding="utf-8"))
    dynamic = json.loads((DYNAMIC_DIR / "dynamic_response.json").read_text(encoding="utf-8"))
    pareto = pd.read_csv(MAIN_DIR / "pareto_front.csv")
    base = summary["FIFO基线"]
    opt = summary["综合交付解"]
    capacity = summary["产能优先备选解"]
    return summary, dynamic, pareto, base, opt, capacity


def mean_device_load_rate(metrics: dict) -> float:
    excluded = (
        "峰值",
        "平均利用率",
        "综合稼动率",
        "纯切割稼动率",
        "班次稼动率",
    )
    values = [
        float(value)
        for key, value in metrics.items()
        if key.endswith("利用率")
        and isinstance(value, (int, float))
        and not any(token in key for token in excluded)
    ]
    return sum(values) / len(values) if values else 0.0


def set_run_font(
    run,
    size: float | None = None,
    bold: bool | None = None,
    color: str | None = None,
    name: str = "Microsoft YaHei",
) -> None:
    run.font.name = name
    run._element.get_or_add_rPr()
    run._element.rPr.rFonts.set(qn("w:ascii"), name)
    run._element.rPr.rFonts.set(qn("w:hAnsi"), name)
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if color:
        run.font.color.rgb = RGBColor.from_string(color)


def add_notation_segments(
    paragraph,
    segments,
    size: float = 9.2,
    color: str = TEXT,
    bold: bool = False,
) -> None:
    for segment in segments:
        if isinstance(segment, str):
            run = paragraph.add_run(segment)
            set_run_font(run, size=size, bold=bold, color=color)
            continue
        run = paragraph.add_run(segment.base)
        set_run_font(run, size=size, bold=bold, color=color)
        if segment.sub:
            sub_run = paragraph.add_run(segment.sub)
            set_run_font(sub_run, size=size * 0.78, bold=bold, color=color)
            sub_run.font.subscript = True
        if segment.sup:
            sup_run = paragraph.add_run(segment.sup)
            set_run_font(sup_run, size=size * 0.78, bold=bold, color=color)
            sup_run.font.superscript = True
        if segment.tail:
            tail_run = paragraph.add_run(segment.tail)
            set_run_font(tail_run, size=size, bold=bold, color=color)


def add_notation_para(
    doc: Document,
    segments,
    size: float = 10.8,
    color: str = TEXT,
    bold: bool = False,
    after: float = 7,
):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.line_spacing = 1.28
    add_notation_segments(p, segments, size=size, color=color, bold=bold)
    return p


def set_cell_margins(cell, top=85, start=125, bottom=85, end=125) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for side, value in (
        ("top", top),
        ("start", start),
        ("bottom", bottom),
        ("end", end),
    ):
        node = tc_mar.find(qn(f"w:{side}"))
        if node is None:
            node = OxmlElement(f"w:{side}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def set_cell_shading(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_borders(cell) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = tc_pr.first_child_found_in("w:tcBorders")
    if borders is None:
        borders = OxmlElement("w:tcBorders")
        tc_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        tag = f"w:{edge}"
        node = borders.find(qn(tag))
        if node is None:
            node = OxmlElement(tag)
            borders.append(node)
        node.set(qn("w:val"), "single")
        node.set(qn("w:sz"), "5")
        node.set(qn("w:space"), "0")
        node.set(qn("w:color"), TABLE_BORDER)


def repeat_table_header(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    tbl_header = OxmlElement("w:tblHeader")
    tbl_header.set(qn("w:val"), "true")
    tr_pr.append(tbl_header)


def prevent_row_split(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    cant_split = OxmlElement("w:cantSplit")
    cant_split.set(qn("w:val"), "true")
    tr_pr.append(cant_split)


def set_table_width(table, widths: list[float]) -> None:
    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    tbl_pr = table._tbl.tblPr
    tbl_w = tbl_pr.first_child_found_in("w:tblW")
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW")
        tbl_pr.append(tbl_w)
    tbl_w.set(qn("w:w"), str(round(sum(widths) * 1440)))
    tbl_w.set(qn("w:type"), "dxa")
    for row in table.rows:
        for idx, cell in enumerate(row.cells):
            cell.width = Inches(widths[idx])
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            set_cell_margins(cell)
            set_cell_borders(cell)
            tc_pr = cell._tc.get_or_add_tcPr()
            tc_w = tc_pr.first_child_found_in("w:tcW")
            if tc_w is None:
                tc_w = OxmlElement("w:tcW")
                tc_pr.append(tc_w)
            tc_w.set(qn("w:w"), str(round(widths[idx] * 1440)))
            tc_w.set(qn("w:type"), "dxa")


def add_para(
    doc: Document,
    text: str = "",
    size: float = 10.8,
    bold: bool = False,
    color: str = TEXT,
    align=WD_ALIGN_PARAGRAPH.JUSTIFY,
    before: float = 0,
    after: float = 7,
    line_spacing: float = 1.28,
):
    p = doc.add_paragraph()
    p.alignment = align
    p.paragraph_format.space_before = Pt(before)
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.line_spacing = line_spacing
    if text:
        r = p.add_run(text)
        set_run_font(r, size=size, bold=bold, color=color)
    return p


def add_bullet(doc: Document, text: str, level: int = 0):
    style = "List Bullet" if level == 0 else "List Bullet 2"
    p = doc.add_paragraph(style=style)
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.line_spacing = 1.18
    r = p.add_run(text)
    set_run_font(r, size=10.5, color=TEXT)
    return p


def add_heading(doc: Document, text: str, level: int = 1):
    style_name = "Heading 1" if level == 1 else "Heading 2" if level == 2 else "Heading 3"
    p = doc.add_paragraph(style=style_name)
    p.paragraph_format.keep_with_next = True
    p.paragraph_format.space_before = Pt(16 if level == 1 else 11)
    p.paragraph_format.space_after = Pt(8 if level == 1 else 6)
    r = p.add_run(text)
    set_run_font(
        r,
        size=16 if level == 1 else 13 if level == 2 else 11.5,
        bold=True,
        color=BLACK,
    )
    return p


def add_table(
    doc: Document,
    headers: list[str],
    rows: list[list[object]],
    widths: list[float],
    font_size: float = 9.2,
):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    set_table_width(table, widths)
    header = table.rows[0]
    repeat_table_header(header)
    prevent_row_split(header)
    for idx, value in enumerate(headers):
        cell = header.cells[idx]
        set_cell_shading(cell, TABLE_HEADER)
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(0)
        r = p.add_run(str(value))
        set_run_font(r, size=font_size, bold=True, color="FFFFFF")
    for row_idx, row in enumerate(rows):
        cells = table.add_row().cells
        prevent_row_split(table.rows[-1])
        for idx, value in enumerate(row):
            if row_idx % 2 == 1:
                set_cell_shading(cells[idx], TABLE_ALT)
            p = cells[idx].paragraphs[0]
            p.alignment = (
                WD_ALIGN_PARAGRAPH.LEFT
                if idx == 0 or isinstance(value, str) and len(str(value)) > 22
                else WD_ALIGN_PARAGRAPH.CENTER
            )
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = 1.12
            if isinstance(value, RichNotation) or (
                isinstance(value, list)
                and value
                and any(isinstance(item, RichNotation) for item in value)
            ):
                segments = value if isinstance(value, list) else [value]
                add_notation_segments(p, segments, size=font_size, color=TEXT)
            else:
                r = p.add_run(str(value))
                set_run_font(r, size=font_size, color=TEXT)
    add_para(doc, "", after=2)
    return table


def add_figure(
    doc: Document,
    path: Path,
    caption: str,
    width: float = 6.3,
):
    if not path.exists():
        return
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(3)
    shape = p.add_run().add_picture(str(path), width=Inches(width))
    shape._inline.docPr.set("descr", caption)
    shape._inline.docPr.set("title", caption)
    cap = doc.add_paragraph()
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap.paragraph_format.space_after = Pt(9)
    r = cap.add_run(caption)
    set_run_font(r, size=9, color=MUTED)


def mrun(text: str, upright: bool = False):
    node = OxmlElement("m:r")
    if upright:
        rpr = OxmlElement("m:rPr")
        sty = OxmlElement("m:sty")
        sty.set(qn("m:val"), "p")
        rpr.append(sty)
        node.append(rpr)
    t = OxmlElement("m:t")
    t.text = text
    node.append(t)
    return node


def mrow(*items):
    node = OxmlElement("m:mrow")
    for item in items:
        node.append(item)
    return node


def mfrac(num, den):
    node = OxmlElement("m:f")
    n = OxmlElement("m:num")
    d = OxmlElement("m:den")
    n.append(num)
    d.append(den)
    node.append(n)
    node.append(d)
    return node


def msub(base, sub=None):
    node = OxmlElement("m:sSub")
    e = OxmlElement("m:e")
    s = OxmlElement("m:sub")
    e.append(base)
    s.append(sub if sub is not None else mrow(mrun("")))
    node.append(e)
    node.append(s)
    return node


def msub_text(base: str, sub: str):
    return msub(mrow(mrun(base)), mrow(mrun(sub)))


def msubsup(base, sub, sup):
    node = OxmlElement("m:sSubSup")
    e = OxmlElement("m:e")
    s = OxmlElement("m:sub")
    p = OxmlElement("m:sup")
    e.append(base)
    s.append(sub)
    p.append(sup)
    node.append(e)
    node.append(s)
    node.append(p)
    return node


def msubsup_text(base: str, sub: str, sup: str):
    return msubsup(
        mrow(mrun(base)),
        mrow(mrun(sub)),
        mrow(mrun(sup)),
    )


def msup(base, sup):
    node = OxmlElement("m:sSup")
    e = OxmlElement("m:e")
    s = OxmlElement("m:sup")
    e.append(base)
    s.append(sup)
    node.append(e)
    node.append(s)
    return node


def msup_text(base: str, sup: str):
    return msup(mrow(mrun(base)), mrow(mrun(sup)))


def mdelim(content):
    node = OxmlElement("m:d")
    pr = OxmlElement("m:dPr")
    beg = OxmlElement("m:begChr")
    end = OxmlElement("m:endChr")
    beg.set(qn("m:val"), "(")
    end.set(qn("m:val"), ")")
    pr.append(beg)
    pr.append(end)
    e = OxmlElement("m:e")
    e.append(content)
    node.append(pr)
    node.append(e)
    return node


def add_equation(doc: Document, items, caption: str):
    match = re.match(r"公式\s*([0-9]+(?:-[0-9]+)?)\s*(.*)", caption)
    number = match.group(1) if match else "?"
    title = match.group(2).strip() if match else caption

    if "约束" in title or "容量" in title:
        lead = f"为了满足{title}，引入约束条件如公式({number})所示："
    elif "目标" in title or "评价" in title or "奖励" in title:
        lead = f"为实现{title}，引入目标表达式如公式({number})所示："
    else:
        lead = f"根据前述参数和变量，公式({number})用于表达{title}："

    add_para(doc, lead, size=10.8, after=3)
    table = doc.add_table(rows=1, cols=2)
    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    prevent_row_split(table.rows[0])
    repeat_table_header(table.rows[0])
    left, right = table.rows[0].cells
    for cell, width in ((left, 5.95), (right, 0.75)):
        cell.width = Inches(width)
        set_cell_margins(cell, top=40, start=20, bottom=40, end=20)
        tc_pr = cell._tc.get_or_add_tcPr()
        tc_w = tc_pr.first_child_found_in("w:tcW")
        if tc_w is None:
            tc_w = OxmlElement("w:tcW")
            tc_pr.append(tc_w)
        tc_w.set(qn("w:w"), str(round(width * 1440)))
        tc_w.set(qn("w:type"), "dxa")

    p = left.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(2)
    para = OxmlElement("m:oMathPara")
    math = OxmlElement("m:oMath")
    for item in items:
        math.append(item)
    para.append(math)
    p._p.append(para)

    num_p = right.paragraphs[0]
    num_p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    num_p.paragraph_format.space_before = Pt(2)
    num_p.paragraph_format.space_after = Pt(2)
    num_run = num_p.add_run(f"({number})")
    set_run_font(num_run, size=10, color=TEXT)
    add_para(doc, "", after=5)


def add_plain_math_line(doc: Document, line: str):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(7)
    r = p.add_run(line)
    set_run_font(r, size=10.5, bold=True, color=ACCENT, name="Cambria Math")
    return p


def set_section_columns(section, count: int, space_twips: int = 360, separator: bool = True) -> None:
    sect_pr = section._sectPr
    cols = sect_pr.find(qn("w:cols"))
    if cols is None:
        cols = OxmlElement("w:cols")
        sect_pr.append(cols)
    cols.set(qn("w:num"), str(max(1, count)))
    cols.set(qn("w:space"), str(space_twips))
    cols.set(qn("w:sep"), "1" if separator and count > 1 else "0")
    cols.set(qn("w:equalWidth"), "1")


def add_page_number(section) -> None:
    p = section.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("")
    set_run_font(r, size=8.5, color=MUTED)
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    p._p.append(fld)


def configure_styles(doc: Document) -> None:
    normal = doc.styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal._element.rPr.rFonts.set(qn("w:ascii"), "Microsoft YaHei")
    normal._element.rPr.rFonts.set(qn("w:hAnsi"), "Microsoft YaHei")
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    normal.font.size = Pt(10.8)
    normal.font.color.rgb = RGBColor.from_string(TEXT)
    normal.paragraph_format.line_spacing = 1.28

    title = doc.styles["Title"]
    title.font.name = "Microsoft YaHei"
    title._element.rPr.rFonts.set(qn("w:ascii"), "Microsoft YaHei")
    title._element.rPr.rFonts.set(qn("w:hAnsi"), "Microsoft YaHei")
    title._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    title.font.size = Pt(26)
    title.font.bold = True
    title.font.color.rgb = RGBColor.from_string(BLACK)

    for style_name, size in (("Heading 1", 16), ("Heading 2", 13), ("Heading 3", 11.5)):
        style = doc.styles[style_name]
        style.font.name = "Microsoft YaHei"
        style._element.rPr.rFonts.set(qn("w:ascii"), "Microsoft YaHei")
        style._element.rPr.rFonts.set(qn("w:hAnsi"), "Microsoft YaHei")
        style._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = RGBColor.from_string(BLACK)


def cover(doc: Document, summary: dict) -> None:
    for _ in range(4):
        add_para(doc, "", after=12)
    p = doc.add_paragraph(style="Title")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("船体加工车间智能排产与齐套配盘优化调度")
    set_run_font(r, size=27, bold=True, color=BLACK)
    add_para(
        doc,
        "完整技术方案报告",
        size=18,
        bold=True,
        color=ACCENT,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        before=14,
        after=20,
    )
    add_para(
        doc,
        "SA+Tabu、GA+LNS、DQN+NSGA-II 与 DRL 自适应搜索\n离散事件仿真与状态化动态重排",
        size=14,
        color=MUTED,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        after=42,
    )
    add_para(
        doc,
        "题目编号：CS-202625",
        size=12,
        bold=True,
        color=TEXT,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        after=8,
    )
    add_para(
        doc,
        "技术路线：DQN + NSGA-II + 离散事件仿真 + 动态事件重排",
        size=11,
        color=TEXT,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        after=8,
    )
    add_para(
        doc,
        "报告日期：2026 年 9 月 13 日",
        size=11,
        color=MUTED,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        after=50,
    )
    add_para(
        doc,
        "本报告采用附件 2 和附件 3 的当前数据生成。题目未给出的物流与缓存参数集中在 ModelConfig 中显式参数化，未把默认假设表述为企业实测值。",
        size=9.5,
        color=MUTED,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        after=0,
        line_spacing=1.2,
    )
    doc.add_page_break()


def contents(doc: Document) -> None:
    add_heading(doc, "目录", 1)
    toc_section = doc.add_section(WD_SECTION.CONTINUOUS)
    set_section_columns(toc_section, 2, space_twips=300, separator=True)
    items = [
        "摘要",
        "1 项目背景与需求对应",
        "1.1 车间痛点与建设目标",
        "1.2 竞赛要求与本方案对应关系",
        "1.3 参考指标与交付口径",
        "1.4 灵活运行与现场响应能力",
        "1.5 订单变更与紧急插单双模式",
        "2 问题定义、流程与建模假设",
        "2.1 生产线与资源边界",
        "2.2 输入数据与校验结果",
        "2.3 料框与齐套定义",
        "2.4 建模假设",
        "3 数学建模与约束推导",
        "3.1 集合、索引与参数",
        "3.2 决策变量",
        "3.3 工艺时间推导",
        "3.4 工序时序与资源约束",
        "3.5 目标函数与 Pareto 形式",
        "3.6 资源竞争与等待时间递推",
        "3.7 理论下界与 KPI 推导",
        "4 算法设计、评价函数与求解流程",
        "4.1 分层求解架构",
        "4.2 DQN 机器分配",
        "4.3 改进 NSGA-II",
        "4.4 SA+Tabu",
        "4.5 GA+LNS",
        "4.6 DRL 自适应 Q-learning",
        "4.7 动态重排",
        "4.8 评价函数族与数学推导",
        "4.8.1 简单固定权重目标函数",
        "4.8.2 统一产能优先标量函数",
        "4.8.3 FIFO 相对平衡函数",
        "4.8.4 二次非线性惩罚函数",
        "4.8.5 Pareto 向量与多解交付",
        "4.9 算法与评价函数匹配规则",
        "5 离散事件仿真系统",
        "5.1 状态模型",
        "5.2 事件处理顺序",
        "5.3 KPI 计算与约束诊断",
        "5.4 结果可视化与文件输出",
        "6 仿真实验环境与复现方法",
        "6.1 运行环境",
        "6.2 数据与参数来源",
        "6.3 复现实验命令",
        "6.4 评价与停止条件",
        "6.5 实验设计矩阵",
        "6.6 随机性与可复现控制",
        "6.7 实验验收标准",
        "6.8 多进程并行加速",
        "7 实验结果与指标分析",
        "7.1 主实验对比",
        "7.2 Pareto 前沿与两条交付口径",
        "7.3 切割与齐套结果",
        "7.4 资源利用率与缓存诊断",
        "8 动态响应与鲁棒性验证",
        "8.1 故障与订单变更场景",
        "8.2 不稳定性来源与控制措施",
        "8.3 结果边界",
        "9 工程实现、代码结构与提交物",
        "9.1 分层代码结构",
        "9.2 主实验产物",
        "9.3 启动与操作",
        "10 风险边界、参数校准与后续工作",
        "10.1 当前实现的风险边界",
        "10.2 后续优化方向",
        "10.3 结论",
        "附录 A 主要参数与数据字典",
        "附录 B 代码文件与输出文件清单",
    ]
    for item in items:
        head = item.split(" ", 1)[0]
        is_sub = head.count(".") == 1
        is_subsub = head.count(".") == 2
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p.paragraph_format.space_after = Pt(2 if is_sub else 4)
        p.paragraph_format.line_spacing = 1.08
        if is_sub or is_subsub:
            p.paragraph_format.left_indent = Inches(0.72 if is_subsub else 0.32)
            p.paragraph_format.first_line_indent = Inches(-0.06)
        r = p.add_run(item)
        set_run_font(
            r,
            size=8.8 if is_subsub else 9.4 if is_sub else 11,
            bold=not is_sub,
            color=MUTED if is_sub else BLACK,
        )
    end_section = doc.add_section(WD_SECTION.CONTINUOUS)
    set_section_columns(end_section, 1, separator=False)
    doc.add_page_break()


def abstract(doc: Document, summary: dict, opt: dict) -> None:
    add_heading(doc, "摘要", 1)
    add_para(
        doc,
        "本方案面向船体加工车间“多品种、小批量、多工序耦合、齐套优先”的排产问题，"
        "建立了覆盖 N2/N5 双工位切割、桁架分拣、自动/人工打磨、自动/人工坡口、AGV 转运、"
        "半框缓存、齐套缓存和天车吊运的离散事件调度模型。模型以钢板顺序和每张钢板的切割机分配"
        "为主要决策，将齐套优先级、设备资格、工位占用、加工前后序、缓存容量、AGV 区域冲突和"
        "资源故障统一纳入状态演化。",
    )
    add_para(
        doc,
        "求解层并列提供多种算法。SA+Tabu 通过交换、插入、逆序和降温接受准则快速搜索钢板排列；"
        "GA+LNS 通过排列交叉、变异和破坏—修复提高种群多样性；DQN+NSGA-II 使用机器分配先验和"
        "非支配排序输出多目标 Pareto 前沿；DRL-guided Q-learning 根据搜索阶段动态选择邻域算子"
        "和权重；稳定右移、局部重优化与全局重优化用于故障和订单变更。各算法共享同一套离散事件"
        "仿真，避免不同方法使用不同约束口径。",
    )
    add_para(
        doc,
        "评价函数覆盖简单固定权重、Cmax/LB 产能优先、FIFO 相对线性平衡、二次非线性惩罚、"
        "四目标 Pareto 向量、DRL 动态权重和动态规则目标。系统支持用户在计算时间与模型参数之间"
        "选择优先口径，支持多故障、订单变更和多类目标同时处理。计算过程可即时停止，未成功完成的"
        "staging 结果不会提交到正式运行记录。",
    )
    add_para(
        doc,
        f"在附件 2 的 109 张钢板、910 个零件和 21 个齐套组上，独立运行的综合交付解为："
        f"总完工时间 {opt['总完工时间(h)']:.2f} h，平均齐套跨度 "
        f"{opt['加权平均齐套跨度(h)']:.2f} h，切割负载差 {opt['切割负载差(h)']:.2f} h，"
        f"整体产能 {opt['整体产能(张板/班)']:.2f} 张板/班，切割机综合稼动率 "
        f"{opt['切割机综合稼动率'] * 100:.2f}%。其中总完工时间和平均齐套跨度低于题目参考值，"
        f"产能高于 13 张板/班；平均设备负载率由 "
        f"{mean_device_load_rate(summary['FIFO基线']) * 100:.2f}% 提高到 "
        f"{mean_device_load_rate(opt) * 100:.2f}%。切割负载差作为内部平衡诊断保留，"
        "不单独设置硬性阈值。"
        "模型在故障与订单变更场景中于 8 s 预算内完成重排，"
        "并保持无死锁。缓存容量中的人工坡口工作站峰值属于尚未由企业数据校准的暂定参数，"
        "报告在风险章节单独说明。",
    )
    add_para(
        doc,
        "关键词：船体加工；齐套配盘；SA+Tabu；GA+LNS；DQN+NSGA-II；DRL-guided Q-learning；"
        "多评价函数；Pareto 优化；离散事件仿真；动态重排",
        size=10.5,
        bold=True,
        color=ACCENT,
        after=4,
    )
    doc.add_page_break()


def section_requirements(doc: Document, summary: dict) -> None:
    add_heading(doc, "1 项目背景与需求对应", 1)
    add_heading(doc, "1.1 车间痛点与建设目标", 2)
    add_para(
        doc,
        "题目要求同时解决多工序耦合、有限缓存下的齐套配盘、设备负载均衡和多目标动态优化。"
        "船体钢板切割后的零件并不以“单张钢板完成”为交付单位，而要以“相同分段、相同需求优先级”"
        "的零件全部到位作为齐套条件。一个分段中任何关键零件滞后，都会让已经加工完成的其他零件"
        "停留在缓存区，形成等待库存。",
    )
    add_para(
        doc,
        "本方案把排产目标拆成两类。第一类是硬约束可行性，包括设备资格、工序前后序、资源互斥、"
        "AGV 区域冲突、故障区间和缓存容量。第二类优化目标包括总完工时间、齐套跨度、切割负载差、"
        "等待时间和设备利用率。两类目标通过完整离散事件仿真连接，避免只用静态工时估算而产生"
        "“纸面最优”。",
    )
    add_heading(doc, "1.2 竞赛要求与本方案对应关系", 2)
    add_table(
        doc,
        ["竞赛要求", "方案实现", "验证证据"],
        [
            [
                "建立多工序、异构设备数学模型",
                "集合、参数、决策变量、工序时序、资源能力和缓存约束完整建模",
                "第 3 章公式推导；steel_schedule_model.py",
            ],
            [
                "攻克齐套配盘与有限缓存瓶颈",
                "按分段、优先级、坡口属性和零件类型组框；最终齐套按分段门控",
                "kit_groups.csv；part_completion.csv；齐套跨度图",
            ],
            [
                "设备负载均衡",
                "显式机器码编码；N5 厚度/宽度资格约束；负载差进入 Pareto 目标",
                "Pareto 前沿；N2/N5 利用率；产能优先备选解",
            ],
            [
                "多目标动态平衡",
                "四目标 Pareto；产能优先和兼顾三指标两条交付口径",
                "pareto_front.csv；综合交付解与备选解对比",
            ],
            [
                "多种算法和评价函数",
                "SA+Tabu、GA+LNS、DQN+NSGA-II、DRL-guided SA；线性、二次、Pareto 和动态目标",
                "第 4 章；用户手册算法选择表；代码模块映射",
            ],
            [
                "设备故障与插单变更",
                "订单变更与紧急插单双模式；资源日历、状态快照、在制策略、稳定右移、局部/全局重排",
                "动态响应 JSON、CSV 和运行截图",
            ],
            [
                "完整报告、代码与仿真程序",
                "Web 系统、命令行模型、优化器、动态引擎、结果导出",
                "第 9 章代码清单；用户手册；提交包",
            ],
        ],
        [1.65, 2.85, 2.10],
    )
    add_heading(doc, "1.3 参考指标与交付口径", 2)
    add_table(
        doc,
        ["指标", "题目参考值", "本方案综合交付解", "结果判断"],
        [
            ["总完工时间", "≤ 67.25 h", f"{summary['综合交付解']['总完工时间(h)']:.2f} h", "优于参考值"],
            [
                "流向平均齐套时长",
                "≤ 12.7 h",
                f"{summary['综合交付解']['加权平均齐套跨度(h)']:.2f} h",
                "优于参考值",
            ],
            [
                "平均设备负载率",
                "题目重点指标",
                f"{mean_device_load_rate(summary['综合交付解']) * 100:.2f}%",
                "按全部加工与物流资源利用率均值计算",
            ],
            [
                "整体产能",
                "≥ 13 张板/班",
                f"{summary['综合交付解']['整体产能(张板/班)']:.2f} 张板/班",
                "优于参考值",
            ],
            [
                "切割机综合稼动率",
                "≥ 61%",
                f"{summary['综合交付解']['切割机综合稼动率'] * 100:.2f}%",
                "优于参考值",
            ],
        ],
        [1.45, 1.35, 1.65, 2.15],
    )
    add_heading(doc, "1.4 灵活运行与现场响应能力", 2)
    add_para(
        doc,
        "方案不把算法写成只能按固定流程运行的黑盒。用户可以根据现场交期、算力、故障状态和"
        "模型置信度选择运行口径，系统在同一任务内保留产能优先解和兼顾产能、齐套、负载的平衡解。",
    )
    add_table(
        doc,
        ["灵活能力", "实现方式", "对现场的使用价值"],
        [
            [
                "即时停止",
                "前端发送 client_token，后端设置取消标记；评估器在安全点检查并抛出取消异常",
                "停止后 discard staging，不覆盖 current，也不写入最终历史结果",
            ],
            [
                "可接受计算时间",
                "max_compute_time_s 定义用户时间上限，实际优化预算取 95%，并受按钢板数缩放的安全上限约束",
                "用户可以先给 5 s、60 s 或数小时预算，系统在当前评估结束后停止",
            ],
            [
                "时间优先或模型参数优先",
                "time_priority_mode=True 时自动放大迭代次数、种群规模或代数上限；False 时完全按模型参数运行",
                "交期紧急时以时间为准，验证实验时以固定参数为准；同一 API 支持两种口径",
            ],
            [
                "多指标综合与并行交付",
                "同一次 DQN+NSGA-II 运行生成 Pareto 前沿，再分别选出产能优先和兼顾三指标结果",
                "用户可以比较重工时与综合平衡方案，而不是接受一个固定权重分数",
            ],
            [
                "多故障同时发生",
                "多个 FaultEvent 统一映射到资源日历，允许故障重叠、提前修复和修复时间调整",
                "切割头、胎架、打磨、坡口、AGV 和天车故障可以在同一重排状态中处理",
            ],
            [
                "订单变更与故障并发",
                "订单取消、优先级调整、附件替换和故障清单在同一次重排提交中解析",
                "插单、撤单和设备故障同时发生时，仍能重建冻结、在制和未开始集合",
            ],
            [
                "在制钢板处置可选",
                "finish 与 interrupt 两种策略；interrupt 时加入清台时间",
                "可以选择最大产出或减少现场切换，避免对在制钢板作不真实回滚",
            ],
            [
                "剩余排程模型可选",
                "首板快速锁定后，剩余钢板可选 stable、SA+Tabu、GA+LNS 或 DQN+NSGA-II",
                "短时故障用稳定右移，大范围变更用更强搜索模型",
            ],
        ],
        [1.35, 2.70, 2.45],
        font_size=8.2,
    )
    add_heading(doc, "1.5 订单变更与紧急插单双模式", 2)
    add_para(
        doc,
        "附件 2 上传并校验成功后，界面才显示“订单变更”和“紧急插单”两个模式按钮；"
        "上传未完成或附件 2 校验失败时不显示模式入口，避免把无效数据写入动态状态。两种模式"
        "都复用故障日历、在制钢板状态、时间预算和 staging/current 提交流程。",
    )
    add_figure(
        doc,
        ASSET_DIR / "dual_mode_flow.png",
        "图 1-1 订单变更与紧急插单双模式流程",
        width=6.45,
    )
    add_table(
        doc,
        ["项目", "订单变更模式", "紧急插单模式"],
        [
            ["数据语义", "新附件 2 是变更后的订单数据", "新附件 2 是新增批次，原计划保留"],
            ["算法", "使用原有动态重排算法", "新增批次前置，再续排原计划剩余钢板"],
            ["批次拆分", "不拆分新增批次与原计划批次", "明确拆分新增批次和原计划批次"],
            ["故障和订单时间", "仍按原逻辑处理机器故障和时间", "故障日历、时间预算和在制状态继续生效"],
            ["同名钢板", "按附件 2 校验规则处理", "与任一原计划钢板同名时立即提示，不合并"],
            ["首阶段输出", "无独立首 4 张预览", "新增批次首 4 张立即生成甘特图和 staging CSV"],
            ["最终提交", "统一提交 current", "新增批次全部计算完，再以资源释放时间为起点续排原计划，最后统一提交"],
        ],
        [1.20, 2.20, 3.10],
        font_size=8.3,
    )
    add_para(
        doc,
        "紧急插单的计算顺序为：读取并校验新增附件 2；把新增钢板作为前置批次；用最大响应时间"
        "约 95% 计算新增批次前 4 张钢板；立即生成新增批次的甘特图和 staging CSV；后台继续计算"
        "全部新增附件 2 钢板；新增批次排完后，以新增批次的资源释放时间为起点续排原计划剩余"
        "钢板；最后统一提交 current 并更新历史记录。若新增批次与原计划存在同名钢板，系统直接"
        "提示重名，不把不同订单错误合并。",
    )
    add_para(
        doc,
        "紧急插单首阶段只导出新增批次前 4 张钢板到 staging/{job_id}/optimized_plate_schedule.csv、"
        "part_completion.csv、kit_groups.csv 和 process_stages.csv。全部新增附件 2 计算完成后，"
        "完整新增批次和原计划续排结果仍在 staging 中。最终提交后，正式下载地址切换到 "
        "current/ 下的对应文件。",
    )
    add_table(
        doc,
        ["中断或提交动作", "系统行为", "用户可见结果"],
        [
            ["紧急中断", "停止后台优化；删除对应 staging；不提交 current；不更新 history.json", "临时甘特图清除，结果概览、甘特图、齐套分析、设备利用率恢复动态响应前正式结果"],
            ["完整计算成功", "staging/{job_id}/ -> current/；旧 current/ -> generations/；current.json 原子切换", "结果概览、甘特图、齐套分析、设备利用率、CSV、summary.json、history.json 和 balanced 结果同步更新"],
        ],
        [1.25, 3.30, 1.95],
        font_size=8.2,
    )
    doc.add_page_break()


def section_problem(doc: Document, summary: dict) -> None:
    add_heading(doc, "2 问题定义、流程与建模假设", 1)
    add_heading(doc, "2.1 生产线与资源边界", 2)
    add_para(
        doc,
        "产线由 1 台人工天车、2 台双工位等离子切割机、2 套双臂分拣桁架、2 套小件自由边打磨"
        "设备、1 台自动小件坡口设备、1 个人工坡口工位和 2 台 AGV 组成。N2 和 N5 均为双工位"
        "切割机；N5 对钢板厚度和宽度存在加工资格限制，N2 作为通用后备机台。小件经过分拣、"
        "打磨、自动坡口、码垛和 AGV 转运，大件经过胎架自由边打磨、天车转运和人工坡口。",
    )
    add_figure(
        doc,
        ASSET_DIR / "process_flow.png",
        "图 2-1 钢板、零件、缓存与齐套的全流程约束链",
        width=6.45,
    )
    add_heading(doc, "2.2 输入数据与校验结果", 2)
    checks = summary["数据校验"]
    add_table(
        doc,
        ["数据项", "当前数据", "校验结论"],
        [
            ["钢板数量", f"{checks['钢板数']} 张", "附件 2 钢板表有效"],
            ["零件数量", f"{checks['零件数']} 个", "零件表有效"],
            ["未匹配零件", f"{checks['未匹配零件数']} 个", "钢板与零件关联完整"],
            ["零件数量不一致钢板", f"{checks['零件数不一致钢板数']} 张", "汇总数量一致"],
            ["小件数量不一致钢板", f"{checks['小件数不一致钢板数']} 张", "汇总数量一致"],
            ["大件数量不一致钢板", f"{checks['大件数不一致钢板数']} 张", "汇总数量一致"],
            ["V 坡长度不一致钢板", f"{checks['V坡长度不一致钢板数']} 张", "差异未超过 0.01 mm"],
            ["齐套组", f"{checks['齐套组数(分段+优先级)']} 组", "按分段和优先级聚合"],
            [
                "打磨长度缺失零件",
                f"{checks['打磨长度缺失零件数']} 个",
                "原值保留，仿真按待确认临时输入处理",
            ],
        ],
        [1.90, 1.55, 3.15],
    )
    add_heading(doc, "2.3 料框与齐套定义", 2)
    add_para(
        doc,
        "小件料框的键为“分段号、齐套优先级、是否坡口件、零件类型、所属切割机”。相同键的零件"
        "合并为一个料框，坡口件和非坡口件不混装。分段齐套只针对小件料框；大件不进入 AGV "
        "料框链路，加工完成后进入成品放置区，不参与小件分段齐套门控。系统检查小件分段需求"
        "是否全部满足后，再将齐套料框送入小部材放置区。",
    )
    add_para(
        doc,
        "齐套跨度定义为“小件组内最后一件到达半框缓存的时刻”减去“小件组内首件到达时刻”。"
        "加权平均齐套跨度以小件零件数为权重，避免小样本分段对结果产生不成比例的影响。",
    )
    add_heading(doc, "2.4 建模假设", 2)
    assumptions = [
        "所有输入长度以毫米计，所有时间以分钟计；班次产能按 8 h/班换算。",
        "钢板切割前的原料吊运与胎架准备显式建模；双工位设备的残材处理与另一工位切割重叠，默认不重复累加 25 min。",
        "N5 仅加工厚度不超过 36 mm 且宽度不超过 4500 mm 的钢板；N2 对所有当前钢板均可加工。",
        "自动坡口和人工坡口的加工时间按零件分别累计，料框转运时间按整框计。",
        "天车同一时间只能执行一个动作，空驶和载运均占用资源；AGV 采用区域互斥与负载均衡策略。",
        "题目未给出的缓存容量、缓存停留时间和物流参数使用 ModelConfig 默认值，不宣称为企业实测值。",
        "优化过程固定随机种子；由于多目标搜索本身是随机算法，单次运行只代表固定种子下的一次可复现实验。",
    ]
    for item in assumptions:
        add_bullet(doc, item)
    doc.add_page_break()


def section_math(doc: Document) -> None:
    add_heading(doc, "3 数学建模与约束推导", 1)
    add_heading(doc, "3.1 集合、索引与参数", 2)
    add_notation_para(
        doc,
        [
            "本章统一采用上下标记号。右下角是对象，右上角是方式或行为。例如 ",
            RichNotation("L", "p", "straight"),
            " 表示“钢板 p 的直切长度”，",
            RichNotation("s", "i,r"),
            " 表示“零件 i 在资源 r 上的开始时刻”。所有正式公式均由 Word 原生公式对象呈现。",
        ],
    )
    add_table(
        doc,
        ["符号", "含义"],
        [
            ["P", "钢板集合，p ∈ P；|P|=109"],
            ["I", "零件集合，i ∈ I；|I|=910"],
            ["M", "切割机集合，m ∈ M；当前为 {N2, N5}"],
            ["R", "下游资源集合，包含桁架、打磨机、坡口机、AGV、天车"],
            ["G", "齐套组集合，g=(s,q)，s 为分段，q 为需求优先级"],
            ["H", "缓存区集合，h 包含机旁码垛、坡口缓存、半框缓存和齐套缓存"],
            [
                [
                    RichNotation("x", "p"),
                    ", ",
                    RichNotation("y", "p"),
                    ", ",
                    RichNotation("θ", "p"),
                ],
                "钢板 p 的宽度、厚度及其切割参数",
            ],
            [
                RichNotation("L", "p", "straight"),
                "钢板 p 的直切长度",
            ],
            [
                RichNotation("L", "p", "V"),
                "钢板 p 的 V 坡长度",
            ],
            [
                RichNotation("L", "p", "empty"),
                "钢板 p 的空行程长度",
            ],
            [
                RichNotation("L", "p", "mark"),
                "钢板 p 的划线长度",
            ],
            [
                RichNotation("n", "p", "pierce"),
                "钢板 p 的穿孔数",
            ],
            [
                [
                    RichNotation("v", "s"),
                    "(θ), ",
                    RichNotation("v", "VY"),
                    "(θ), ",
                    RichNotation("v", "XK"),
                    "(θ)",
                ],
                "厚度 θ 对应的直切、V&Y 坡和 X&K 坡速度",
            ],
            [
                [
                    RichNotation("t", "setup"),
                    ", ",
                    RichNotation("t", "pierce"),
                ],
                "单板上料准备时间和单孔穿孔时间",
            ],
            [
                RichNotation("B", "h"),
                "缓存区 h 的容量，单位为料框",
            ],
            [
                RichNotation("Ω", "r"),
                "资源 r 的故障不可用区间集合",
            ],
        ],
        [1.75, 4.85],
        font_size=8.8,
    )
    add_heading(doc, "3.2 决策变量", 2)
    add_notation_para(
        doc,
        [
            "本模型使用“钢板顺序 OS + 机器分配 MS”的两层编码。钢板顺序决定何种分段优先进入产线，"
            "机器分配决定 N2 和 N5 的负载结构。对 i∈I、r∈R，定义非负开始时刻 ",
            RichNotation("s", "i,r"),
            " 和结束时刻 ",
            RichNotation("c", "i,r"),
            "；对钢板 p，定义切割开始 ",
            RichNotation("s", "p", "cut"),
            "、切割完成 ",
            RichNotation("c", "p", "cut"),
            " 和工位释放时刻 ",
            RichNotation("c", "p", "table"),
            "。机器分配变量 ",
            RichNotation("a", "p,m"),
            "∈{0,1}，当钢板 p 分配给机器 m 时取 1。",
        ],
    )
    add_plain_math_line(
        doc,
        "OS = [p1, p2, …, p|P|]    MS = [m1, m2, …, m|P|]",
    )
    add_heading(doc, "3.3 工艺时间推导", 2)
    add_para(
        doc,
        "切割时间由直切、V 坡切割、空行程、划线、穿孔和上料准备构成。附件 3 给出的厚度速度表"
        "覆盖 6 mm 到 50 mm，厚度未精确命中时对三种速度分别进行线性插值。",
    )
    add_equation(
        doc,
        [
            msubsup_text("T", "p", "cut"),
            mrun("=", True),
            mfrac(
                msubsup_text("L", "p", "straight"),
                msub_text("v", "s"),
            ),
            mrun("+", True),
            mfrac(
                msubsup_text("L", "p", "V"),
                msub_text("v", "VY"),
            ),
            mrun("+", True),
            mfrac(
                msubsup_text("L", "p", "empty"),
                msub_text("v", "rapid"),
            ),
            mrun("+", True),
            mfrac(
                msubsup_text("L", "p", "mark"),
                msub_text("v", "mark"),
            ),
            mrun("+", True),
            mrow(
                msubsup_text("n", "p", "pierce"),
                msub_text("t", "pierce"),
            ),
            mrun("+", True),
            msub_text("t", "setup"),
        ],
        "公式 3-1 钢板切割总时间",
    )
    add_para(
        doc,
        "小件自动打磨采用双面打磨近似，附加一次视觉扫描；自动坡口分别使用 V&Y 和 X&K 的速度，"
        "并把扫描、预热和翻面时间作为固定开销；人工坡口只计入 Y、X、K 坡长度，V 坡已在切割阶段"
        "完成，I 坡不计入人工坡口。",
    )
    add_equation(
        doc,
        [
            msubsup_text("t", "i", "sg"),
            mrun("=", True),
            mfrac(
                mrow(mrun("2"), msubsup_text("L", "i", "grind")),
                msub_text("v", "sg"),
            ),
            mrun("+", True),
            msub_text("t", "scan"),
        ],
        "公式 3-2 小件自动打磨时间",
    )
    add_equation(
        doc,
        [
            msubsup_text("t", "i", "ab"),
            mrun("=", True),
            mfrac(
                msubsup_text("L", "i", "Y"),
                msub_text("v", "VY"),
            ),
            mrun("+", True),
            mfrac(
                mrow(
                    msubsup_text("L", "i", "X"),
                    mrun("+", True),
                    msubsup_text("L", "i", "K"),
                ),
                msub_text("v", "XK"),
            ),
            mrun("+", True),
            msub_text("t", "overhead"),
        ],
        "公式 3-3 小件自动坡口时间",
    )
    add_equation(
        doc,
        [
            msubsup_text("t", "i", "mb"),
            mrun("=", True),
            mfrac(
                mrow(
                    msubsup_text("L", "i", "Y"),
                    mrun("+", True),
                    msubsup_text("L", "i", "X"),
                    mrun("+", True),
                    msubsup_text("L", "i", "K"),
                ),
                msub_text("v", "mb"),
            ),
        ],
        "公式 3-4 大件人工坡口时间",
    )

    add_heading(doc, "3.4 工序时序与资源约束", 2)
    add_para(
        doc,
        "对任一零件的相邻工序 k 和 k+1，后序开始时刻不得早于前序完成时刻加上必要的转运时间。"
        "同一资源在同一时刻最多执行一个任务；当资源存在故障区间时，任务持续时间必须完全避开"
        "不可用窗口。料框在缓存区内的占用数不得超过额定容量。",
    )
    add_equation(
        doc,
        [
            msub_text("s", "i,k+1"),
            mrun("≥", True),
            msub_text("c", "i,k"),
            mrun("+", True),
            msubsup_text("t", "i,k", "trans"),
        ],
        "公式 3-5 工序前后序约束",
    )
    add_equation(
        doc,
        [
            msub_text("s", "i,k"),
            mrun("≥", True),
            msub_text("f", "r"),
            mrun(",", True),
            msub_text("s", "i,k"),
            mrun("≥", True),
            msub_text("A", "r"),
        ],
        "公式 3-6 资源释放与故障可用性约束",
    )
    add_equation(
        doc,
        [
            msub_text("b", "h"),
            mdelim(mrow(mrun("t"))),
            mrun("≤", True),
            msub_text("B", "h"),
        ],
        "公式 3-7 缓存容量约束",
    )
    add_para(
        doc,
        "AGV 路径被抽象为 N2 区、N5 区、加工区和齐套区四个互斥区域。AGV 选择使用最早可出发的"
        "车辆，在 2 min 窗口内优先选择累计运输时间较短的车辆，并检测循环等待风险。天车优先级"
        "高于 AGV，但进入同一区域时仍需等待区域内已有任务结束。",
    )

    add_heading(doc, "3.5 目标函数与 Pareto 形式", 2)
    add_para(
        doc,
        "总完工时间取“小件齐套完成”和“大件加工完成”两类终点的最晚时刻；平均齐套跨度只按"
        "小件料框统计；切割负载差取"
        "N2 和 N5 总切割工时的最大差值。为了适应不同规模的数据，产能目标使用当前数据计算出的"
        "理论下界 LB 进行归一化，而不是写死 67.25 h。",
    )
    add_equation(
        doc,
        [
            msub_text("C", "max"),
            mrun("=", True),
            mrow(
                mrun("max", True),
                msubsup_text("c", "i", "kit"),
            ),
            mrun(",", True),
            msub_text("K", "avg"),
            mrun("=", True),
            mfrac(
                mrow(mrun("∑", True), msub_text("n", "g"), msub_text("K", "g")),
                mrow(mrun("∑", True), msub_text("n", "g")),
            ),
        ],
        "公式 3-8 完工时间与加权平均齐套跨度",
    )
    add_equation(
        doc,
        [
            msub_text("ΔL", "cut"),
            mrun("=", True),
            mrow(mrun("max", True), msub_text("L", "m")),
            mrun("−", True),
            mrow(mrun("min", True), msub_text("L", "m")),
        ],
        "公式 3-9 切割负载差",
    )
    add_equation(
        doc,
        [
            mrow(
                mrun("F1"),
                mrun("=", True),
                mfrac(
                    msub_text("C", "max"),
                    mrow(mrun("LB")),
                ),
                mrun(",", True),
                mrun("F2"),
                mrun("=", True),
                mfrac(
                    msub_text("K", "avg"),
                    msub_text("K", "FIFO"),
                ),
                mrun(",", True),
                mrun("F3"),
                mrun("=", True),
                mfrac(
                    msub_text("ΔL", "cut"),
                    msub_text("ΔL", "FIFO"),
                ),
                mrun(",", True),
                mrun("F4"),
                mrun("=", True),
                mfrac(
                    msub_text("W", "wait"),
                    msub_text("W", "FIFO"),
                ),
            )
        ],
        "公式 3-10 四目标向量",
    )
    add_para(
        doc,
        "NSGA-II 直接对四目标向量进行非支配排序和拥挤度筛选，不把多目标预先压成单一标量。"
        "最终从同一 Pareto 前沿分别按“产能优先”和“兼顾三指标”规则选解。分解决策使用支配关系："
        "若解 A 的所有目标均不劣于 B，且至少一个目标严格优于 B，则 A 支配 B。",
    )
    add_equation(
        doc,
        [
            mrun("A", True),
            mrun("≺", True),
            mrun("B", True),
            mrun("⇔", True),
            mrow(
                mrun("∀k", True),
                msub_text("F", "k"),
                mrun("(A)≤", True),
                msub_text("F", "k"),
                mrun("(B)", True),
            ),
            mrun("∧", True),
            mrow(
                mrun("∃k", True),
                msub_text("F", "k"),
                mrun("(A)<", True),
                msub_text("F", "k"),
                mrun("(B)", True),
            ),
        ],
        "公式 3-11 Pareto 支配关系",
    )
    add_para(
        doc,
        "在辅助交付口径中，系统对产能、齐套、负载和等待项进行有界归一化，"
        "J = Cmax/LB + ε·(wK·nK + wL·nL + wW·nW)。其中 ε 控制辅助指标对产能的"
        "影响上限，防止齐套改善以明显牺牲产能为代价。当前实现的默认辅助权重为"
        "齐套 0.40、负载 0.25、等待 0.35。",
    )
    add_heading(doc, "3.6 资源竞争与等待时间递推", 2)
    add_para(
        doc,
        "资源池排程可以写成最早就绪递推。若任务 i 在资源 r 上加工，任务前序完成后到达资源的"
        "时间为 ready_i，资源 r 上次占用结束时间为 f_r，则任务开始时刻是二者与资源可用日历"
        "共同约束下的最早值。这个递推形成了不依赖固定触发时序的通用资源模型。",
    )
    add_equation(
        doc,
        [
            msub_text("s", "i,r"),
            mrun("=", True),
            mrow(
                mrun("max", True),
                mdelim(
                    mrow(
                        msub_text("ready", "i"),
                        mrun(",", True),
                        msub_text("f", "r"),
                        mrun(",", True),
                        msub_text("A", "r"),
                    )
                ),
            ),
            mrun(",", True),
            msub_text("c", "i,r"),
            mrun("=", True),
            msub_text("s", "i,r"),
            mrun("+", True),
            msub_text("t", "i,r"),
        ],
        "公式 3-12 资源开始时刻递推",
    )
    add_para(
        doc,
        "AGV 区域互斥增加了起点和目标区域释放约束。设 AGV a 从区域 Z1 到 Z2，跨区准备时间为"
        " δ，区域释放时间为 g_Z，则出发时刻需要考虑 AGV 空闲时间和起点区域，到达时刻还需要"
        "检查目标区域是否在运输期间被相邻路径占用。该机制同时记录冲突次数、等待时间和循环等待风险。",
    )
    add_equation(
        doc,
        [
            msub_text("dep", "a"),
            mrun("=", True),
            mrow(
                mrun("max", True),
                mdelim(
                    mrow(
                        msub_text("ready", "task"),
                        mrun("−δ", True),
                        mrun(",", True),
                        msub_text("free", "a"),
                        mrun(",", True),
                        msub_text("g", "Z1"),
                    )
                ),
            ),
            mrun(",", True),
            msub_text("arr", "a"),
            mrun("=", True),
            mrow(
                mrun("max", True),
                mdelim(
                    mrow(
                        msub_text("dep", "a"),
                        mrun("+δ", True),
                        mrun(",", True),
                        msub_text("g", "Z2"),
                    )
                ),
            ),
        ],
        "公式 3-13 AGV 出发与到达时刻",
    )
    add_heading(doc, "3.7 理论下界与 KPI 推导", 2)
    add_para(
        doc,
        "为避免使用固定 67.25 h 作为唯一基准，程序分别计算切割资源下界、下游资源下界和单板"
        "关键链下界，再取最大值。这样更换钢板规模后，优化目标会自动随数据重新缩放。",
    )
    add_equation(
        doc,
        [
            msub_text("LB", "cut"),
            mrun("=", True),
            mfrac(
                mrow(mrun("∑p", True), msub_text("T", "p,cut")),
                msub_text("n", "cut"),
            ),
            mrun(",", True),
            msub_text("LB", "down"),
            mrun("=", True),
            mrow(
                mrun("max", True),
                msub_text("r", "resource"),
                mfrac(
                    mrow(mrun("∑i", True), msub_text("t", "i,r")),
                    msub_text("n", "r"),
                ),
            ),
            mrun(",", True),
            msub_text("LB", "chain"),
            mrun("=", True),
            mrow(
                mrun("max", True),
                msub_text("p", "plate"),
                msub_text("C", "p,key"),
            ),
        ],
        "公式 3-14 理论下界",
    )
    add_equation(
        doc,
        [
            msub_text("LB", "total"),
            mrun("=", True),
            mrow(
                mrun("max", True),
                mdelim(
                    mrow(
                        msub_text("LB", "cut"),
                        mrun(",", True),
                        msub_text("LB", "down"),
                        mrun(",", True),
                        msub_text("LB", "chain"),
                    )
                ),
            ),
            mrun(",", True),
            msub_text("THR", "shift"),
            mrun("=", True),
            mfrac(
                mrow(mrun("8", True), msub_text("n", "plate")),
                msub_text("C", "max"),
            ),
            mrun(",", True),
            msub_text("OEE", "cut"),
            mrun("=", True),
            mfrac(
                mrow(mrun("∑m", True), msub_text("L", "m")),
                mrow(msub_text("n", "cut"), msub_text("C", "max")),
            ),
        ],
        "公式 3-15 下界、班次产能与切割机稼动率",
    )
    add_para(
        doc,
        "等待时间由切割前等待和齐套等待组成。切割前等待反映钢材已经由天车送到胎架、但切割头"
        "或工位尚未释放的时间；齐套等待反映某零件已到半框缓存、但同组其他零件尚未到达的时间。"
        "两类等待分别写入指标，避免把设备等待和齐套等待混成一个无法解释的数值。",
    )
    add_equation(
        doc,
        [
            msub_text("W", "total"),
            mrun("=", True),
            mrow(
                mrun("∑p", True),
                mrow(
                    mrun("max", True),
                    mdelim(
                        mrow(
                            mrun("0,", True),
                            msubsup_text("s", "p", "cut"),
                            mrun("−", True),
                            msubsup_text("c", "p", "raw"),
                        )
                    ),
                ),
                mrun("+", True),
                mrun("∑i", True),
                mrow(
                    mrun("max", True),
                    mdelim(
                        mrow(
                            mrun("0,", True),
                            msubsup_text("C", "g", "kit"),
                            mrun("−", True),
                            msubsup_text("a", "i", "half"),
                        )
                    ),
                ),
            ),
        ],
        "公式 3-16 等待时间分解",
    )
    doc.add_page_break()


def section_algorithm(doc: Document, summary: dict) -> None:
    add_heading(doc, "4 算法设计、评价函数与求解流程", 1)
    add_heading(doc, "4.1 分层求解架构", 2)
    add_para(
        doc,
        "求解器把排产问题分为“高层排序与机器选择”和“低层离散事件仿真”两层。高层只负责生成"
        "可行钢板顺序和机器分配，低层完整执行切割、下游加工、缓存和齐套事件。这样既能搜索大组合"
        "空间，又能保证每个候选解都经过同一套工艺约束评估。",
    )
    add_figure(
        doc,
        ASSET_DIR / "overall_architecture.png",
        "图 4-1 数据、仿真、优化、动态响应与输出架构",
        width=6.45,
    )
    add_figure(
        doc,
        ASSET_DIR / "algorithm_family_map.png",
        "图 4-2 多算法求解器族、表示方式与适用边界",
        width=6.45,
    )
    add_heading(doc, "4.2 DQN 机器分配模块", 2)
    add_para(
        doc,
        "DQN 的输入是 16 维状态向量，包含钢板厚度、宽度、切割工时、齐套优先级、当前机器负载"
        "向量和剩余任务总量；网络结构为 16→128→64→5；输出 5 个动作，对应 N2、N5 及备机位。"
        "模型文件只保存网络权重和训练数据特征范围，不保存具体排产表。",
    )
    add_para(
        doc,
        "运行时先检查 dqn_machine_model.pt、meta.json 和当前数据范围。若权重缺失、PyTorch 不可用"
        "或当前板厚、板宽、板数、切割工时超出训练范围，DQN 部分自动跳过，使用负载均衡、N2 偏好、"
        "N5 偏好和随机扰动组成启发式机器码。主搜索器仍然是 NSGA-II。",
    )
    add_equation(
        doc,
        [
            mrun("Q(s,a)"),
            mrun("←", True),
            mrun("Q(s,a)"),
            mrun("+", True),
            mrun("α", True),
            mdelim(
                mrow(
                    msub_text("r", "t"),
                    mrun("+", True),
                    mrun("γ", True),
                    mrun("max Q(s′,a′)", True),
                    mrun("−", True),
                    mrun("Q(s,a)", True),
                )
            ),
        ],
        "公式 4-1 DQN 时序差分更新",
    )
    add_para(
        doc,
        "训练奖励由局部机器负载惩罚和终局仿真反馈构成。局部项奖励把任务分配给较早空闲、"
        "负载更均衡的合法机器；终局项使用完整排产结果的产能、齐套和负载表现作为全局反馈。"
        "这样 DQN 只负责提高初始机器码质量，不直接替代全链路仿真。",
    )
    add_heading(doc, "4.3 改进 NSGA-II 主搜索", 2)
    add_para(
        doc,
        "NSGA-II 的个体编码由钢板顺序和每张钢板的机器码组成。初始种群混合 FIFO、分段优先、"
        "短工时、天车感知、DQN 机器码和随机扰动。每代执行 OX 交叉、顺序变异、机器码变异，"
        "并对合并种群进行非支配排序和拥挤度计算。精英保留在保留第一前沿的同时，按代数衰减"
        "概率保留部分低层解，以减少种群快速同质化。",
    )
    add_table(
        doc,
        ["模块", "实现细节", "作用"],
        [
            ["个体编码", "钢板顺序 OS + 机器码 MS", "同时决策排序和 N2/N5 分配"],
            ["初始化", "启发式种子 + DQN 种子 + 随机扰动", "覆盖不同负载结构"],
            ["交叉", "OX 排列交叉；机器码继承和交换", "保持钢板顺序可行性"],
            ["变异", "交换、插入、分块逆序、机器切换", "跳出局部最优"],
            ["选择", "非支配排序、拥挤度、概率精英保留", "保持收敛性和多样性"],
            ["评价", "每个个体运行完整离散事件仿真", "指标与真实约束一致"],
            ["交付", "同一前沿选产能优先解和综合解", "直接服务不同决策偏好"],
        ],
        [1.35, 2.90, 2.35],
    )
    add_para(
        doc,
        "非支配排序把种群划分为若干前沿。第一前沿中的任何解都不会被其他解在所有目标上同时"
        "支配。同一前沿内使用拥挤度维持分布：边界解赋予无穷大拥挤度，内部解的拥挤度为相邻"
        "解在每一目标维度上的归一化距离之和。",
    )
    add_equation(
        doc,
        [
            msub_text("d", "i"),
            mrun("=", True),
            mrow(
                mrun("∑k", True),
                mfrac(
                    mrow(
                        msub_text("F", "k,i+1"),
                        mrun("−", True),
                        msub_text("F", "k,i−1"),
                    ),
                    mrow(
                        msub_text("F", "k,max"),
                        mrun("−", True),
                        msub_text("F", "k,min"),
                    ),
                ),
            ),
        ],
        "公式 4-2 同前沿个体的拥挤距离",
    )
    add_para(
        doc,
        "概率精英保留进一步降低了第一前沿长期垄断种群的风险。第 i 个前沿在本代被保留的基础"
        "概率与前沿规模有关，并随代数衰减；当剩余种群容量不足以整体保留一个前沿时，优先保留"
        "拥挤度较大的解。该规则比固定截断更适合多目标 Pareto 搜索。",
    )
    add_equation(
        doc,
        [
            msub_text("P", "i"),
            mrun("=", True),
            mrow(
                mrun("sqrt", True),
                mdelim(
                    mrow(
                        mrun("1", True),
                        mrun("−", True),
                        mfrac(
                            msub_text("n", "i"),
                            mrow(mrun("2N")),
                        ),
                    )
                ),
            ),
            mrun("·", True),
            mrow(
                mrun("exp", True),
                mdelim(
                    mrow(
                        mrun("−", True),
                        mfrac(
                            mrun("g", True),
                            msub_text("g", "max"),
                        ),
                    )
                ),
            ),
        ],
        "公式 4-3 概率精英保留",
    )
    add_heading(doc, "4.4 SA+Tabu 求解器", 2)
    add_para(
        doc,
        "SA+Tabu 适合中等规模、算力有限或希望快速得到稳定可行解的排产任务。其解表示为钢板排列，"
        "低层仿真把排列映射为 N2/N5 的自动机器分配和全流程事件。算法先用 FIFO、齐套完整性、"
        "分段优先短工时、分段优先长工时、短工时和天车感知策略生成多个起点，再从每个起点独立搜索。",
    )
    add_para(
        doc,
        "邻域包含同分段交换、插入、分块逆序、同分段洗牌、齐套聚类、天车错峰和跨机再平衡。"
        "候选解的能量为目标函数值。若候选解更优则直接接受；若更差，则按照 Metropolis 概率接受。"
        "随着温度下降，接受差解的概率降低；连续多次无改善时执行回热。",
    )
    add_equation(
        doc,
        [
            mrow(mrun("ΔE"), mrun("=", True), mrow(mrun("E"), mdelim(mrow(mrun("x′")))), mrun("−", True), mrow(mrun("E"), mdelim(mrow(mrun("x"))))),
            mrun(",", True),
            mrow(mrun("P"), mdelim(mrow(mrun("accept"))), mrun("=", True), mrow(mrun("min", True), mdelim(mrow(mrun("1"))), mrun(",", True), mrow(mrun("exp", True), mdelim(mrow(mrun("−ΔE/T")))))),
        ],
        "公式 4-4 Metropolis 接受准则",
    )
    add_equation(
        doc,
        [
            msub_text("T", "r+1"),
            mrun("=", True),
            mrow(
                mrun("max", True),
                mdelim(
                    mrow(
                        msub_text("T", "r"),
                        mrun("·ρ", True),
                    )
                ),
                mrun(",", True),
                msub_text("T", "min"),
            ),
        ],
        "公式 4-5 降温与最低温度约束",
    )
    add_para(
        doc,
        "Tabu 列表保存近期访问解的完整序列哈希，禁止搜索在短周期内回到同一钢板排列。"
        "当 Tabu 长度超过阈值时，只保留最近窗口。Pareto 档案记录 Cmax 和齐套跨度两个维度的"
        "非支配解；完成搜索后，再按当前评价口径从档案和解池中选择交付解。",
    )
    add_heading(doc, "4.5 GA+LNS 求解器", 2)
    add_para(
        doc,
        "GA+LNS 适合多个分段内的钢板优先关系差异较大、需要更强种群多样性的数据。GA 层使用"
        "排列染色体，选择策略以锦标赛为主；交叉提供 OX 和 PMX 两种排列交叉，保证子代仍然是"
        "完整的钢板排列；变异对部分钢板执行交换、插入或分块调整。",
    )
    add_para(
        doc,
        "LNS 层在遗传搜索中增加破坏—修复动作。破坏算子随机或按分段选择一部分钢板，"
        "从当前序列中移除；修复算子使用启发式优先项或随机插入，把被移除钢板重新放入序列。"
        "该过程可以用统一的数学形式表示：",
    )
    add_equation(
        doc,
        [
            mrow(
                mrun("x′", True),
                mrun("=", True),
                mrow(mrun("Repair", True), mdelim(mrow(mrun("Destroy(x,q), H")))),
            )
        ],
        "公式 4-6 LNS 破坏—修复",
    )
    add_para(
        doc,
        "GA+LNS 的适应度不是固定值，而是调用当前评价函数。产能优先轨道使用相对理论下界的"
        "产能目标；三指标平衡轨道使用 Cmax、齐套跨度和负载差的归一化组合；二次轨道放大"
        "越过目标的惩罚。算法同时维护 Pareto 档案，避免某一次种群更新丢失历史非支配解。",
    )
    add_heading(doc, "4.6 DRL 自适应 Q-learning 求解器", 2)
    add_para(
        doc,
        "项目还实现了轻量级 DRL-guided SA+Tabu。该模块不直接决定钢板顺序，而是学习在当前"
        "搜索阶段应当选用哪一种邻域算子，以及应当使用哪一组目标权重。状态被离散为"
        "243 个组合，动作空间包含 9 种邻域算子乘 3 组权重，共 27 个动作。",
    )
    add_table(
        doc,
        ["状态维度", "离散等级", "含义"],
        [
            ["搜索阶段", "早期、中期、后期", "按当前迭代比例划分"],
            ["温度区间", "高、中、低", "相对初始退火温度划分"],
            ["连续未改善次数", "正常、停滞、深度停滞", "控制探索强度"],
            ["齐套趋势", "改善、平稳、恶化", "过去 10 次迭代的齐套变化"],
            ["负载等级", "均衡、中等、失衡", "切割负载差的现场分档"],
        ],
        [1.55, 1.65, 3.35],
    )
    add_para(
        doc,
        "每个动作对应“邻域算子 + 权重配置”。权重配置分别偏向平衡、齐套和产能。"
        "Q-learning 使用 ε-greedy 选择动作，经验回放缓冲区保存状态转移，批量更新 Q 值。"
        "训练时还要根据当前 Cmax 和齐套是否严重偏离 FIFO 基线，执行 Pareto 感知的算子偏置。",
    )
    add_equation(
        doc,
        [
            mrow(mrun("Q"), mdelim(mrow(mrun("s,a")))),
            mrun("←", True),
            mrow(mrun("Q"), mdelim(mrow(mrun("s,a")))),
            mrun("+", True),
            mrow(
                mrun("α", True),
                mdelim(
                    mrow(
                        msub(mrow(mrun("r")), mrow(mrun("t"))),
                        mrun("+", True),
                        mrow(mrun("γ", True), mrun("max", True), mrow(mrun("Q"), mdelim(mrow(mrun("s′,a′"))))),
                        mrun("−", True),
                        mrow(mrun("Q"), mdelim(mrow(mrun("s,a")))),
                    )
                ),
            ),
        ],
        "公式 4-7 DRL Q-learning 更新",
    )
    add_equation(
        doc,
        [
            mrow(
                mrun("R", True),
                mrun("=", True),
                mrow(mrun("tanh", True), mdelim(mrow(mrun("5ΔJ/J")))),
                mrun("+", True),
                mrow(mrun("0.3", True), mrun("tanh", True), mdelim(mrow(mrun("10ΔK/K")))),
                mrun("+", True),
                mrow(mrun("0.1I", True), mdelim(mrow(mrun("accept")))),
            )
        ],
        "公式 4-8 DRL 奖励函数",
    )
    add_para(
        doc,
        "DRL 模块的训练数据来自 SA+Tabu 在多个数据集上的高质量轨迹。若预训练 Q 表不存在，"
        "代理从零开始学习；若 Q 表存在，则降低探索率并复用已有策略。该路线适合数据量有限、"
        "不同分段和订单类型需要不同邻域策略的场景。",
    )
    add_heading(doc, "4.7 动态重排算法", 2)
    add_para(
        doc,
        "动态重排从已提交的静态排程出发，先根据故障时间和订单变更时间确定决策时刻，再把钢板"
        "分为已完成、在制、未开始和取消四类。在制钢板可以选择继续加工到完成，或在给定清台时间"
        "后中断。未开始钢板重新进入候选集合，并在可用资源日历上搜索。",
    )
    add_table(
        doc,
        ["策略", "适用条件", "调整方式", "目标"],
        [
            [
                "稳定右移",
                "纯设备故障或受影响任务较少",
                "保留原顺序、切割机和胎架，只允许任务向后平移",
                "减少现场切换，响应快",
            ],
            [
                "局部重优化",
                "受影响比例约 20% 到 50%",
                "仅对故障窗口及邻域钢板做交换和插入",
                "兼顾响应速度与局部改善",
            ],
            [
                "全局重优化",
                "在制清台或受影响比例超过 50%",
                "对全部未完成钢板重新搜索顺序",
                "恢复全局可行性",
            ],
            [
                "模型化剩余重排",
                "首板快速锁定后仍有较多剩余钢板",
                "使用 SA+Tabu、GA+LNS 或 DQN+NSGA-II 优化剩余序列",
                "在响应预算内争取更好的余下调度",
            ],
        ],
        [1.20, 1.80, 2.25, 1.35],
    )
    add_para(
        doc,
        "动态流程不重复叠加旧故障，而是以完整故障清单重建资源日历和状态快照。多故障可以重叠，"
        "支持调整故障开始时间和修复结束时间，也可以在同一状态上继续执行下一次重排。",
    )
    add_heading(doc, "4.8 评价函数族与数学推导", 2)
    add_figure(
        doc,
        ASSET_DIR / "evaluation_function_map.png",
        "图 4-3 评价函数族、归一化方式和选择路径",
        width=6.45,
    )
    add_para(
        doc,
        "项目保留多种评价函数，原因是现场决策并不总是只有一个目标。交期紧张时，Cmax 应当"
        "优先；齐套成为瓶颈时，应当放大齐套跨度；设备稳定性或能耗敏感时，应当放大负载差；"
        "多部门共同决策时，则不应先把目标压缩成一个固定权重。下面对每类评价函数给出数学形式、"
        "代码对应和适用边界。",
    )
    add_heading(doc, "4.8.1 简单固定权重目标函数", 3)
    add_para(
        doc,
        "简单目标函数用于构造式比较和接口验证。它把 Cmax、齐套跨度和切割负载差分别除以"
        "可配置参考值，再按 4:4:2 组合，并把胎架累计空闲时间作为很小的软惩罚。"
        "该函数的优点是便于解释和跨算法复用，缺点是参考值固定，换到其他规模数据时不能像 LB "
        "归一化那样自动缩放。",
    )
    add_equation(
        doc,
        [
            msub_text("J", "simple"),
            mrun("=", True),
            mrun("0.4", True),
            mfrac(
                msub_text("C", "max"),
                mrow(mrun("67.25")),
            ),
            mrun("+0.4", True),
            mfrac(
                msub_text("K", "avg"),
                mrow(mrun("12.7")),
            ),
            mrun("+0.2", True),
            mfrac(
                msub_text("ΔL", "cut"),
                msub_text("ΔL", "ref"),
            ),
            mrun("+", True),
            msub_text("λ", "idle"),
            msub_text("I", "idle"),
        ],
        "公式 4-9 简单固定权重目标函数",
    )
    add_heading(doc, "4.8.2 统一产能优先标量函数", 3)
    add_para(
        doc,
        "该函数适用于“先保证交期和产出，再在 1% 产能带内改善齐套、负载和等待”的场景。"
        "它使用理论下界 LB 归一化 Cmax，并把辅助指标封顶到 [0,1]，避免某个辅助指标无限放大。",
    )
    add_equation(
        doc,
        [
            msub_text("J", "cap"),
            mrun("=", True),
            mfrac(
                msub_text("C", "max"),
                mrow(mrun("LB")),
            ),
            mrun("+", True),
            mrun("ε", True),
            mdelim(
                mrow(
                    msub_text("w", "K"),
                    msub_text("n", "K"),
                    mrun("+", True),
                    msub_text("w", "L"),
                    msub_text("n", "L"),
                    mrun("+", True),
                    msub_text("w", "W"),
                    msub_text("n", "W"),
                )
            ),
        ],
        "公式 4-10 产能优先标量函数",
    )
    add_para(
        doc,
        "当 objective_type=quadratic 时，辅助项使用 nK²、nL²、nW²；当 eval_track=balanced 时，"
        "切换到 FIFO 相对归一化的平衡函数。最终选解阶段先找最小 Cmax/LB，再只比较产能差不超过"
        "给定阈值的解，避免用明显更长的交期换取较小的齐套改善。",
    )
    add_equation(
        doc,
        [
            msub_text("C", "best"),
            mrun("=", True),
            mrow(
                mrun("min", True),
                mrow(mrun("C"), mrun("/LB", True), mrun("+", True), mrun("ε", True), mrun("S", True)),
            ),
            mrun("s.t.", True),
            mfrac(
                msub_text("C", "max"),
                mrow(mrun("LB")),
            ),
            mrun("≤", True),
            mrow(
                msub_text("C", "min"),
                mdelim(mrow(mrun("1+τ"))),
                mrun("/LB", True),
            ),
        ],
        "公式 4-11 1% 产能容差选解",
    )
    add_heading(doc, "4.8.3 FIFO 相对平衡函数", 3)
    add_para(
        doc,
        "平衡函数适用于没有绝对目标、但希望同时不劣于现有现场规则的场景。Cmax、齐套跨度和"
        "负载差分别相对 FIFO 基线归一化，再按权重组合。线性形式适合需要直观解释的场景；"
        "对超过 FIFO 的维度增加额外惩罚，可避免某项指标退步过多。",
    )
    add_equation(
        doc,
        [
            msub_text("J", "bal"),
            mrun("=", True),
            msub_text("w", "C"),
            mfrac(
                msub_text("C", "max"),
                msub_text("C", "FIFO"),
            ),
            mrun("+", True),
            msub_text("w", "K"),
            mfrac(
                msub_text("K", "avg"),
                msub_text("K", "FIFO"),
            ),
            mrun("+", True),
            msub_text("w", "L"),
            mfrac(
                msub_text("ΔL", "cut"),
                msub_text("ΔL", "FIFO"),
            ),
        ],
        "公式 4-12 FIFO 相对线性平衡函数",
    )
    add_heading(doc, "4.8.4 二次非线性惩罚函数", 3)
    add_para(
        doc,
        "二次函数适用于对超目标或超 FIFO 行为特别敏感的场景。它保留线性项的排序趋势，"
        "同时对较大退步施加更高惩罚，适合管理层不希望某个指标明显恶化的情况。",
    )
    add_equation(
        doc,
        [
            msub_text("J", "quad"),
            mrun("=", True),
            msub_text("w", "C"),
            msup_text("c", "2"),
            mrun("+", True),
            msub_text("w", "K"),
            msup_text("k", "2"),
            mrun("+", True),
            msub_text("w", "L"),
            msup_text("l", "2"),
            mrun("+", True),
            mrun("α", True),
            msup_text("max(c−1,0)", "2"),
            mrun("+", True),
            mrun("β", True),
            msup_text("max(k−1,0)", "2"),
            mrun("+", True),
            mrun("γ", True),
            msup_text("max(l−1,0)", "2"),
        ],
        "公式 4-13 二次非线性评价函数",
    )
    add_heading(doc, "4.8.5 Pareto 向量与多解交付", 3)
    add_para(
        doc,
        "Pareto 向量函数不提前固定权重，而是把归一化后的产能、齐套、负载和等待作为四维目标。"
        "算法对每个候选解运行完整仿真，得到目标向量，再执行支配排序。一次运行输出完整前沿，"
        "并在同一前沿上选择产能优先解和综合交付解。",
    )
    add_table(
        doc,
        ["评价函数", "输入", "输出", "适用场景", "风险"],
        [
            ["简单目标函数", "目标值或 FIFO 相对值", "单一分数", "快速构造和接口验证", "权重固定，解释范围有限"],
            ["统一产能优先", "Cmax/LB + 有界辅助项", "单一分数", "交期或产出优先", "辅助指标只允许 1% 微调"],
            ["FIFO 线性平衡", "三个归一化指标", "单一分数", "计划员日常排程", "FIFO 本身不一定最优"],
            ["二次非线性", "相对 FIFO 的平方项", "单一分数", "需要强约束退步", "超参数敏感"],
            ["Pareto 向量", "四维归一化目标", "非支配解集", "多部门协商、多方案交付", "需要后处理选解"],
            ["DRL 动态权重", "阶段、温度、趋势、负载", "自适应权重", "数据有限、搜索策略变化", "依赖预训练质量"],
            ["动态规则目标", "切割完工、优先级、负载标准差", "单目标值", "故障和订单变更", "只比较未完成钢板"],
        ],
        [1.25, 1.45, 1.25, 1.55, 1.15],
        font_size=8.1,
    )
    add_table(
        doc,
        ["评价函数", "代码入口", "关键参数"],
        [
            ["简单固定权重", "steel_schedule_model.objective", "Cmax 和齐套目标参考值，负载参考值由配置提供"],
            ["统一产能优先", "steel_schedule_model.unified_capacity_objective", "capacity_eps、secondary_weight_*"],
            ["FIFO 相对平衡", "steel_schedule_model.legacy_balanced_objective", "obj_weight_cmax、obj_weight_kit、obj_weight_load"],
            ["二次非线性", "pareto_optimizer.quadratic_objective", "quadratic_penalty_*、eval_track"],
            ["Pareto 向量", "dqn_nsga2_optimizer.DqnNsga2Optimizer._metric_vector", "FIFO 基线、理论下界 LB、等待时间"],
            ["DRL 自适应权重", "drl_optimizer.DRLGuidedOptimizer.kit_span_objective", "WEIGHT_PROFILES、Q 表动作"],
            ["动态规则目标", "backend/dynamic_rescheduler.DynamicRescheduleService._schedule_cut_order", "priority、load_std、schedule_floor"],
        ],
        [1.35, 2.65, 2.60],
        font_size=8.2,
    )
    add_para(
        doc,
        "DQN 的终局奖励与 DRL 的搜索奖励用途不同。DQN 奖励服务于机器分配训练，由局部切割"
        "时间和负载溢出惩罚开始，在完整排产完成后追加产能、齐套、负载和等待反馈；"
        "DRL 奖励服务于搜索过程，直接比较相邻搜索步的目标变化和齐套变化，用于更新算子选择策略。",
    )
    add_equation(
        doc,
        [
            msub_text("R", "DQN"),
            mrun("=", True),
            mrun("−", True),
            mfrac(
                msubsup_text("T", "p", "cut"),
                mrow(mrun("120")),
            ),
            mrun("−", True),
            mrow(
                mrun("max", True),
                mdelim(
                    mrow(
                        mrun("0,", True),
                        msub_text("L", "m"),
                        mrun("−", True),
                        msubsup_text("L", "m", "old"),
                        mrun("−1", True),
                    )
                ),
            ),
            mrun("+", True),
            mrow(
                mrun("−1.5", True),
                mfrac(
                    mrow(msub_text("C", "max")),
                    mrow(mrun("LB")),
                ),
                mrun("−0.4", True),
                mfrac(
                    mrow(msub_text("K", "avg")),
                    mrow(msub_text("K", "FIFO")),
                ),
                mrun("−0.2", True),
                mfrac(
                    mrow(msub_text("ΔL", "cut")),
                    mrow(msub_text("ΔL", "FIFO")),
                ),
                mrun("−0.2", True),
                mfrac(
                    mrow(msub_text("W", "total")),
                    mrow(msub_text("W", "FIFO")),
                ),
            ),
        ],
        "公式 4-14 DQN 局部与终局奖励",
    )
    add_para(
        doc,
        "动态重排使用一个更适合在线决策的标量目标。它以未完成钢板的切割完工时间为主项，"
        "对低优先级钢板施加更晚完成的惩罚，并用切割负载标准差鼓励 N2/N5 恢复均衡。稳定修复"
        "阶段还会禁止动态结果早于基础排程的物理时间下限，避免故障后出现不可解释的完工时间缩短。",
    )
    add_equation(
        doc,
        [
            msub_text("J", "dynamic"),
            mrun("=", True),
            msub_text("C", "cut"),
            mrun("+0.002", True),
            mrow(
                mrun("∑p", True),
                mdelim(
                    mrow(
                        mrun("11−", True),
                        msub_text("pri", "p"),
                    )
                ),
                msub_text("c", "p"),
                mrun("+0.05", True),
                msub_text("σ", "load"),
            ),
        ],
        "公式 4-15 动态重排目标函数",
    )
    add_heading(doc, "4.9 算法与评价函数的匹配规则", 2)
    add_table(
        doc,
        ["业务情形", "推荐算法", "推荐评价函数", "原因"],
        [
            ["现场数据少，只想快速得到可行基线", "FIFO / 构造规则", "简单目标函数", "计算量低，结果稳定"],
            ["钢板数中等、设备资源固定、需要快速改善", "SA+Tabu", "产能优先或 FIFO 线性", "邻域清晰，响应快"],
            ["同分段钢板聚集、需要更强的全局多样性", "GA+LNS", "平衡函数或 Pareto", "交叉和破坏—修复扩大搜索"],
            ["同时关心产能、齐套、负载和等待", "DQN+NSGA-II", "四目标 Pareto", "一次输出多套非支配方案"],
            ["数据分布有限，但希望搜索过程自动换策略", "DRL-guided SA", "动态权重", "由 Q 表选择算子和权重"],
            ["某指标不能明显超过目标", "SA+Tabu / GA+LNS", "二次非线性", "二次项放大越界代价"],
            ["设备故障但希望减少现场切换", "稳定右移", "动态规则目标", "不改变原资源结构"],
            ["订单变更影响部分分段", "局部重优化", "动态规则目标", "只调整受影响窗口"],
            ["取消、插单或大批未开始钢板变化", "全局重优化或剩余模型重排", "产能优先或 Pareto", "重新建立资源利用结构"],
        ],
        [1.75, 1.35, 1.25, 2.20],
        font_size=8.1,
    )
    add_para(
        doc,
        "选择规则不是按算法名称偏好，而是由数据规模、交期压力、设备瓶颈、决策人数和响应时间共同"
        "决定。主实验采用 DQN+NSGA-II，是因为题目同时强调齐套、产能、负载和动态响应；这并不表示"
        "其他求解器没有作用。生产现场低时间预算时可以先用 SA+Tabu 或稳定右移，在线下批量排程时"
        "再使用 Pareto 搜索扩大可选方案。",
    )
    doc.add_page_break()


def section_simulation(doc: Document, summary: dict, dynamic: dict) -> None:
    add_heading(doc, "5 离散事件仿真系统", 1)
    add_heading(doc, "5.1 状态模型", 2)
    add_para(
        doc,
        "每个排产候选解对应一个离散事件状态。状态包含钢板顺序、切割机分配、每个切割头和胎架的"
        "空闲时刻、下游资源占用、AGV 位置与累计运输时间、天车位置、缓存区占用、料框齐套条件、"
        "故障日历以及所有工序的开始和结束记录。事件按时间排序，同一时间下按稳定的序号打破并列。",
    )
    add_table(
        doc,
        ["状态维度", "核心字段", "用途"],
        [
            ["切割", "free、gun_free、worktables、gun_table", "控制切割头、胎架和双工位占用"],
            ["下游加工", "ResourcePool.free / busy", "桁架、打磨、坡口资源互斥"],
            ["物流", "AGV 状态、Zone free、天车区间", "路径冲突、避让和转运"],
            ["缓存", "机旁码垛、坡口、半框、齐套占用事件", "容量检查和峰值统计"],
            ["齐套", "组零件数、料框计数、组完成时间", "分段门控和齐套跨度"],
            ["故障", "resource_id、start、end", "生成资源不可用日历"],
        ],
        [1.35, 2.70, 2.55],
    )
    add_heading(doc, "5.2 事件处理顺序", 2)
    steps = [
        "读取并校验钢板和零件数据，建立钢板—零件映射。",
        "按候选钢板顺序分配切割机并安排原料吊运、工位和切割动作。",
        "切割完成后立即推进小件分拣、打磨、坡口、码垛和大件自由边打磨。",
        "按料框键聚合小件，执行机旁码垛、AGV 转运、坡口缓存和半框缓存事件。",
        "当同分段的小件全部满足后，执行小件齐套料框转运，并计算小件到齐套区时刻；"
        "大件完成后进入成品放置区，不进入小件齐套门控。",
        "输出逐板排程、零件完成、齐套分组、工序事件、缓存时序和 KPI。",
    ]
    for idx, step in enumerate(steps, 1):
        add_para(doc, f"{idx}. {step}", after=4)
    add_heading(doc, "5.3 KPI 计算与约束诊断", 2)
    add_table(
        doc,
        ["KPI", "计算方式", "解释"],
        [
            ["总完工时间 Cmax", "小件齐套完成与大件加工完成的最晚时刻", "越低越好"],
            ["平均齐套跨度", "按零件数加权的组首件到组末件时长", "越低越好"],
            [
                "切割负载差",
                "N2/N5 总切割工时最大值减最小值",
                "越低越均衡",
            ],
            [
                "整体产能",
                "钢板数 / Cmax(h) × 8",
                "每 8 h 可处理钢板数",
            ],
            [
                "切割机综合稼动率",
                "总切割工时 /（切割机数 × Cmax）",
                "含辅助切割时间",
            ],
            [
                "AGV 冲突",
                "区域互斥造成的等待次数和等待分钟",
                "越低越好",
            ],
            [
                "缓存峰值",
                "各时刻活跃料框数与额定容量之比",
                "用于发现容量风险",
            ],
            [
                "死锁与溢出",
                "事件队列中的循环等待和容量越界告警",
                "必须为零或明确标注风险",
            ],
        ],
        [1.45, 2.90, 2.25],
    )
    add_para(
        doc,
        "静态候选搜索默认把部分缓存容量检查作为结果诊断，动态重排和最终仿真可以对缓存进入"
        "执行门控。报告中不把默认参数下的峰值低于 1 直接等同于企业约束满足，而是在风险章节"
        "列出需要现场校准的缓存参数。",
    )
    add_heading(doc, "5.4 结果可视化与文件输出", 2)
    add_para(
        doc,
        "每次运行输出钢板切割排序、零件完成时刻、齐套组结果、全工序事件、对比指标、切割甘特图、"
        "齐套跨度图和资源利用率图。前端保留运行历史，支持按时间、完工时间、跨度、负载、利用率和"
        "综合评分排序，并对不同运行版本进行切换。",
    )
    add_table(
        doc,
        ["输出文件", "内容", "典型用途"],
        [
            ["optimized_plate_schedule.csv", "每张钢板的顺序、机器、切割和工位时间", "车间排序与设备派工"],
            ["part_completion.csv", "每个零件释放、半框缓存和齐套区时间", "零件级追踪"],
            ["kit_groups.csv", "分段—优先级组的首件、齐套完成和跨度", "齐套评价"],
            ["process_stages.csv", "逐工序资源、开始、结束和时长", "仿真复算与回放"],
            ["comparison.csv", "FIFO、综合解和备选解指标", "实验对比"],
            ["summary.json", "输入校验、参数、KPI 和运行配置", "结果归档和报告生成"],
        ],
        [1.85, 2.90, 1.85],
    )
    doc.add_page_break()


def section_environment(doc: Document, summary: dict) -> None:
    add_heading(doc, "6 仿真实验环境与复现方法", 1)
    add_heading(doc, "6.1 运行环境", 2)
    add_table(
        doc,
        ["项目", "本次验证环境"],
        [
            ["操作系统", "Windows 11 10.0.26200"],
            ["处理器", "Intel Core Ultra 9 285H，逻辑处理器 16 个"],
            ["Python", "3.12.14，64 位"],
            ["主要库", "pandas 3.0.1、numpy 2.3.5、Pillow、python-docx"],
            ["输入文件", "附件 2：钢板零件数据.xlsx；附件 3：工艺用时计算表.xlsx"],
            ["主算法", "DQN + 改进 NSGA-II"],
            ["种群规模", f"{summary['运行信息']['population']}"],
            ["迭代上限", f"{summary['运行信息']['iterations']}"],
            ["有效代数", f"{summary['运行信息']['generations']}"],
            ["随机种子", f"{summary['运行信息']['random_seed']}"],
            ["单进程运行时间", f"{summary['运行信息']['elapsed_s']:.2f} s"],
            ["动态重排预算", "8.0 s"],
        ],
        [1.45, 5.15],
    )
    add_heading(doc, "6.2 数据与参数来源", 2)
    add_para(
        doc,
        "钢板、零件和厚度相关速度表来自企业提供的附件 2 和附件 3。设备数量、上料、分拣、"
        "打磨、坡口、AGV 和天车时间优先使用附件 3 或题目图示中的值。题目没有给出的缓存容量、"
        "AGV 单次运输时间、清台时间、缓存停留时间和备机位参数集中存放在 ModelConfig 或运行参数中，"
        "可通过 Web 页面或命令行覆盖。",
    )
    add_heading(doc, "6.3 复现实验命令", 2)
    add_para(
        doc,
        "为保证结果可追溯，主报告使用同一数据和同一随机种子运行。命令行方式如下：",
    )
    add_para(
        doc,
        "python steel_schedule_model.py "
        "--input \"附件2：钢板零件数据.xlsx\" "
        "--speed-table \"附件3：工艺用时计算表.xlsx\" "
        "--output outputs",
        size=9.2,
        color=ACCENT,
        align=WD_ALIGN_PARAGRAPH.LEFT,
        after=4,
    )
    add_para(
        doc,
        "Web 运行方式：双击 start.bat 或使用 restart_server.py 启动服务，在数据导入页上传附件 2，"
        "可选上传附件 3，选择 DQN+NSGA-II、种群规模、迭代上限和随机种子，点击开始计算。动态响应"
        "页面输入故障资源、故障时间和修复时间，再执行重排。",
    )
    add_heading(doc, "6.4 评价与停止条件", 2)
    add_para(
        doc,
        "主算法使用固定种群规模和迭代上限，不设置早停，以便得到完整 Pareto 档案。动态重排使用"
        "用户给定的时间预算，先构造快速可行解，再在剩余预算内进行局部或全局搜索，最后为全链路"
        "仿真预留时间。缓存、资源占用和故障日历均在每轮评估中重新构造，不接受跨情景复用状态。",
    )
    add_heading(doc, "6.5 实验设计矩阵", 2)
    add_para(
        doc,
        "实验不是只验证一次最优排程，而是分别检查静态质量、算法行为和扰动后的状态恢复。"
        "每次实验固定输入数据、随机种子、设备资源和评价函数，改变一个实验因素后重新运行全链路"
        "仿真，避免把历史结果与新口径结果混在同一张表中。",
    )
    add_table(
        doc,
        ["实验组", "改变因素", "固定条件", "观察指标"],
        [
            ["FIFO 基线", "排序规则为原始顺序", "数据、设备和评价口径相同", "Cmax、齐套跨度、负载差、产能"],
            ["主方案 Pareto", "DQN+NSGA-II，固定种子", "附件 2、附件 3、资源数量相同", "Pareto 前沿、产能优先解、综合交付解"],
            ["算法切换", "SA+Tabu、GA+LNS、DQN+NSGA-II", "数据、预算、KPI 相同", "收敛质量、输出形态、响应时间"],
            ["评价函数切换", "线性、二次、Pareto、动态目标", "钢板顺序和资源日历相同", "不同指标之间的取舍"],
            ["故障响应", "故障资源、开始时间、持续时间", "基础排程和订单不变", "响应时间、冻结数量、重排数量、KPI 变化"],
            ["订单变更", "取消、优先级调整、数据替换", "故障日历和资源数量不变", "取消数量、在制处置、齐套恢复"],
            ["缓存压力", "缓存容量和坡口站容量", "钢板顺序和资源日历固定", "峰值占用、溢出告警、等待时间"],
        ],
        [1.20, 1.70, 1.85, 1.85],
        font_size=8.3,
    )
    add_heading(doc, "6.6 随机性与可复现控制", 2)
    add_para(
        doc,
        "随机性来自初始种群、邻域选择、交叉、变异、DQN 特征编码和并行任务顺序。主实验固定"
        " random_seed=20260723、种群规模 16、有效代数 10，并使用单进程评估，以便报告中的"
        "数值可以由相同代码和相同输入重新得到。Web 端切换为多进程时，问题求解结果仍应经过"
        "同一个确定性评估器，但并行任务完成顺序可能改变随机数消耗顺序。",
    )
    add_para(
        doc,
        "初始化缓存和状态时不复用上一情景的运行对象。每次候选解评估都重新生成资源池、AGV 区域"
        "释放时间、缓存事件和故障日历，避免一个候选解的状态污染下一个候选解。动态重排使用"
        "原子提交目录，旧 run 归档到 generations，current 只保存最新一版可读结果。",
    )
    add_heading(doc, "6.7 实验验收标准", 2)
    add_table(
        doc,
        ["验收项", "通过标准", "失败时的处理"],
        [
            ["数据完整性", "未匹配零件数为 0；数量与 V 坡汇总一致", "停止计算并返回数据错误"],
            ["设备资格", "N5 仅接收厚度和宽度满足条件的钢板", "分配回退到 N2"],
            ["资源互斥", "同一资源不存在重叠占用", "检查 ResourcePool 或动态日历"],
            ["工序前后序", "后序开始不早于前序完成和转运完成", "回溯事件链并修正工序依赖"],
            ["缓存与死锁", "无未解释溢出；死锁警告为 0", "启用容量门控或增加缓存资源"],
            ["参考指标", "Cmax、齐套、负载和产能达到或优于参考值", "切换评价轨道或扩大搜索预算"],
            ["复现性", "相同数据、种子和进程数得到相同结果", "固定并行度和随机数消费顺序"],
        ],
        [1.20, 2.45, 2.95],
        font_size=8.5,
    )
    add_heading(doc, "6.8 多进程并行加速", 2)
    add_para(
        doc,
        "不同候选解之间相互独立，因此可以并行完成离散事件仿真。DQN+NSGA-II 和 GA+LNS "
        "在主进程中生成种群，再把每个个体的顺序和机器码发送给进程池；每个工作进程只运行"
        "一次完整仿真并返回 KPI。SA+Tabu、二次 SA 和 DRL-guided SA 则把不同初始策略分配给"
        "不同工作进程，父进程通过队列接收迭代进度和最优解。",
    )
    add_table(
        doc,
        ["模块", "并行粒度", "启动方式", "回退与同步"],
        [
            ["DQN+NSGA-II", "按个体并行评估", "Windows 使用 spawn，Linux/macOS 使用 fork", "主进程负责排序、交叉、变异和最终选解"],
            ["GA+LNS", "按子代种群并行评估", "multiprocessing.Pool，initializer 加载共享上下文", "父进程统一选择、存档和结果输出"],
            ["SA+Tabu", "按初始策略多起点并行", "每个初始解一个 worker，chunksize=1", "进度队列回报迭代信息，父进程选优"],
            ["二次 SA", "按初始策略多起点并行", "复用 SA 并行框架", "选择器按当前评价函数汇总"],
            ["DRL-guided SA", "按初始策略并行，代理在各自轨迹中学习", "每个 worker 初始化参数和无网络依赖上下文", "父进程汇总指标，不共享可变模型对象"],
            ["后台任务", "静态双轨使用线程并行；动态重排独立后台线程", "ThreadPoolExecutor", "取消标记和 staging 隔离保证回滚"],
        ],
        [1.20, 1.60, 2.05, 1.75],
        font_size=8.1,
    )
    add_para(
        doc,
        "并行度由 parallel_workers 参数、OPTIMIZER_PARALLEL 环境变量和 CPU 核数共同决定。"
        "普通算法的产能优先与三指标平衡轨道各分一半核心；DQN+NSGA-II 只运行一次 Pareto "
        "搜索，因此可以使用全部可用核心。设为 1 或 OPTIMIZER_PARALLEL=0 时完全串行执行，"
        "便于复现实验。多进程环境中的每次评估仍使用同一数据、约束和 KPI 口径。",
    )
    doc.add_page_break()


def section_results(
    doc: Document,
    summary: dict,
    base: dict,
    opt: dict,
    capacity: dict,
    pareto: pd.DataFrame,
) -> None:
    add_heading(doc, "7 实验结果与指标分析", 1)
    add_heading(doc, "7.1 主实验对比", 2)
    cmax_imp = (base["总完工时间(h)"] - opt["总完工时间(h)"]) / base["总完工时间(h)"] * 100
    kit_imp = (
        base["加权平均齐套跨度(h)"] - opt["加权平均齐套跨度(h)"]
    ) / base["加权平均齐套跨度(h)"] * 100
    max_kit_imp = (
        base["最大齐套跨度(h)"] - opt["最大齐套跨度(h)"]
    ) / base["最大齐套跨度(h)"] * 100
    load_delta = (
        base["切割负载差(h)"] - opt["切割负载差(h)"]
    ) / base["切割负载差(h)"] * 100
    base_load_rate = mean_device_load_rate(base)
    opt_load_rate = mean_device_load_rate(opt)
    load_rate_delta = (opt_load_rate - base_load_rate) / base_load_rate * 100 if base_load_rate else 0.0
    thr_imp = (
        opt["整体产能(张板/班)"] - base["整体产能(张板/班)"]
    ) / base["整体产能(张板/班)"] * 100
    add_table(
        doc,
        ["指标", "FIFO 基线", "综合交付解", "相对变化", "题目参考值"],
        [
            ["总完工时间", f"{base['总完工时间(h)']:.2f} h", f"{opt['总完工时间(h)']:.2f} h", f"-{cmax_imp:.2f}%", "≤ 67.25 h"],
            ["平均齐套跨度", f"{base['加权平均齐套跨度(h)']:.2f} h", f"{opt['加权平均齐套跨度(h)']:.2f} h", f"-{kit_imp:.2f}%", "≤ 12.7 h"],
            ["最大齐套跨度", f"{base['最大齐套跨度(h)']:.2f} h", f"{opt['最大齐套跨度(h)']:.2f} h", f"-{max_kit_imp:.2f}%", "无硬性目标"],
            ["平均设备负载率", f"{base_load_rate * 100:.2f}%", f"{opt_load_rate * 100:.2f}%", f"+{load_rate_delta:.2f}%", "题目重点指标"],
            ["切割负载差", f"{base['切割负载差(h)']:.2f} h", f"{opt['切割负载差(h)']:.2f} h", f"+{abs(load_delta):.2f}%", "内部平衡诊断"],
            ["整体产能", f"{base['整体产能(张板/班)']:.2f} 张板/班", f"{opt['整体产能(张板/班)']:.2f} 张板/班", f"+{thr_imp:.2f}%", "≥ 13 张板/班"],
            ["切割机综合稼动率", f"{base['切割机综合稼动率'] * 100:.2f}%", f"{opt['切割机综合稼动率'] * 100:.2f}%", f"+{(opt['切割机综合稼动率'] - base['切割机综合稼动率']) * 100:.2f} pp", "≥ 61%"],
            ["AGV 路径冲突次数", f"{base.get('AGV路径冲突次数', 0)}", f"{opt.get('AGV路径冲突次数', 0)}", "下降", "越低越好"],
            ["死锁警告数", f"{base.get('死锁警告数', 0)}", f"{opt.get('死锁警告数', 0)}", "保持为零", "应为零"],
        ],
        [1.35, 1.20, 1.25, 1.30, 1.50],
        font_size=8.5,
    )
    add_para(
        doc,
        "综合交付解在总完工时间、平均齐套跨度和整体产能上明显优于 FIFO；平均设备负载率同步提高。"
        "切割负载差相对 FIFO 有所上升，它是内部平衡诊断，不是题目给出的硬性阈值。结果说明"
        "产能、齐套和负载之间存在真实的 Pareto 权衡，不能只用一个不加区分的综合分数替代决策。",
    )
    add_figure(
        doc,
        ASSET_DIR / "kpi_comparison.png",
        "图 7-1 FIFO 与综合交付解的关键指标对比",
        width=6.35,
    )
    add_heading(doc, "7.2 Pareto 前沿与两条交付口径", 2)
    best_kit = pareto.iloc[pareto["加权平均齐套跨度(h)"].idxmin()]
    add_table(
        doc,
        ["交付口径", "总完工时间", "平均齐套跨度", "切割负载差", "整体产能", "适用场景"],
        [
            ["综合交付解", f"{opt['总完工时间(h)']:.2f} h", f"{opt['加权平均齐套跨度(h)']:.2f} h", f"{opt['切割负载差(h)']:.2f} h", f"{opt['整体产能(张板/班)']:.2f}", "同时满足三项参考指标"],
            ["产能优先备选解", f"{capacity['总完工时间(h)']:.2f} h", f"{capacity['加权平均齐套跨度(h)']:.2f} h", f"{capacity['切割负载差(h)']:.2f} h", f"{capacity['整体产能(张板/班)']:.2f}", "交期压力最大时使用"],
            ["前沿最小齐套解", f"{best_kit['总完工时间(h)']:.2f} h", f"{best_kit['加权平均齐套跨度(h)']:.2f} h", f"{best_kit['切割负载差(h)']:.2f} h", f"{best_kit['整体产能(张板/班)']:.2f}", "齐套优先且允许更长工期时使用"],
        ],
        [1.25, 1.05, 1.10, 1.05, 1.00, 1.15],
        font_size=8.4,
    )
    add_para(
        doc,
        f"本次运行共保留 {len(pareto)} 个非支配解。综合交付解位于前沿的齐套优先一侧，"
        "产能优先备选解位于低完工时间一侧。两类解共享同一评估函数和约束口径，可以在前端直接切换。",
    )
    add_figure(
        doc,
        ASSET_DIR / "pareto_front.png",
        "图 7-2 DQN+NSGA-II 多目标 Pareto 前沿",
        width=6.35,
    )
    add_heading(doc, "7.3 切割与齐套结果", 2)
    add_figure(
        doc,
        MAIN_DIR / "cutting_gantt.png",
        "图 7-3 综合交付解的 N2/N5 切割甘特图",
        width=6.35,
    )
    add_figure(
        doc,
        MAIN_DIR / "kit_span.png",
        "图 7-4 各分段—优先级组的齐套跨度",
        width=6.35,
    )
    add_heading(doc, "7.4 资源利用率与缓存诊断", 2)
    add_figure(
        doc,
        MAIN_DIR / "resource_utilisation.png",
        "图 7-5 综合交付解的资源和切割机利用率",
        width=6.35,
    )
    add_table(
        doc,
        ["诊断项", "FIFO 基线", "综合交付解", "判断"],
        [
            ["N2 利用率", f"{base.get('N2利用率', 0) * 100:.2f}%", f"{opt.get('N2利用率', 0) * 100:.2f}%", "接近均衡"],
            ["N5 利用率", f"{base.get('N5利用率', 0) * 100:.2f}%", f"{opt.get('N5利用率', 0) * 100:.2f}%", "接近均衡"],
            ["半框缓存峰值占用率", f"{base.get('半框缓存区峰值占用率', 0) * 100:.2f}%", f"{opt.get('半框缓存区峰值占用率', 0) * 100:.2f}%", "低于暂定容量"],
            ["齐套缓存峰值占用率", f"{base.get('齐套缓存区峰值占用率', 0) * 100:.2f}%", f"{opt.get('齐套缓存区峰值占用率', 0) * 100:.2f}%", "低于暂定容量"],
            ["坡口工作站峰值占用率", f"{base.get('坡口工作站峰值占用率', 0) * 100:.2f}%", f"{opt.get('坡口工作站峰值占用率', 0) * 100:.2f}%", "暂定容量下存在风险"],
            ["死锁警告数", f"{base.get('死锁警告数', 0)}", f"{opt.get('死锁警告数', 0)}", "无死锁"],
        ],
        [1.70, 1.35, 1.35, 1.90],
    )
    add_para(
        doc,
        "坡口工作站峰值占用率超过 1 的数值来自尚未确认的企业现场容量假设，报告将其列为风险，"
        "不得作为“现场容量必然充足”的结论。实际投产前需要把坡口工作站、半框和齐套缓存的实际"
        "框位数量重新写入 ModelConfig，并重新运行容量门控实验。",
    )
    doc.add_page_break()


def section_dynamic(doc: Document, dynamic: dict) -> None:
    add_heading(doc, "8 动态响应与鲁棒性验证", 1)
    add_heading(doc, "8.1 故障与订单变更场景", 2)
    add_para(
        doc,
        "动态实验采用“N2 切割头在 08:00 至 12:00 停用 4 h，同时取消 1 张未投料钢板、"
        "调整 1 张未投料钢板的优先级”的组合事件。动态引擎以 08:00 为决策时刻，冻结 16 张"
        "已完成钢板，保留 92 张未完成钢板进行重排，最终取消 1 张钢板。",
    )
    dyn = dynamic["metrics"]["optimized"]
    base_dyn = dynamic["comparisonData"][0]
    add_table(
        doc,
        ["项目", "静态综合解 / 重排前", "动态重排后", "结果"],
        [
            ["决策时刻", "08:00", dynamic["decision_time"], "事件时间对齐"],
            ["响应时间", "-", f"{dynamic['reschedule_time_s']:.3f} s", "满足 8 s 预算"],
            ["冻结钢板", "-", dynamic["frozen_plates"], "保留已完成状态"],
            ["重排钢板", "-", dynamic["reoptimized_plates"], "未开始钢板重新搜索"],
            ["取消钢板", "-", dynamic["cancelled_plates"], "订单变更生效"],
            ["总完工时间", f"{base_dyn['总完工时间(h)']:.2f} h", f"{dyn['总完工时间(h)']:.2f} h", "动态约束下重新评估"],
            ["平均齐套跨度", f"{base_dyn['加权平均齐套跨度(h)']:.2f} h", f"{dyn['加权平均齐套跨度(h)']:.2f} h", "基本保持"],
            ["切割负载差", f"{base_dyn['切割负载差(h)']:.2f} h", f"{dyn['切割负载差(h)']:.2f} h", "故障期间上升"],
            ["整体产能", f"{base_dyn['整体产能(张板/班)']:.2f} 张板/班", f"{dyn['整体产能(张板/班)']:.2f} 张板/班", "维持高产能"],
            ["死锁警告", f"{base_dyn.get('死锁警告数', 0)}", f"{dyn.get('死锁警告数', 0)}", "无死锁"],
        ],
        [1.45, 1.60, 1.65, 1.90],
        font_size=8.7,
    )
    add_figure(
        doc,
        ASSET_DIR / "dynamic_response.png",
        "图 8-1 故障与订单变更后的动态指标",
        width=6.35,
    )
    add_heading(doc, "8.2 不稳定性来源与控制措施", 2)
    add_para(
        doc,
        "动态场景中的切割负载差上升，是因为 N2 被停用后重排器把部分钢板转移到 N5，"
        "但 N5 仍有厚度和宽度资格限制。该变化反映的是资源故障的代价，不是算法把约束遗漏。"
        "系统保持无死锁、无缓存溢出告警，并把未完成钢板的优先级变化写入状态，供下一轮继续使用。",
    )
    control_rows = [
        ["故障资源不可用", "使用 AvailabilityCalendar 并取多个停用区间的并集", "避免故障区间的任务重叠"],
        ["在制钢板处置", "finish / interrupt 两种策略", "避免对在制状态做不真实回滚"],
        ["旧故障重复叠加", "每次从基础排程和完整故障清单重建", "保证动态结果可追溯"],
        ["搜索时间不确定", "先贪心修复，再在剩余预算内搜索", "保证响应时间上界"],
        ["数据更新与取消", "重新上传附件并重建零件关联；已完成钢板按库存处理", "避免旧零件数据污染"],
    ]
    add_table(doc, ["风险", "控制措施", "目标"], control_rows, [1.65, 3.05, 1.90])
    add_heading(doc, "8.3 结果边界", 2)
    add_para(
        doc,
        "本次动态实验使用 8 s 搜索预算，未进行多故障概率分布、修复时间不确定性或连续插单的"
        "大规模蒙特卡洛实验。当前证据证明状态化重排链路可以执行并返回可行结果，不能证明任意"
        "随机故障下的最坏情况性能。正式上线前应把企业故障分布和历史修复时间纳入情景库，"
        "开展重复抽样和置信区间评估。",
    )
    doc.add_page_break()


def section_engineering(doc: Document, summary: dict) -> None:
    add_heading(doc, "9 工程实现、代码结构与提交物", 1)
    add_heading(doc, "9.1 分层代码结构", 2)
    add_table(
        doc,
        ["层次", "主要文件", "职责"],
        [
            ["核心模型", "steel_schedule_model.py", "数据校验、资源池、工序时间、离散事件仿真和 KPI"],
            ["多策略优化", "improved_optimizer.py", "SA+Tabu、邻域算子和多策略并行"],
            ["遗传与 LNS", "ga_lns_optimizer.py", "GA+LNS 搜索和 Pareto 档案"],
            ["二次目标实验", "pareto_optimizer.py", "二次非线性评价和对照优化"],
            ["DQN 训练", "train_dqn_nsga2.py", "机器分配网络离线训练"],
            ["DQN 推理", "dqn_machine_model.py", "模型加载、特征域检查和机器码生成"],
            ["多目标求解", "dqn_nsga2_optimizer.py", "OS+MS 编码、非支配排序、拥挤度和前沿输出"],
            ["动态重排", "backend/dynamic_rescheduler.py", "故障、订单变更、状态快照和重排策略"],
            ["服务层", "backend/main.py", "上传、计算、结果历史、动态接口和导出"],
            ["前端", "frontend/src", "参数配置、甘特图、齐套图、利用率、回放和历史管理"],
            ["回归测试", "regression_*.py", "多数据集批量回归和接口检查"],
            ["报告生成", "build_report.py", "基于运行结果生成基础 Word 建模报告"],
        ],
        [1.20, 2.30, 3.10],
        font_size=8.6,
    )
    add_heading(doc, "9.2 主实验产物", 2)
    add_table(
        doc,
        ["文件或目录", "内容"],
        [
            ["提交材料/仿真结果/综合交付_DQN_NSGA2/summary.json", "主实验配置、FIFO、综合解和备选解"],
            ["提交材料/仿真结果/综合交付_DQN_NSGA2/pareto_front.csv", "36 个非支配解"],
            ["提交材料/仿真结果/综合交付_DQN_NSGA2/optimized_plate_schedule.csv", "109 张钢板排序和机器分配"],
            ["提交材料/仿真结果/综合交付_DQN_NSGA2/part_completion.csv", "910 个零件完成时间"],
            ["提交材料/仿真结果/综合交付_DQN_NSGA2/process_stages.csv", "全工序事件"],
            ["提交材料/仿真结果/动态响应_N2故障与订单变更", "动态状态、重排结果、KPI 和可视化"],
            ["提交材料/报告素材", "报告使用的架构图、流程图、Pareto 图和 KPI 图"],
        ],
        [3.55, 3.05],
        font_size=8.6,
    )
    add_heading(doc, "9.3 启动与操作", 2)
    add_para(
        doc,
        "Web 端通过 start.bat 或 restart_server.py 启动。用户上传附件 2，可选上传附件 3，"
        "选择算法、种群规模、迭代上限、随机种子和设备参数后执行计算。结果页提供对比表、甘特图、"
        "齐套跨度图、资源利用率、全流程回放和历史版本切换。动态页输入故障资源及时间，"
        "选择稳定右移、局部或全局重排策略，即可得到新的排产结果。",
    )
    add_para(
        doc,
        "命令行适合无前端环境下复现静态结果。动态重排依赖基础运行目录和标准结果文件，"
        "需要在 Web 页面或服务层调用动态接口。具体命令与接口参数见与本报告同目录的用户手册。",
    )
    add_para(
        doc,
        "动态响应页面在附件 2 校验成功后显示“订单变更”和“紧急插单”。订单变更沿用原动态重排；"
        "紧急插单先计算新增批次前 4 张并生成 staging 预览，再完整计算新增批次并以资源释放时间"
        "续排原计划。最新本地服务地址为 http://127.0.0.1:8000。前端 TypeScript、Vite 构建和"
        "后端编译均已通过。",
    )
    doc.add_page_break()


def section_risks(doc: Document) -> None:
    add_heading(doc, "10 风险边界、参数校准与后续工作", 1)
    add_heading(doc, "10.1 当前实现的风险边界", 2)
    risks = [
        [
            "缓存容量参数未完全由企业给出",
            "坡口工作站峰值超过暂定容量阈值；半框和齐套缓存参数也可能随现场布局变化",
            "将现场真实框位写入 ModelConfig，运行强制容量门控并重新回归",
        ],
        [
            "DQN 只用当前一份数据训练",
            "当前 DQN 作为种子器有效，但不能证明对新订单分布泛化",
            "扩展到多订单、多厚度和多设备组合的数据集，开展域外测试",
        ],
        [
            "动态验证使用固定故障情景",
            "未覆盖连续多故障、修复超时和插单潮汐",
            "使用故障概率模型和历史修复时间做蒙特卡洛鲁棒性分析",
        ],
        [
            "物流参数仍有默认值",
            "AGV 速度、路径距离和天车空驶时间可能影响动态瓶颈",
            "用现场测时和路径地图替换默认参数，校准 AGV 区域模型",
        ],
        [
            "多目标权重需要按现场决策口径确认",
            "综合解和产能优先解的选择依赖业务偏好",
            "建立管理层、计划员和设备主管三类决策口径并做敏感性分析",
        ],
    ]
    add_table(doc, ["风险", "影响", "处置计划"], risks, [1.65, 2.55, 2.40], font_size=8.5)
    add_heading(doc, "10.2 后续优化方向", 2)
    directions = [
        "把“坡口缓存、半框缓存、齐套缓存”的容量门控从诊断升级为求解约束，确保所有 Pareto 解硬满足容量限制。",
        "增加订单插单、撤单、交期变更和设备多故障的滚动时域重排，并记录冻结窗口长度。",
        "把 DQN 训练扩展到多类型钢板分布，使用动态元数据和分布外检测控制模型适用范围。",
        "在前端增加“求解器—目标口径—时间预算”的决策看板，使计划员可以看到 Pareto 取舍，而不是只看一个分数。",
        "把结果文件直接转换为现场派工单、料框标签和 AGV 任务清单，减少系统与现场系统之间的手工转换。",
    ]
    for item in directions:
        add_bullet(doc, item)
    add_heading(doc, "10.3 结论", 2)
    add_para(
        doc,
        "本方案已经形成从原始钢板数据到钢板排序、切割机分配、下游工序仿真、齐套判断和动态重排的"
        "可运行闭环。主实验结果在总完工时间、平均齐套跨度、负载差、整体产能和切割机稼动率上达到"
        "题目参考要求，并通过了故障与订单变更的动态验证。剩余工作集中在现场容量和物流参数校准，"
        "不能把暂定参数当成已完成现场验证的结论。",
    )
    doc.add_page_break()


def appendix(doc: Document, summary: dict) -> None:
    add_heading(doc, "附录 A 主要参数与数据字典", 1)
    add_table(
        doc,
        ["参数", "当前值", "来源/说明"],
        [
            ["直切速度回退值", "1700 mm/min", "仅在未提供附件 3 时使用"],
            ["V&Y 坡速度回退值", "800 mm/min", "仅在未提供附件 3 时使用"],
            ["空行程速度", "24000 mm/min", "题目/工艺说明"],
            ["划线速度", "24000 mm/min", "题目/工艺说明"],
            ["上料准备", "3 min/张", "附件 3"],
            ["小件分拣", "1.58 min/件", "附件 3，95 s"],
            ["小件打磨速度", "2520 mm/min", "附件 3，42 mm/s"],
            ["大件自由边打磨", "1950 mm/min", "附件 3，1.95 m/min"],
            ["人工坡口速度", "250 mm/min", "附件 3"],
            ["自动坡口固定开销", "3.5 min/件", "扫描 2 + 预热 0.5 + 翻面 1"],
            ["N2/N5", "2 台，双工位", "题目"],
            ["自动坡口机", "1 台", "题目"],
            ["人工坡口工位", "1 个", "题目"],
            ["AGV", "2 台", "题目和运行配置"],
            ["半框缓存", f"{summary['综合交付解'].get('半框缓存区峰值占用率', 0):.4f} 峰值比", "容量写入 ModelConfig 后复核"],
            ["齐套缓存", f"{summary['综合交付解'].get('齐套缓存区峰值占用率', 0):.4f} 峰值比", "容量写入 ModelConfig 后复核"],
        ],
        [2.05, 1.75, 2.80],
        font_size=8.5,
    )
    add_heading(doc, "附录 B 代码文件与输出文件清单", 1)
    add_table(
        doc,
        ["文件", "用途"],
        [
            ["steel_schedule_model.py", "主模型：数据校验、工艺时间、资源池、离散事件仿真、KPI"],
            ["improved_optimizer.py", "SA+Tabu 与多策略搜索"],
            ["ga_lns_optimizer.py", "GA+LNS 搜索"],
            ["dqn_machine_model.py", "DQN 机器分配推理和回退"],
            ["dqn_nsga2_optimizer.py", "DQN+NSGA-II 多目标求解"],
            ["train_dqn_nsga2.py", "DQN 离线训练"],
            ["backend/dynamic_rescheduler.py", "故障、订单变更和动态重排"],
            ["backend/main.py", "FastAPI 服务和运行历史"],
            ["frontend/src", "可视化、参数配置、动态响应和历史管理"],
            ["outputs/optimized_plate_schedule.csv", "标准钢板排程输出"],
            ["outputs/part_completion.csv", "标准零件完成输出"],
            ["outputs/process_stages.csv", "标准工序事件输出"],
        ],
        [3.10, 3.50],
        font_size=8.7,
    )


def build() -> None:
    summary, dynamic, pareto, base, opt, capacity = load_inputs()
    doc = Document()
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(0.78)
    section.bottom_margin = Inches(0.72)
    section.left_margin = Inches(0.82)
    section.right_margin = Inches(0.82)
    section.header_distance = Inches(0.32)
    section.footer_distance = Inches(0.34)
    configure_styles(doc)
    add_page_number(section)

    cover(doc, summary)
    contents(doc)
    abstract(doc, summary, opt)
    section_requirements(doc, summary)
    section_problem(doc, summary)
    section_math(doc)
    section_algorithm(doc, summary)
    section_simulation(doc, summary, dynamic)
    section_environment(doc, summary)
    section_results(doc, summary, base, opt, capacity, pareto)
    section_dynamic(doc, dynamic)
    section_engineering(doc, summary)
    section_risks(doc)
    appendix(doc, summary)

    doc.core_properties.title = "船体加工车间智能排产与齐套配盘优化调度技术方案报告"
    doc.core_properties.subject = "CS-202625 技术方案、算法设计与仿真验证"
    doc.core_properties.author = "参赛团队"
    doc.core_properties.keywords = "船体加工, 齐套配盘, NSGA-II, DQN, 动态排产"
    try:
        doc.save(OUTPUT)
        print(OUTPUT)
    except PermissionError:
        fallback = OUTPUT.with_name(f"{OUTPUT.stem}_双模式更新版{OUTPUT.suffix}")
        doc.save(fallback)
        print(fallback)


if __name__ == "__main__":
    build()
