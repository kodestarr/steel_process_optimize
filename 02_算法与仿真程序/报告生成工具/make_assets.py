"""Generate report diagrams from the verified simulation outputs."""

from __future__ import annotations

import json
import math
import sys
import uuid
from pathlib import Path

import pandas as pd
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from project_paths import EVIDENCE_DIR, create_run_directory, ensure_output_dirs, run_result_dir


ROOT = Path(__file__).resolve().parents[2]
SUBMISSION = EVIDENCE_DIR
MAIN_DIR = SUBMISSION / "仿真结果" / "综合交付_DQN_NSGA2"
DYNAMIC_DIR = (
    SUBMISSION
    / "仿真结果"
    / "动态响应_N2故障与订单变更"
    / "current"
)
ASSET_DIR: Path | None = None
FONT_DIR: Path | None = None

COLORS = {
    "ink": "#172033",
    "muted": "#5F6B7A",
    "line": "#CBD3DF",
    "paper": "#FFFFFF",
    "blue": "#245A8D",
    "blue_light": "#DCEAF7",
    "teal": "#167D7F",
    "teal_light": "#DCEFEF",
    "gold": "#A66A00",
    "gold_light": "#F7EACD",
    "red": "#A63D40",
    "red_light": "#F4DEDE",
    "green": "#2E6B49",
    "green_light": "#DDEDE3",
    "gray_light": "#EEF1F5",
}


def font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    if FONT_DIR is None:
        return ImageFont.load_default()
    path = FONT_DIR / ("msyhbd.ttc" if bold else "msyh.ttc")
    if not path.exists():
        return ImageFont.load_default()
    return ImageFont.truetype(str(path), size=size)


def text_size(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.ImageFont):
    box = draw.textbbox((0, 0), text, font=fnt)
    return box[2] - box[0], box[3] - box[1]


def draw_center(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    fnt: ImageFont.FreeTypeFont,
    fill: str = COLORS["ink"],
    line_gap: int = 8,
) -> None:
    lines = text.split("\n")
    heights = [text_size(draw, line, fnt)[1] for line in lines]
    total_h = sum(heights) + line_gap * max(0, len(lines) - 1)
    y = box[1] + (box[3] - box[1] - total_h) / 2
    for line, h in zip(lines, heights):
        w, _ = text_size(draw, line, fnt)
        draw.text(
            (box[0] + (box[2] - box[0] - w) / 2, y),
            line,
            font=fnt,
            fill=fill,
        )
        y += h + line_gap


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    fnt: ImageFont.FreeTypeFont,
    max_width: int,
    fill: str = COLORS["ink"],
    line_gap: int = 8,
) -> int:
    words = list(text)
    lines: list[str] = []
    line = ""
    for char in words:
        candidate = line + char
        if line and text_size(draw, candidate, fnt)[0] > max_width:
            lines.append(line)
            line = char
        else:
            line = candidate
    if line:
        lines.append(line)
    y = xy[1]
    for item in lines:
        draw.text((xy[0], y), item, font=fnt, fill=fill)
        y += text_size(draw, item, fnt)[1] + line_gap
    return y


def arrow(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    color: str = COLORS["blue"],
    width: int = 6,
    head: int = 16,
) -> None:
    draw.line((start, end), fill=color, width=width)
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    for delta in (2.55, -2.55):
        p = (
            end[0] + head * math.cos(angle + delta),
            end[1] + head * math.sin(angle + delta),
        )
        draw.line((end, p), fill=color, width=width)


def node(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    title: str,
    detail: str = "",
    fill: str = COLORS["paper"],
    outline: str = COLORS["line"],
) -> None:
    draw.rounded_rectangle(
        box,
        radius=16,
        fill=fill,
        outline=outline,
        width=3,
    )
    if detail:
        draw.text(
            (box[0] + 24, box[1] + 18),
            title,
            font=font(34, True),
            fill=COLORS["ink"],
        )
        draw_wrapped(
            draw,
            (box[0] + 24, box[1] + 72),
            detail,
            font(25),
            max_width=box[2] - box[0] - 48,
            fill=COLORS["muted"],
            line_gap=6,
        )
    else:
        draw_center(draw, box, title, font(30, True))


