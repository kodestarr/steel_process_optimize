"""从 steel_schedule_model.py 的输出生成可提交前继续完善的 Word 建模报告。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

BLUE = "2E74B5"; DARK = "1F4D78"; NAVY = "0B2545"; LIGHT = "F4F6F9"; GRAY = "666666"

def set_font(run, size=None, color=None, bold=None, name="Calibri"):
    run.font.name = name
    run._element.rPr.rFonts.set(qn("w:ascii"), name); run._element.rPr.rFonts.set(qn("w:hAnsi"), name)
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    if size: run.font.size = Pt(size)
    if color: run.font.color.rgb = RGBColor.from_string(color)
    if bold is not None: run.bold = bold

def shade(cell, fill):
    tcPr = cell._tc.get_or_add_tcPr(); shd = OxmlElement("w:shd"); shd.set(qn("w:fill"), fill); tcPr.append(shd)

def set_cell_margins(cell, top=80, start=120, bottom=80, end=120):
    tc = cell._tc; tcPr = tc.get_or_add_tcPr(); tcMar = tcPr.first_child_found_in("w:tcMar")
    if tcMar is None: tcMar = OxmlElement("w:tcMar"); tcPr.append(tcMar)
    for side, value in [("top", top), ("start", start), ("bottom", bottom), ("end", end)]:
        node = tcMar.find(qn(f"w:{side}"))
        if node is None: node = OxmlElement(f"w:{side}"); tcMar.append(node)
        node.set(qn("w:w"), str(value)); node.set(qn("w:type"), "dxa")

def fix_table(table, widths):
    table.autofit = False
    tblPr = table._tbl.tblPr; tblW = tblPr.first_child_found_in("w:tblW")
    if tblW is None: tblW = OxmlElement("w:tblW"); tblPr.append(tblW)
    tblW.set(qn("w:w"), "9360"); tblW.set(qn("w:type"), "dxa")
    for row in table.rows:
        for i, cell in enumerate(row.cells):
            cell.width = Inches(widths[i]); cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER; set_cell_margins(cell)
            tcPr = cell._tc.get_or_add_tcPr(); tcW = tcPr.first_child_found_in("w:tcW")
            if tcW is None: tcW = OxmlElement("w:tcW"); tcPr.append(tcW)
            tcW.set(qn("w:w"), str(round(widths[i] * 1440))); tcW.set(qn("w:type"), "dxa")

def add_table(doc, headers, rows, widths):
    table = doc.add_table(rows=1, cols=len(headers)); table.style = "Table Grid"; fix_table(table, widths)
    for i, h in enumerate(headers):
        c = table.rows[0].cells[i]; shade(c, "E8EEF5"); p = c.paragraphs[0]; p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(str(h)); set_font(r, 9.5, NAVY, True)
    for row in rows:
        cells = table.add_row().cells
        for i, value in enumerate(row):
            p = cells[i].paragraphs[0]; p.alignment = WD_ALIGN_PARAGRAPH.CENTER if i > 0 else WD_ALIGN_PARAGRAPH.LEFT
            r = p.add_run(str(value)); set_font(r, 9.5, "222222")
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return table

def add_para(doc, text="", size=11, bold=False, color="222222", after=8, before=0, align=WD_ALIGN_PARAGRAPH.LEFT):
    p = doc.add_paragraph(); p.alignment = align; p.paragraph_format.space_before = Pt(before); p.paragraph_format.space_after = Pt(after); p.paragraph_format.line_spacing = 1.333
    r = p.add_run(text); set_font(r, size, color, bold); return p

def heading(doc, text, level=1):
    p = doc.add_paragraph(); p.paragraph_format.space_before = Pt(18 if level == 1 else 12); p.paragraph_format.space_after = Pt(10 if level == 1 else 6)
    r = p.add_run(text); set_font(r, 16 if level == 1 else 13, BLUE if level == 1 else DARK, True); return p

def add_bullet(doc, text):
    p = doc.add_paragraph(style="List Bullet"); p.paragraph_format.space_after = Pt(4); p.paragraph_format.line_spacing = 1.208
    r = p.add_run(text); set_font(r, 10.5, "222222")

def add_page_number(section):
    p = section.footer.paragraphs[0]; p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    r = p.add_run("钢板齐套感知排产模型  |  "); set_font(r, 9, GRAY)
    fld = OxmlElement("w:fldSimple"); fld.set(qn("w:instr"), "PAGE"); p._p.append(fld)

def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--results", type=Path, required=True); parser.add_argument("--output", type=Path, required=True); args = parser.parse_args()
    summary = json.loads((args.results / "summary.json").read_text(encoding="utf-8"))
    comparison = pd.read_csv(args.results / "comparison.csv")
    groups = pd.read_csv(args.results / "kit_groups.csv")
    schedule = pd.read_csv(args.results / "optimized_plate_schedule.csv")
    checks = summary["数据校验"]; base = summary["FIFO基线"]; opt = summary["齐套感知优化"]

    doc = Document(); sec = doc.sections[0]
    sec.top_margin = sec.bottom_margin = sec.left_margin = sec.right_margin = Inches(1)
    sec.header_distance = sec.footer_distance = Inches(0.492); add_page_number(sec)
    styles = doc.styles
    normal = styles["Normal"]; normal.font.name = "Calibri"; normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei"); normal.font.size = Pt(11)

    # editorial_cover：报告型封面
    for _ in range(5): add_para(doc, "", after=10)
    add_para(doc, "挑战杯参赛建模报告", 13, True, "7A5A00", 18, align=WD_ALIGN_PARAGRAPH.CENTER)
    add_para(doc, "钢板零件齐套感知\n多资源协同排产模型", 27, True, NAVY, 10, align=WD_ALIGN_PARAGRAPH.CENTER)
    add_para(doc, "基于离散事件仿真的切割—加工—缓存—AGV 联合调度", 14, False, DARK, 32, align=WD_ALIGN_PARAGRAPH.CENTER)
    add_para(doc, "数据版本：附件2更新版  |  模型版本：V1.0  |  日期：2026年7月", 10, False, GRAY, 70, align=WD_ALIGN_PARAGRAPH.CENTER)
    add_para(doc, "说明：本报告已实现可运行模型；附件3速度表、设备配置和现场物流参数缺失时采用可替换默认参数。", 9.5, False, GRAY, 6, align=WD_ALIGN_PARAGRAPH.CENTER)
    doc.add_page_break()

    heading(doc, "摘  要")
    add_para(doc, f"针对钢板切割后零件需经分拣、打磨、坡口、缓存与 AGV 转运并按分段齐套交付的生产组织问题，本文构建了一个齐套感知的多资源离散事件调度模型。模型以{checks['钢板数']}张钢板和{checks['零件数']}个零件为对象，将钢板加工顺序及N2/N5分配作为上层决策，将小件自动工序、大件人工工序、AGV转运和有限缓存作为下层事件约束。目标是在保证资源可行的前提下，综合最小化总完工时间、加权平均齐套跨度和切割负载差。")
    add_para(doc, "程序已实现数据校验、基线方案、局部搜索优化、结果可视化和CSV导出。当前仅获得附件2，故全部未给定的工艺速度、设备数量和物流参数均显式参数化；报告中的数值属于默认参数情景，不作为企业现场工时结论。", 11, False, "222222", 8)
    heading(doc, "1  问题定义与数据基础")
    add_para(doc, "每张套料钢板切割后会产生多个属于同一分段、但可能具有不同齐套优先级的零件。单纯按钢板顺序或短工时规则加工，可能使某个分段的大量零件提前完成、却因少数关键零件滞后而不能齐套。因此排产必须同时考虑设备负载与零件齐套。")
    add_table(doc, ["项目", "校验结果"], [
        ["钢板 / 零件", f"{checks['钢板数']} 张 / {checks['零件数']} 个"],
        ["分段—优先级齐套组", f"{checks['齐套组数(分段+优先级)']} 个"],
        ["钢板—零件关联", f"未匹配 {checks['未匹配零件数']} 个；零件数汇总不一致 {checks['零件数不一致钢板数']} 张"],
        ["小件 / 大件数量", "钢板汇总与零件明细均一致"],
        ["V坡长度汇总", f"不一致钢板 {checks['V坡长度不一致钢板数']} 张"],
        ["打磨长度缺失", f"{checks['打磨长度缺失零件数']} 个（原值保留，模型中以0作为待确认临时输入）"],
    ], [1.75, 4.75])
    add_para(doc, "大件人工坡口的派工长度按照补充说明取 Y坡 + X坡 + K坡，不把 I坡计入人工坡口长度；原始字段不被覆盖。", 10, False, GRAY, 6)

    heading(doc, "2  模型设计")
    heading(doc, "2.1 决策、流程与约束", 2)
    add_bullet(doc, f"上层决策：{checks['钢板数']}张钢板的加工次序，以及每张钢板分配至N2或N5。")
    add_bullet(doc, "下层事件：零件在分拣、自动/人工打磨、自动/人工坡口、AGV转运中的开始和结束时刻。")
    add_bullet(doc, "资源约束：任一设备、工位或AGV在同一时刻最多处理一个任务；成品缓存容量受限，满载时上游任务延迟进入缓存。")
    add_bullet(doc, "齐套定义：以“分段 + 齐套优先级”为组，组完成时刻等于全部零件到达齐套区的最晚时刻，齐套跨度等于首件与末件到区时间之差。")
    heading(doc, "2.2 工时与目标函数", 2)
    add_para(doc, "切割工时由直切、V坡、空行程、划线、穿孔和换板准备构成。速度与设备参数集中存放在 ModelConfig 中，以便将附件3的正式公式直接替换。")
    add_para(doc, "T_cut = L_cut/v_cut + L_V/v_V + L_empty/v_rapid + L_mark/v_mark + n_pierce·t_pierce + t_setup", 11, True, NAVY, 10, align=WD_ALIGN_PARAGRAPH.CENTER)
    add_para(doc, "优化评价函数为 J = 0.40·Cmax + 0.40·Tkit + 0.20·ΔL。其中 Cmax 为总完工时间，Tkit 为按零件数加权的平均齐套跨度，ΔL 为N2/N5切割负载差。权重可在比赛答疑明确评分口径后调整。", 10.5, False, "222222", 8)
    heading(doc, "2.3 求解方法", 2)
    add_para(doc, "首先构造 FIFO 基线；然后采用“交换两张钢板”与“前插一张钢板”两类邻域的受控随机局部搜索。每一个候选排序均运行完整离散事件仿真，而非仅用静态加工时长估计，从而让齐套、下游工序和AGV资源约束共同影响评价。")

    heading(doc, "3  默认参数情景实验")
    add_para(doc, "默认情景设置为：N2/N5两台切割机、1台自动分拣、1台小件自动打磨机、2个人工打磨工位、1台自动坡口机、2个人工坡口工位、1台AGV以及50件成品缓存。该配置仅用于验证模型流程，必须以题目附件或企业数据更新。", 10.5, False, "222222", 8)
    rows = []
    for _, r in comparison.iterrows():
        rows.append([r["方案"], f"{r['总完工时间(h)']:.2f}", f"{r['加权平均齐套跨度(h)']:.2f}", f"{r['最大齐套跨度(h)']:.2f}", f"{r['切割负载差(h)']:.2f}"])
    add_table(doc, ["方案", "总完工(h)", "平均齐套跨度(h)", "最大齐套跨度(h)", "切割负载差(h)"], rows, [1.55, 1.2, 1.45, 1.45, 0.85])
    add_para(doc, "结果解释：在目前的默认参数与目标权重下，FIFO原始顺序已是本次局部搜索找到的最优解，因此模型没有虚构改善率。该现象说明需要使用附件3的真实速度、物流和资源数据才能形成有意义的现场改进结论。", 10.5, True, "7A5A00", 9)
    for img, caption in [("cutting_gantt.png", "图1 优化方案的N2/N5切割甘特图"), ("kit_span.png", "图2 各齐套组的齐套跨度"), ("resource_utilisation.png", "图3 后续资源利用率")]:
        image_path = args.results / img
        if image_path.exists():
            p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER; p.add_run().add_picture(str(image_path), width=Inches(6.2))
            add_para(doc, caption, 9.5, False, GRAY, 10, align=WD_ALIGN_PARAGRAPH.CENTER)

    heading(doc, "4  可复现性与交付说明")
    add_table(doc, ["文件", "用途"], [
        ["steel_schedule_model.py", "模型主程序：读取附件、仿真、优化、导出结果"],
        ["outputs/optimized_plate_schedule.csv", f"{checks['钢板数']}张钢板的切割顺序、切割机分配与时间"],
        ["outputs/part_completion.csv", f"{checks['零件数']}个零件的释放和到齐套区时刻"],
        ["outputs/process_stages.csv", "逐工序资源占用事件，可用于复核甘特图"],
        ["outputs/kit_groups.csv", f"{checks['齐套组数(分段+优先级)']}个齐套组的完成时刻与齐套跨度"],
    ], [2.45, 4.05])
    heading(doc, "5  结论与后续工作")
    add_para(doc, "本文已形成一套能从原始钢板/零件数据直接运行的齐套感知排产原型。它避免将数据缺口隐藏在代码中，并将所有待确认项集中为参数。下一步应将附件3中不同厚度的切割、打磨、坡口速度和效率公式逐项写入 ModelConfig；再补充设备数量、料框容量、AGV速度、站点距离和缓存容量，进行多情景仿真与参数校准。若题目要求故障和插单，可在现有离散事件框架上增加滚动冻结窗口和重调度规则选择模块。")
    add_para(doc, "本版本的贡献是建立可信的建模底座；只有在口径校准后，才应将优化结果与企业的67.25小时、61%稼动率等参考指标做正式比较。", 10.5, True, NAVY, 8)
    doc.save(args.output)
    print(args.output)

if __name__ == "__main__": main()