def architecture() -> None:
    image = Image.new("RGB", (1900, 1350), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    draw.text((70, 48), "船体加工车间智能排产系统总体架构", font=font(48, True), fill=COLORS["ink"])
    draw.text(
        (72, 112),
        "五层架构共享数据、仿真和 KPI 口径，避免不同算法产生不可比较的结果",
        font=font(27),
        fill=COLORS["muted"],
    )

    bands = [
        (
            "输入层",
            COLORS["blue_light"],
            [
                ("附件2 钢板零件数据", "钢板、零件、优先级和工艺长度校验"),
                ("附件3 工艺参数", "厚度速度表与线性插值"),
                ("运行状态", "设备、缓存、随机种子和时间预算"),
            ],
        ),
        (
            "仿真层",
            COLORS["teal_light"],
            [
                ("数据加载与校验", "异常数据拒绝、钢板零件关联、一致性检查"),
                ("全流程离散事件仿真", "切割、分拣、打磨、坡口、AGV、缓存和齐套"),
                ("KPI 与约束诊断", "Cmax、齐套、负载、等待、利用率与死锁"),
            ],
        ),
        (
            "优化层",
            COLORS["gold_light"],
            [
                ("DQN 机器分配", "16维状态、5维动作、特征域外自动回退"),
                ("NSGA-II Pareto", "OS+MS编码、非支配排序、拥挤度与概率精英"),
                ("SA+Tabu / GA+LNS / DRL", "多种求解器共享同一仿真评估器"),
            ],
        ),
        (
            "动态层",
            COLORS["red_light"],
            [
                ("故障日历", "切割头、胎架、打磨、坡口、AGV和天车"),
                ("状态快照", "已完成、在制、未开始、取消与清台"),
                ("双模式重排", "订单变更与紧急插单，多策略选择"),
            ],
        ),
        (
            "交付层",
            COLORS["green_light"],
            [
                ("FastAPI 服务", "上传、计算、取消、动态重排和结果版本"),
                ("Web 可视化", "甘特、齐套、利用率、回放和历史对比"),
                ("提交结果", "CSV、JSON、PNG、summary 与 Word 报告"),
            ],
        ),
    ]

    y = 205
    band_h = 190
    for band_name, band_color, items in bands:
        draw.rounded_rectangle(
            (70, y, 1830, y + band_h),
            radius=20,
            fill=band_color,
            outline=COLORS["line"],
            width=3,
        )
        draw_center(draw, (90, y + 20, 310, y + band_h - 20), band_name, font(34, True))
        x = 340
        card_w = 470
        for title, detail in items:
            node(
                draw,
                (x, y + 28, x + card_w, y + band_h - 28),
                title,
                detail,
                fill="#FFFFFF",
            )
            x += card_w + 30
        y += band_h + 28

    image.save(ASSET_DIR / "overall_architecture.png", quality=95)


def process_flow() -> None:
    image = Image.new("RGB", (1900, 1280), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    draw.text((70, 48), "钢板与零件全流程约束链", font=font(48, True), fill=COLORS["ink"])
    draw.text(
        (72, 112),
        "主切割流程统一，小件和大件分成左右两条独立链路，分段齐套只属于小件",
        font=font(28),
        fill=COLORS["muted"],
    )

    top_y = 220
    top_h = 150
    top_boxes = [
        (70, top_y, 430, top_y + top_h, "原料钢板", "人工天车\n3 min/张"),
        (520, top_y, 920, top_y + top_h, "N2 / N5", "双工位切割\n厚度速度表"),
        (1010, top_y, 1410, top_y + top_h, "切割完成", "直切 + V坡 + 空行程\n划线 + 穿孔"),
        (1500, top_y, 1830, top_y + top_h, "零件分流", "按小件和大件\n进入两条独立链路"),
    ]
    for box in top_boxes:
        node(draw, box[:4], box[4], box[5], fill=COLORS["blue_light"])
    for a, b in zip(top_boxes, top_boxes[1:]):
        arrow(draw, (a[2], top_y + top_h // 2), (b[0], top_y + top_h // 2), width=5, head=13)

    lane_y = 470
    box_h = 145
    gap = 55
    small_boxes = [
        (130, lane_y, 860, lane_y + box_h, "小件链路：分拣与打磨", "桁架分侧；2L/v + 视觉扫描"),
        (130, lane_y + box_h + gap, 860, lane_y + 2 * box_h + gap, "自动坡口与料框码垛", "Y/v + (X+K)/v + 3.5 min；坡口件分框"),
        (130, lane_y + 2 * (box_h + gap), 860, lane_y + 3 * box_h + 2 * gap, "AGV 转运至半框缓存", "N2/N5 料框按分段和优先级转运"),
        (130, lane_y + 3 * (box_h + gap), 860, lane_y + 4 * box_h + 3 * gap, "小件分段齐套", "同分段全部小件到位后进入小部材放置区"),
    ]
    large_boxes = [
        (1040, lane_y, 1770, lane_y + box_h, "大件链路：胎架自由边打磨", "L / 1950 mm/min"),
        (1040, lane_y + box_h + gap, 1770, lane_y + 2 * box_h + gap, "天车成组转运", "胎架 -> 人工坡口区"),
        (1040, lane_y + 2 * (box_h + gap), 1770, lane_y + 3 * box_h + 2 * gap, "人工坡口", "Y+X+K / 250 mm/min"),
        (1040, lane_y + 3 * (box_h + gap), 1770, lane_y + 4 * box_h + 3 * gap, "成品放置区", "大件不进入小件分段齐套链路"),
    ]
    for box in small_boxes:
        node(draw, box[:4], box[4], box[5], fill=COLORS["teal_light"])
    for box in large_boxes:
        node(draw, box[:4], box[4], box[5], fill=COLORS["gold_light"])

    for a, b in zip(small_boxes, small_boxes[1:]):
        arrow(draw, (495, a[3]), (495, b[1]), color=COLORS["teal"], width=5, head=13)
    for a, b in zip(large_boxes, large_boxes[1:]):
        arrow(draw, (1405, a[3]), (1405, b[1]), color=COLORS["gold"], width=5, head=13)

    split_x = (top_boxes[-1][0] + top_boxes[-1][2]) // 2
    split_y = top_y + top_h
    branch_y = split_y + 42
    draw.line((split_x, split_y, split_x, branch_y), fill=COLORS["ink"], width=5)
    draw.line((495, branch_y, 1405, branch_y), fill=COLORS["ink"], width=5)
    arrow(draw, (495, branch_y), (495, small_boxes[0][1]), color=COLORS["teal"], width=5, head=13)
    arrow(draw, (1405, branch_y), (1405, large_boxes[0][1]), color=COLORS["gold"], width=5, head=13)

    image.save(ASSET_DIR / "process_flow.png", quality=95)


def pareto_front() -> None:
    data = pd.read_csv(MAIN_DIR / "pareto_front.csv")
    w, h = 1800, 1080
    image = Image.new("RGB", (w, h), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    draw.text((80, 48), "DQN+NSGA-II 多目标 Pareto 前沿", font=font(48, True), fill=COLORS["ink"])
    draw.text(
        (82, 112),
        "横轴为相对理论下界的产能比，纵轴为相对 FIFO 的齐套跨度比；颜色表示负载差倍数",
        font=font(26),
        fill=COLORS["muted"],
    )

    left, top, right, bottom = 160, 210, 1650, 930
    draw.rectangle((left, top, right, bottom), outline=COLORS["line"], width=4)
    for i in range(6):
        x = left + (right - left) * i / 5
        y = bottom - (bottom - top) * i / 5
        draw.line((x, top, x, bottom), fill="#E8EDF3", width=2)
        draw.line((left, y, right, y), fill="#E8EDF3", width=2)

    xmin = max(1.12, float(data["F1_Cmax相对LB"].min()) - 0.01)
    xmax = float(data["F1_Cmax相对LB"].max()) + 0.01
    ymin = max(0.60, float(data["F2_齐套相对FIFO"].min()) - 0.05)
    ymax = float(data["F2_齐套相对FIFO"].max()) + 0.05

    def px(x):
        return left + (float(x) - xmin) / (xmax - xmin) * (right - left)

    def py(y):
        return bottom - (float(y) - ymin) / (ymax - ymin) * (bottom - top)

    for _, row in data.iterrows():
        x = px(row["F1_Cmax相对LB"])
        y = py(row["F2_齐套相对FIFO"])
        ratio = float(row["F3_负载差相对FIFO"])
        radius = 14 + min(10, math.log1p(max(0.0, ratio)) * 7)
        if ratio <= 2.0:
            fill = COLORS["green"]
        elif ratio <= 6.0:
            fill = COLORS["gold"]
        else:
            fill = COLORS["red"]
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill, outline=COLORS["paper"], width=2)

    balanced = data.iloc[data["加权平均齐套跨度(h)"].idxmin()]
    capacity = data.iloc[data["总完工时间(h)"].idxmin()]

    bx, by = px(balanced["F1_Cmax相对LB"]), py(balanced["F2_齐套相对FIFO"])
    cx, cy = px(capacity["F1_Cmax相对LB"]), py(capacity["F2_齐套相对FIFO"])
    draw.polygon(
        [
            (bx, by - 22),
            (bx + 22, by),
            (bx, by + 22),
            (bx - 22, by),
        ],
        fill=COLORS["red"],
        outline=COLORS["paper"],
    )
    draw.ellipse((cx - 18, cy - 18, cx + 18, cy + 18), fill=COLORS["blue"], outline=COLORS["paper"], width=3)

    draw.text((bx + 30, by - 38), "综合交付解", font=font(30, True), fill=COLORS["red"])
    draw.text((cx + 30, cy - 38), "产能优先备选", font=font(30, True), fill=COLORS["blue"])

    ideal_x, ideal_y = px(1.0), py(1.0)
    if left <= ideal_x <= right and top <= ideal_y <= bottom:
        draw.line((ideal_x - 12, ideal_y, ideal_x + 12, ideal_y), fill=COLORS["ink"], width=4)
        draw.line((ideal_x, ideal_y - 12, ideal_x, ideal_y + 12), fill=COLORS["ink"], width=4)
        draw.text((ideal_x + 16, ideal_y - 44), "理论参考点 (1, 1)", font=font(24, True), fill=COLORS["ink"])

    fnt_axis = font(25)
    for i in range(6):
        x_val = xmin + (xmax - xmin) * i / 5
        y_val = ymin + (ymax - ymin) * i / 5
        pxv, pyv = px(x_val), py(y_val)
        draw.text((pxv - 38, bottom + 18), f"{x_val:.2f}", font=fnt_axis, fill=COLORS["muted"])
        draw.text((left - 100, pyv - 14), f"{y_val:.2f}", font=fnt_axis, fill=COLORS["muted"])

    draw.text((780, 990), "Cmax / 理论下界（越低越好）", font=font(28, True), fill=COLORS["ink"])
    draw.text((28, 550), "齐套跨度 / FIFO", font=font(28, True), fill=COLORS["ink"])

    legend_y = 1020
    draw.ellipse((1140, legend_y, 1165, legend_y + 25), fill=COLORS["green"])
    draw.text((1175, legend_y - 5), "负载差 ≤ 2×FIFO", font=font(23), fill=COLORS["muted"])
    draw.ellipse((1410, legend_y, 1435, legend_y + 25), fill=COLORS["gold"])
    draw.text((1445, legend_y - 5), "2×至 6×FIFO", font=font(23), fill=COLORS["muted"])

    image.save(ASSET_DIR / "pareto_front.png", quality=95)


def comparison_plot() -> None:
    summary = json.loads((MAIN_DIR / "summary.json").read_text(encoding="utf-8"))
    base = summary["FIFO基线"]
    opt = summary["综合交付解"]
    metrics = [
        ("总完工时间", "h", base["总完工时间(h)"], opt["总完工时间(h)"], 67.25, True),
        ("平均齐套跨度", "h", base["加权平均齐套跨度(h)"], opt["加权平均齐套跨度(h)"], 12.7, True),
        ("最大齐套跨度", "h", base["最大齐套跨度(h)"], opt["最大齐套跨度(h)"], None, True),
        ("整体产能", "张板/班", base["整体产能(张板/班)"], opt["整体产能(张板/班)"], 13.0, False),
        ("综合稼动率", "%", base["切割机综合稼动率"] * 100, opt["切割机综合稼动率"] * 100, 61.0, False),
        ("切割负载差", "h", base["切割负载差(h)"], opt["切割负载差(h)"], None, True),
    ]
    image = Image.new("RGB", (1800, 1180), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    draw.text((70, 44), "综合交付解与 FIFO 的关键指标对比", font=font(46, True), fill=COLORS["ink"])
    draw.text(
        (72, 106),
        "蓝色为 FIFO，绿色为综合交付解；橙色虚线表示题目参考值",
        font=font(27),
        fill=COLORS["muted"],
    )

    tile_w, tile_h = 540, 300
    gaps = (30, 30)
    x0, y0 = 70, 180
    for i, (title, unit, bval, oval, target, lower_better) in enumerate(metrics):
        row, col = divmod(i, 3)
        x = x0 + col * (tile_w + gaps[0])
        y = y0 + row * (tile_h + gaps[1])
        draw.rounded_rectangle(
            (x, y, x + tile_w, y + tile_h),
            radius=18,
            fill="#F8FAFC",
            outline=COLORS["line"],
            width=3,
        )
        draw.text((x + 26, y + 22), title, font=font(30, True), fill=COLORS["ink"])

        chart_top = y + 84
        chart_bottom = y + 228
        chart_left = x + 110
        chart_right = x + tile_w - 35
        max_val = max(bval, oval, target or 0)
        max_scale = max_val * 1.15 if max_val else 1
        bar_w = 80
        bx = chart_left
        ox = chart_left + 145
        bh = (bval / max_scale) * (chart_bottom - chart_top)
        oh = (oval / max_scale) * (chart_bottom - chart_top)
        draw.rectangle(
            (bx, chart_bottom - bh, bx + bar_w, chart_bottom),
            fill=COLORS["blue_light"],
            outline=COLORS["blue"],
            width=3,
        )
        draw.rectangle(
            (ox, chart_bottom - oh, ox + bar_w, chart_bottom),
            fill=COLORS["green_light"],
            outline=COLORS["green"],
            width=3,
        )
        draw.text((bx - 5, chart_bottom - bh - 38), f"{bval:.2f}", font=font(24, True), fill=COLORS["blue"])
        draw.text((ox - 5, chart_bottom - oh - 38), f"{oval:.2f}", font=font(24, True), fill=COLORS["green"])
        draw.line((chart_left, chart_bottom, chart_right, chart_bottom), fill=COLORS["line"], width=3)

        if target is not None:
            ty = chart_bottom - (target / max_scale) * (chart_bottom - chart_top)
            for dash_x in range(chart_left, chart_right, 18):
                draw.line((dash_x, ty, min(dash_x + 10, chart_right), ty), fill=COLORS["gold"], width=4)

        direction = "越低越好" if lower_better else "越高越好"
        draw.text(
            (x + 240, y + 244),
            f"单位：{unit}  |  {direction}",
            font=font(22),
            fill=COLORS["muted"],
        )

    draw.text((72, 1110), "FIFO", font=font(24, True), fill=COLORS["blue"])
    draw.text((145, 1110), "综合交付解", font=font(24, True), fill=COLORS["green"])
    draw.text((320, 1110), "题目参考值", font=font(24, True), fill=COLORS["gold"])
    image.save(ASSET_DIR / "kpi_comparison.png", quality=95)


def dynamic_plot() -> None:
    response = json.loads((DYNAMIC_DIR / "dynamic_response.json").read_text(encoding="utf-8"))
    base = response["comparisonData"][0]
    dyn = response["comparisonData"][1]
    image = Image.new("RGB", (1800, 980), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    draw.text((70, 44), "故障与订单变更下的动态响应结果", font=font(46, True), fill=COLORS["ink"])
    draw.text(
        (72, 106),
        "N2 切割头 08:00 至 12:00 停用；取消 1 张未投料钢板，调整 1 张钢板优先级",
        font=font(26),
        fill=COLORS["muted"],
    )

    items = [
        ("总完工时间", "h", base["总完工时间(h)"], dyn["总完工时间(h)"], 67.25, True),
        ("平均齐套跨度", "h", base["加权平均齐套跨度(h)"], dyn["加权平均齐套跨度(h)"], 12.7, True),
        ("整体产能", "张板/班", base["整体产能(张板/班)"], dyn["整体产能(张板/班)"], 13.0, False),
        ("切割负载差", "h", base["切割负载差(h)"], dyn["切割负载差(h)"], None, True),
    ]

    for i, (title, unit, bval, dval, target, lower) in enumerate(items):
        x = 70 + i * 430
        y = 220
        draw.rounded_rectangle(
            (x, y, x + 390, y + 470),
            radius=18,
            fill="#F8FAFC",
            outline=COLORS["line"],
            width=3,
        )
        draw.text((x + 28, y + 26), title, font=font(31, True), fill=COLORS["ink"])
        chart_top = y + 140
        chart_bottom = y + 370
        chart_left = x + 80
        max_val = max(bval, dval, target or 0) * 1.2
        bar_w = 82
        bh = bval / max_val * (chart_bottom - chart_top)
        dh = dval / max_val * (chart_bottom - chart_top)
        draw.rectangle(
            (chart_left, chart_bottom - bh, chart_left + bar_w, chart_bottom),
            fill=COLORS["blue_light"],
            outline=COLORS["blue"],
            width=3,
        )
        draw.rectangle(
            (chart_left + 135, chart_bottom - dh, chart_left + 135 + bar_w, chart_bottom),
            fill=COLORS["red_light"],
            outline=COLORS["red"],
            width=3,
        )
        draw.text((chart_left - 5, chart_bottom - bh - 40), f"{bval:.2f}", font=font(25, True), fill=COLORS["blue"])
        draw.text((chart_left + 130, chart_bottom - dh - 40), f"{dval:.2f}", font=font(25, True), fill=COLORS["red"])
        if target is not None:
            ty = chart_bottom - target / max_val * (chart_bottom - chart_top)
            for dash_x in range(chart_left, x + 350, 16):
                draw.line((dash_x, ty, min(dash_x + 9, x + 350), ty), fill=COLORS["gold"], width=4)
        draw.text((x + 28, y + 405), f"单位：{unit}", font=font(22), fill=COLORS["muted"])

    draw.text((70, 760), f"重排响应时间：{response['reschedule_time_s']:.2f} s", font=font(30, True), fill=COLORS["green"])
    draw.text((500, 760), f"冻结钢板：{response['frozen_plates']} 张", font=font(30), fill=COLORS["ink"])
    draw.text((880, 760), f"重排钢板：{response['reoptimized_plates']} 张", font=font(30), fill=COLORS["ink"])
    draw.text((1260, 760), f"取消钢板：{response['cancelled_plates']} 张", font=font(30), fill=COLORS["ink"])
    draw.text(
        (70, 830),
        "说明：故障期间负载差上升是故障资源受限后的代价；系统保持无死锁并优先恢复齐套与产出。",
        font=font(24),
        fill=COLORS["muted"],
    )
    image.save(ASSET_DIR / "dynamic_response.png", quality=95)


def algorithm_family_map() -> None:
    image = Image.new("RGB", (1900, 1220), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    draw.text((70, 44), "多算法求解器族与适用边界", font=font(46, True), fill=COLORS["ink"])
    draw.text(
        (72, 106),
        "所有算法共享同一套离散事件仿真和 KPI 口径，差异在搜索机制、输出形态和响应预算",
        font=font(26),
        fill=COLORS["muted"],
    )

    cards = [
        (
            "构造式规则",
            "FIFO、分段优先、短工时和天车感知。",
            "输出单个稳定基线解，计算量最低。",
            "适合现场应急、接口验证和快速基线。",
            COLORS["blue_light"],
            COLORS["blue"],
        ),
        (
            "SA+Tabu",
            "交换、插入、逆序、同分段扰动、Tabu 和回火。",
            "输出单目标解或小型 Pareto 档案。",
            "适合中等规模、算力有限、需要快速改善。",
            COLORS["teal_light"],
            COLORS["teal"],
        ),
        (
            "GA+LNS",
            "锦标赛、OX/PMX 交叉、变异和破坏—修复。",
            "输出单目标解或双目标档案。",
            "适合分段聚集明显、需要种群多样性。",
            COLORS["gold_light"],
            COLORS["gold"],
        ),
        (
            "DQN+NSGA-II",
            "DQN 机器码种子、非支配排序、拥挤度和概率精英。",
            "输出四目标 Pareto 前沿和两条交付解。",
            "适合产能、齐套、负载与等待同时权衡。",
            COLORS["red_light"],
            COLORS["red"],
        ),
        (
            "DRL-guided SA",
            "Q-learning 选择邻域算子，动态切换三组目标权重。",
            "输出自适应单目标解。",
            "适合数据有限、不同阶段需要不同策略。",
            COLORS["green_light"],
            COLORS["green"],
        ),
        (
            "动态修复算法",
            "稳定右移、局部窗口重优化、全局重优化和剩余模型续排。",
            "输出故障或订单变化后的恢复排程。",
            "适合故障、撤单、插单和多事件并发。",
            COLORS["gray_light"],
            COLORS["ink"],
        ),
    ]

    x0, y0 = 65, 185
    card_w, card_h = 570, 430
    gap_x, gap_y = 30, 35
    for idx, (title, mechanism, output, usage, fill, outline) in enumerate(cards):
        row, col = divmod(idx, 3)
        x = x0 + col * (card_w + gap_x)
        y = y0 + row * (card_h + gap_y)
        draw.rounded_rectangle(
            (x, y, x + card_w, y + card_h),
            radius=18,
            fill=fill,
            outline=outline,
            width=3,
        )
        draw.text((x + 26, y + 24), title, font=font(32, True), fill=outline)
        draw.text((x + 26, y + 86), "搜索机制", font=font(24, True), fill=COLORS["ink"])
        draw_wrapped(
            draw,
            (x + 26, y + 125),
            mechanism,
            font(26),
            max_width=card_w - 52,
            fill=COLORS["muted"],
            line_gap=8,
        )
        draw.text((x + 26, y + 240), "输出与适用场景", font=font(24, True), fill=COLORS["ink"])
        draw_wrapped(
            draw,
            (x + 26, y + 280),
            f"{output}\n{usage}",
            font(25),
            max_width=card_w - 52,
            fill=COLORS["muted"],
            line_gap=8,
        )

    image.save(ASSET_DIR / "algorithm_family_map.png", quality=95)


def evaluation_function_map() -> None:
    image = Image.new("RGB", (1900, 1240), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    draw.text((70, 44), "评价函数族与选择路径", font=font(46, True), fill=COLORS["ink"])
    draw.text(
        (72, 106),
        "原始 KPI 先进入归一化层，再按决策需求选择标量、二次惩罚、Pareto 向量或动态加权",
        font=font(26),
        fill=COLORS["muted"],
    )

    raw = [
        ("Cmax", "总完工时间"),
        ("Kavg", "平均齐套跨度"),
        ("ΔL", "切割负载差"),
        ("W", "等待时间"),
        ("Idle", "胎架空闲"),
    ]
    for i, (symbol, label) in enumerate(raw):
        x = 85 + i * 250
        node(draw, (x, 190, x + 205, 315), symbol, label, fill=COLORS["blue_light"])
        arrow(draw, (x + 102, 322), (x + 102, 392), color=COLORS["blue"], width=4, head=12)

    draw.rounded_rectangle(
        (90, 400, 1810, 515),
        radius=16,
        fill=COLORS["gray_light"],
        outline=COLORS["line"],
        width=3,
    )
    draw_center(
        draw,
        (100, 410, 1800, 505),
        "归一化层：目标参考值、理论下界 LB、FIFO 相对比、容量/优先级软惩罚",
        font(29, True),
    )

    branches = [
        (
            80,
            590,
            "产能优先标量",
            "J = C / LB + ε × S",
            "交期优先，允许在1%产能带内微调齐套与负载",
            COLORS["blue_light"],
            COLORS["blue"],
        ),
        (
            500,
            590,
            "三指标平衡",
            "J = w1 × Cnorm +\nw2 × Knorm + w3 × Lnorm",
            "计划员常用，选项直观，适合单解交付",
            COLORS["teal_light"],
            COLORS["teal"],
        ),
        (
            920,
            590,
            "二次惩罚",
            "J = w1 × c² + w2 × k² +\nw3 × l² + penalty",
            "超出目标或 FIFO 的代价显著放大，适合约束敏感场景",
            COLORS["gold_light"],
            COLORS["gold"],
        ),
        (
            1340,
            590,
            "Pareto 向量",
            "F = [C / LB, K / K0,\nΔL / ΔL0, W / W0]",
            "不预先压权重，输出完整前沿，适合多部门协商",
            COLORS["red_light"],
            COLORS["red"],
        ),
    ]

    for x, y, title, formula, detail, fill, outline in branches:
        draw.rounded_rectangle(
            (x, y, x + 440, y + 400),
            radius=18,
            fill=fill,
            outline=outline,
            width=3,
        )
        draw_center(draw, (x + 15, y + 20, x + 425, y + 80), title, font(30, True))
        draw_center(draw, (x + 15, y + 95, x + 425, y + 195), formula, font(25, True), fill=outline, line_gap=10)
        draw_wrapped(
            draw,
            (x + 28, y + 225),
            detail,
            font(24),
            max_width=384,
            fill=COLORS["muted"],
            line_gap=7,
        )
        arrow(draw, (x + 220, y + 405), (x + 220, y + 470), color=outline, width=4, head=12)

    draw.rounded_rectangle(
        (250, 1085, 1650, 1180),
        radius=18,
        fill=COLORS["green_light"],
        outline=COLORS["green"],
        width=3,
    )
    draw_center(
        draw,
        (265, 1095, 1635, 1170),
        "最终选解：capacity 轨道、balanced 轨道、动态场景轨道或用户指定偏好",
        font(28, True),
        fill=COLORS["green"],
    )
    image.save(ASSET_DIR / "evaluation_function_map.png", quality=95)


def dual_mode_flow() -> None:
    image = Image.new("RGB", (1900, 1220), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    draw.text((70, 44), "订单变更与紧急插单双模式流程", font=font(46, True), fill=COLORS["ink"])
    draw.text(
        (72, 106),
        "附件2校验成功后显示模式按钮；选择模式后再进入对应订单处理链路",
        font=font(26),
        fill=COLORS["muted"],
    )

    node(
        draw,
        (100, 210, 590, 380),
        "上传附件2并校验",
        "校验成功后才显示模式选择\n不合格时不显示模式按钮",
        fill=COLORS["blue_light"],
    )
    node(
        draw,
        (700, 210, 1200, 380),
        "选择处理模式",
        "订单变更：变更订单语义\n紧急插单：新增批次优先",
        fill=COLORS["gray_light"],
    )
    arrow(draw, (598, 295), (692, 295), width=5, head=13)

    node(
        draw,
        (180, 540, 850, 820),
        "订单变更模式",
        "使用原有动态重排算法\n新附件2作为变更后的订单数据\n不拆分新增批次与原计划批次\n机器故障和时间仍按原逻辑处理",
        fill=COLORS["teal_light"],
    )
    node(
        draw,
        (1050, 540, 1720, 820),
        "紧急插单模式",
        "新增附件2作为前置批次\n原计划保留并续排\n先算首4张并导出staging\n同名钢板直接拦截",
        fill=COLORS["red_light"],
    )
    mode_center_x = 950
    branch_y = 450
    draw.line((mode_center_x, 380, mode_center_x, branch_y), fill=COLORS["ink"], width=5)
    draw.line((515, branch_y, 1385, branch_y), fill=COLORS["ink"], width=5)
    arrow(draw, (515, branch_y), (515, 540), color=COLORS["teal"], width=5, head=13)
    arrow(draw, (1385, branch_y), (1385, 540), color=COLORS["red"], width=5, head=13)

    node(
        draw,
        (100, 900, 820, 1110),
        "staging 工作区",
        "未完成结果只写 staging\n紧急插单先导出首4张钢板\n完整计算结束后等待统一提交",
        fill=COLORS["blue_light"],
    )
    node(
        draw,
        (930, 900, 1800, 1110),
        "current 正式结果",
        "staging -> current 原子提交\n旧 current -> generations\n同步更新历史、甘特、齐套、利用率、CSV 和 balanced",
        fill=COLORS["green_light"],
    )
    arrow(draw, (515, 820), (515, 900), color=COLORS["teal"], width=5, head=13)
    arrow(draw, (1385, 820), (1385, 900), color=COLORS["red"], width=5, head=13)
    arrow(draw, (828, 1005), (922, 1005), color=COLORS["green"], width=5, head=13)

    draw.rounded_rectangle(
        (180, 1135, 1720, 1185),
        radius=12,
        fill=COLORS["red_light"],
        outline=COLORS["red"],
        width=3,
    )
    draw_center(
        draw,
        (200, 1143, 1700, 1177),
        "紧急中断：停止优化 -> 删除 staging -> 不提交 current / history -> 前端恢复正式结果",
        font(22, True),
        fill=COLORS["red"],
    )
    image.save(ASSET_DIR / "dual_mode_flow.png", quality=95)


def main() -> None:
    global ASSET_DIR, FONT_DIR
    ensure_output_dirs()
    run_dir = create_run_directory(f"report_assets_{uuid.uuid4().hex[:8]}")
    result_dir = run_result_dir(run_dir)
    ASSET_DIR = result_dir / "报告素材"
    FONT_DIR = result_dir / "字体"
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    architecture()
    process_flow()
    pareto_front()
    comparison_plot()
    dynamic_plot()
    algorithm_family_map()
    evaluation_function_map()
    dual_mode_flow()
    print(ASSET_DIR)


if __name__ == "__main__":
    main()
