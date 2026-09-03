"""物理极限分析：不依赖优化器，逐板/逐零件/逐资源计算理论下限。

脚本会调用仓库已有的数据加载函数和附件3速度表，但会独立重算切割公式与
下游工序时间，并显式检查两处容易造成误判的口径：
  1. 人工/自动坡口长度是否错误地使用了“坡口总长”（含 V 坡和 I 坡）；
  2. 25 min/张残材是否被当作完全串行时间，而题目参考指标按双工位并行。

输出：
  outputs/physical_limit/
    physical_limit_summary.json
    plate_step_times.csv
    part_step_times.csv
    resource_lower_bounds.csv
    scenario_check.csv
    findings.md
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steel_schedule_model import (  # noqa: E402
    ModelConfig,
    CranePool,
    _reserve_raw_material_crane,
    cut_duration,
    load_and_validate,
    load_process_params,
    machine_names,
    pick_machine,
    plate_features,
    simulate,
)


SHIFT_H = 8.0
REF_CMAX_H = 67.25
REF_THROUGHPUT = 13.0
DUAL_EQUIV_REMAINDER_MIN = 22.31  # 参考指标反推出的双工位等效残材时间
PLATE_CRANE_MIN = 5.0             # 后端 SATabu 排程使用的下料5min+上料3min-重叠3min


def _num(v, default: float = 0.0) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def _speed_for(thickness: float, speed_table: dict) -> dict:
    """与 steel_schedule_model._get_speeds_for_thickness 做同口径独立插值。"""
    if not speed_table:
        return {"straight": 1700.0, "vy_bevel": 800.0, "xk_bevel": 400.0}
    if thickness in speed_table:
        return speed_table[thickness]
    keys = sorted(speed_table.keys())
    if thickness <= keys[0]:
        return speed_table[keys[0]]
    if thickness >= keys[-1]:
        return speed_table[keys[-1]]
    for i in range(len(keys) - 1):
        lo_k, hi_k = keys[i], keys[i + 1]
        if lo_k <= thickness <= hi_k:
            frac = (thickness - lo_k) / (hi_k - lo_k)
            lo = speed_table[lo_k]
            hi = speed_table[hi_k]
            return {
                "straight": lo["straight"] + (hi["straight"] - lo["straight"]) * frac,
                "vy_bevel": lo["vy_bevel"] + (hi["vy_bevel"] - lo["vy_bevel"]) * frac,
                "xk_bevel": lo["xk_bevel"] + (hi["xk_bevel"] - lo["xk_bevel"]) * frac,
            }
    return speed_table[keys[-1]]


def _build_plate_cut_rows(plates, cfg, speed_table) -> tuple[list[dict], float, float, float]:
    rows = []
    total_cut = 0.0
    pure_cut_total = 0.0
    auxiliary_total = 0.0
    for _, r in plates.iterrows():
        thickness = _num(r.get("厚度(mm)"), 6.0)
        width = _num(r.get("钢板宽度(mm)"), 0.0) or _num(r.get("宽度(mm)"), 0.0)
        cut_len = _num(r.get("切割长度(mm)"))
        v_len = _num(r.get("V坡长度(mm)"))
        empty_len = _num(r.get("空行程长度(mm)"))
        mark_len = _num(r.get("划线长度(mm)"))
        n_pierce = int(_num(r.get("穿孔数")))
        sp = _speed_for(thickness, speed_table)
        t_straight = cut_len / sp["straight"] if sp["straight"] > 0 else 0.0
        t_vy = v_len / sp["vy_bevel"] if sp["vy_bevel"] > 0 else 0.0
        t_empty = empty_len / cfg.rapid_speed_mm_min if cfg.rapid_speed_mm_min > 0 else 0.0
        t_mark = mark_len / cfg.marking_speed_mm_min if cfg.marking_speed_mm_min > 0 else 0.0
        t_pierce = n_pierce * cfg.pierce_minutes
        t_cut = t_straight + t_vy + t_empty + t_mark + t_pierce + cfg.cut_remainder_minutes
        model_dur = cut_duration(r, cfg, speed_table)
        n5_ok = thickness <= 36.0 and (width == 0.0 or width <= 4500.0)
        total_cut += t_cut
        pure_cut_total += t_straight + t_vy
        auxiliary_total += t_empty + t_mark + t_pierce + cfg.cut_remainder_minutes
        rows.append({
            "套料图名": r["套料图名"],
            "分段号": r["分段号"],
            "厚度(mm)": round(thickness, 4),
            "N5资格": "是" if n5_ok else "否",
            "直切长度(mm)": round(cut_len, 4),
            "V坡长度(mm)": round(v_len, 4),
            "空行程长度(mm)": round(empty_len, 4),
            "划线长度(mm)": round(mark_len, 4),
            "直切时间(min)": round(t_straight, 6),
            "V坡时间(min)": round(t_vy, 6),
            "空行程时间24m/min(min)": round(t_empty, 6),
            "划线时间24m/min(min)": round(t_mark, 6),
            "穿孔时间(min)": round(t_pierce, 6),
            "切割总时间(min)": round(t_cut, 6),
            "模型cut_duration(min)": round(model_dur, 6),
            "模型误差(min)": round(model_dur - t_cut, 9),
        })
    return rows, total_cut, pure_cut_total, auxiliary_total


def _build_part_step_rows(parts, cfg, pp, speed_table, plate_release: dict | None = None) -> tuple[list[dict], dict]:
    rows = []
    sums = {
        "small_sort": 0.0,
        "small_grind": 0.0,
        "auto_fixed": 0.0,
        "auto_current": 0.0,
        "palletize": 0.0,
        "large_crane": 0.0,
        "large_grind": 0.0,
        "manual_fixed": 0.0,
        "manual_current": 0.0,
        "agv_part_current": 0.0,
        "agv_part_fixed": 0.0,
    }

    sg_speed = pp.small_grind_speed_mm_min if pp else cfg.small_grind_speed_mm_min
    sg_scan = pp.small_grind_scan_minutes if pp else cfg.small_grind_scan_minutes
    lg_speed = pp.large_grind_speed_mm_min if pp else cfg.large_grind_speed_mm_min
    mb_speed = pp.manual_bevel_speed_mm_min if pp else cfg.manual_bevel_speed_mm_min
    ab_overhead = pp.auto_bevel_overhead_minutes if pp else cfg.auto_bevel_overhead_minutes
    small_trans = pp.small_transfer_minutes if pp else cfg.small_transfer_minutes
    large_trans = pp.large_transfer_minutes if pp else cfg.large_transfer_minutes

    for _, r in parts.iterrows():
        pname = r["零件名"]
        is_small = str(r["零件类型"]).strip().lower() == "small"
        y = _num(r.get("Y坡长度(mm)"))
        x = _num(r.get("X坡长度(mm)"))
        k = _num(r.get("K坡长度(mm)"))
        bv_yxk = y + x + k
        bv_total = _num(r.get("坡口总长(mm)"))
        grind = _num(r.get("打磨长度_模型(mm)"))
        thickness = _num(r.get("厚度(mm)"), 6.0)
        sp = _speed_for(thickness, speed_table)

        row = {
            "零件名": pname,
            "套料图名": r["套料图名"],
            "分段号": r["分段号"],
            "齐套优先级": r["齐套优先级"],
            "零件类型": r["零件类型"],
            "厚度(mm)": round(thickness, 4),
        }

        if plate_release is not None:
            row["理想切割释放(min)"] = round(plate_release.get(r["套料图名"], float("nan")), 6)

        if is_small:
            sort_dur = cfg.small_sort_minutes if grind > 0 else cfg.small_truss_direct_palletize_minutes
            grind_dur = grind * 2.0 / sg_speed + sg_scan
            auto_fixed = 0.0
            auto_current = 0.0
            if bv_yxk > 0:
                auto_fixed = y / sp["vy_bevel"] + (x + k) / sp["xk_bevel"] + ab_overhead
            if bv_total > 0:
                auto_current = y / sp["vy_bevel"] + (x + k) / sp["xk_bevel"] + ab_overhead
            palletize_dur = cfg.small_truss_palletize_minutes
            agv_current = small_trans * (2 if bv_total > 0 else 1)
            agv_fixed = small_trans * (2 if bv_yxk > 0 else 1)

            row.update({
                "分拣(min)": round(sort_dur, 6),
                "自动打磨(min)": round(grind_dur, 6),
                "自动坡口修正Y+X+K(min)": round(auto_fixed, 6),
                "自动坡口现状按坡口总长触发(min)": round(auto_current, 6),
                "桁架码垛(min)": round(palletize_dur, 6),
                "AGV所在料框转运(修正,min)": round(agv_fixed, 6),
                "AGV所在料框转运(现状,min)": round(agv_current, 6),
                "自由边打磨(min)": 0.0,
                "人工坡口修正Y+X+K(min)": 0.0,
                "人工坡口现状按坡口总长(min)": 0.0,
                "天车吊运(min)": 0.0,
            })
            sums["small_sort"] += sort_dur
            sums["small_grind"] += grind_dur
            sums["auto_fixed"] += auto_fixed
            sums["auto_current"] += auto_current
            sums["palletize"] += palletize_dur
            sums["agv_part_fixed"] += agv_fixed
            sums["agv_part_current"] += agv_current
        else:
            grind_dur = grind / lg_speed
            manual_fixed = bv_yxk / mb_speed if bv_yxk > 0 else 0.0
            manual_current = bv_total / mb_speed if bv_total > 0 else 0.0
            row.update({
                "分拣(min)": 0.0,
                "自动打磨(min)": 0.0,
                "自动坡口修正Y+X+K(min)": 0.0,
                "自动坡口现状按坡口总长触发(min)": 0.0,
                "桁架码垛(min)": 0.0,
                "AGV所在料框转运(修正,min)": 0.0,
                "AGV所在料框转运(现状,min)": 0.0,
                "自由边打磨(min)": round(grind_dur, 6),
                "人工坡口修正Y+X+K(min)": round(manual_fixed, 6),
                "人工坡口现状按坡口总长(min)": round(manual_current, 6),
                "天车吊运(min)": round(large_trans, 6),
            })
            sums["large_crane"] += large_trans
            sums["large_grind"] += grind_dur
            sums["manual_fixed"] += manual_fixed
            sums["manual_current"] += manual_current

        chain_fixed = (
            row["分拣(min)"] + row["自动打磨(min)"] + row["自动坡口修正Y+X+K(min)"]
            + row["桁架码垛(min)"] + row["AGV所在料框转运(修正,min)"]
            + row["自由边打磨(min)"] + row["人工坡口修正Y+X+K(min)"] + row["天车吊运(min)"]
        )
        chain_current = (
            row["分拣(min)"] + row["自动打磨(min)"] + row["自动坡口现状按坡口总长触发(min)"]
            + row["桁架码垛(min)"] + row["AGV所在料框转运(现状,min)"]
            + row["自由边打磨(min)"] + row["人工坡口现状按坡口总长(min)"] + row["天车吊运(min)"]
        )
        row["下游链长(修正,min)"] = round(chain_fixed, 6)
        row["下游链长(现状,min)"] = round(chain_current, 6)
        if plate_release is not None:
            row["理想就绪(修正,min)"] = round(plate_release.get(r["套料图名"], float("nan")) + chain_fixed, 6)
            row["理想就绪(现状,min)"] = round(plate_release.get(r["套料图名"], float("nan")) + chain_current, 6)
        rows.append(row)

    return rows, sums


def _small_bin_agv_total(parts, cfg, pp) -> tuple[float, int]:
    small = parts[parts["零件类型"] == "small"].copy()
    small["_bv_yxk"] = (
        small["Y坡长度(mm)"].fillna(0)
        + small["K坡长度(mm)"].fillna(0)
        + small["X坡长度(mm)"].fillna(0)
    )
    small["_bv_total"] = small["坡口总长(mm)"].fillna(0)
    small_trans = pp.small_transfer_minutes if pp else cfg.small_transfer_minutes
    small["_bv_total_flag"] = small["_bv_total"] > 0
    small["_bv_yxk_flag"] = small["_bv_yxk"] > 0
    current_groups = small.groupby(["分段号", "齐套优先级", "_bv_total_flag"]).ngroups
    fixed_groups = small.groupby(["分段号", "齐套优先级", "_bv_yxk_flag"]).ngroups
    current_total = 0.0
    fixed_total = 0.0
    for _, g in small.groupby(["分段号", "齐套优先级", "_bv_total_flag"]):
        current_total += small_trans * (2 if bool(g["_bv_total_flag"].iloc[0]) else 1)
    for _, g in small.groupby(["分段号", "齐套优先级", "_bv_yxk_flag"]):
        fixed_total += small_trans * (2 if bool(g["_bv_yxk_flag"].iloc[0]) else 1)
    return current_total, fixed_total, current_groups, fixed_groups


def _build_schedule(features, cfg, use_crane: bool) -> tuple[pd.DataFrame, dict]:
    name_col = [c for c in features.columns if "套料图名" in c][0]
    seg_col = [c for c in features.columns if "分段号" in c][0]
    pri_col = [c for c in features.columns if "优先" in c or "最低" in c][0]
    cut_col = [c for c in features.columns if "工时" in c and "min" in c][0]
    order = features.sort_values(cut_col, ascending=False)[name_col].tolist()
    ordered = features.set_index(name_col).loc[order].reset_index()
    free = {m: 0.0 for m in machine_names(cfg)}
    worktables = {m: [0.0, 0.0] for m in machine_names(cfg)}
    gun_free = {m: 0.0 for m in machine_names(cfg)}
    crane_pool = CranePool()
    release = {}
    rows = []
    for seq, (_, r) in enumerate(ordered.iterrows(), start=1):
        m = pick_machine(r, free)
        raw_start, raw_empty_end, raw_loaded_end = _reserve_raw_material_crane(
            crane_pool, free[m], cfg,
        )
        start = max(free[m], raw_loaded_end)
        end = start + _num(r[cut_col])
        handling = _num(r.get("工位处理时间(min)"))
        table_idx = 0 if worktables[m][0] <= start else 1
        table_end = end + handling
        worktables[m][table_idx] = table_end
        gun_free[m] = end
        free[m] = max(gun_free[m], min(worktables[m]))
        release[r[name_col]] = end
        rows.append({
            "套料图名": r[name_col],
            "分段号": r[seg_col],
            "切割机": m,
            "切割序号": seq,
            "切割开始(min)": start,
            "切割完成(min)": end,
            "切割工时(min)": _num(r[cut_col]),
            "原料吊运开始(min)": raw_start,
            "原料空驶完成(min)": raw_empty_end,
            "原料吊运完成(min)": raw_loaded_end,
            "原料吊运返回完成(min)": raw_loaded_end,
            "工位处理时间(min)": handling,
            "工位完工(min)": table_end,
            "最低优先级": r[pri_col],
        })
    return pd.DataFrame(rows), release


def _ideal_full_line_makespan(part_rows, parts, cfg, pp) -> dict:
    df = pd.DataFrame(part_rows)
    df = df.merge(
        parts[["零件名", "分段号", "齐套优先级", "零件类型"]].drop_duplicates("零件名"),
        on="零件名",
        how="left",
        suffixes=("", "_parts"),
    )
    if "分段号_parts" in df.columns:
        df["分段号"] = df["分段号_parts"]
        df["齐套优先级"] = df["齐套优先级_parts"]
        df["零件类型"] = df["零件类型_parts"]
    small = df[df["零件类型"] == "small"].copy()
    large = df[df["零件类型"] == "large"].copy()
    small_trans = pp.small_transfer_minutes if pp else cfg.small_transfer_minutes
    g_ready = df.groupby(["分段号", "齐套优先级"])["理想就绪(修正,min)"].max().rename("组就绪(min)")
    small = small.merge(g_ready, on=["分段号", "齐套优先级"], how="left")
    small["最终到齐套(min)"] = small["组就绪(min)"] + small_trans
    overall = max(
        float(small["最终到齐套(min)"].max()),
        float(large["理想就绪(修正,min)"].max()),
    )
    return {
        "理想整线完工(修正,min)": round(overall, 6),
        "理想整线完工(修正,h)": round(overall / 60, 6),
        "组就绪最大(min)": round(float(g_ready.max()), 6),
        "小件最终到齐套最大(min)": round(float(small["最终到齐套(min)"].max()), 6),
        "大件就绪最大(min)": round(float(large["理想就绪(修正,min)"].max()), 6),
    }


def _run_scenario(label, parts_df, remainder_min, use_crane, plates, parts_original, cfg, pp, speed_table):
    cfg2 = copy.deepcopy(cfg)
    cfg2.cut_remainder_minutes = remainder_min
    features = plate_features(plates, parts_df, cfg2, speed_table)
    sched, release = _build_schedule(features, cfg2, use_crane)
    complete, groups, metrics, stages, _ = simulate(sched, parts_df, cfg2, pp)
    kit_move_count = int((stages["工序"].astype(str) == "AGV转齐套缓存").sum())
    return {
        "场景": label,
        "坡口口径": "Y+X+K（当前模型）",
        "残材口径(min/张)": remainder_min,
        "是否叠加每板天车5min": use_crane,
        "LPT切割最大机时(h)": round(max(release.values()) / 60, 6),
        "仿真报告总完工时间(h)": metrics["总完工时间(h)"],
        "仿真阶段最大结束(h)": round(float(stages["结束(min)"].max()) / 60, 6),
        "仿真半框最大到达(h)": round(float(complete["到半框缓存(min)"].max()) / 60, 6),
        "AGV转齐套缓存次数": kit_move_count,
        "整体产能(张/8h班)": metrics["整体产能(张板/班)"],
        "人工坡口利用率": metrics["人工坡口1利用率"],
        "自动坡口利用率": metrics["自动坡口1利用率"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=Path("data/data_0/附件2：钢板零件数据.xlsx"), type=Path)
    parser.add_argument("--speed-table", default=Path("data/data_0/附件3：工艺用时计算表.xlsx"), type=Path)
    parser.add_argument("--output", default=Path("outputs/physical_limit"), type=Path)
    args = parser.parse_args()

    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    plates, parts, checks = load_and_validate(args.input)
    pp = load_process_params(args.speed_table)
    cfg = ModelConfig()
    speed_table = pp.speed_table

    parts_fixed = parts.copy()
    parts_fixed["人工坡口长度(mm)"] = parts_fixed[["Y坡长度(mm)", "K坡长度(mm)", "X坡长度(mm)"]].sum(axis=1)
    parts_fixed["自动坡口长度(mm)"] = parts_fixed[["Y坡长度(mm)", "K坡长度(mm)", "X坡长度(mm)"]].sum(axis=1)

    # ── 1. 逐板切割时间，并与现有 cut_duration 交叉验证 ──
    plate_cut_rows, total_cut, pure_cut_total, auxiliary_total = _build_plate_cut_rows(
        plates, cfg, speed_table
    )
    plate_cut_df = pd.DataFrame(plate_cut_rows)
    max_cut_error = float(plate_cut_df["模型误差(min)"].abs().max())

    # ── 2. 逐零件下游时间 ──
    # 先做一个 LPT 切割排程，只为得到“理想释放时间”，不用于物理下限结论。
    feats_lpt = plate_features(plates, parts_fixed, cfg, speed_table)
    _, release_lpt = _build_schedule(feats_lpt, cfg, use_crane=False)

    part_rows, part_sums = _build_part_step_rows(parts_fixed, cfg, pp, speed_table, release_lpt)
    part_df = pd.DataFrame(part_rows)

    # 现状口径下的零件级明细也需要一份（用于对比人工坡口 bug 的影响）。
    part_rows_current, _ = _build_part_step_rows(parts, cfg, pp, speed_table, release_lpt)
    part_df_current = pd.DataFrame(part_rows_current)

    # ── 3. 资源工作量与理论下限 ──
    n_plates = len(plates)
    n_small = int((parts["零件类型"] == "small").sum())
    n_large = int((parts["零件类型"] == "large").sum())
    target_thr13_cmax = n_plates * SHIFT_H / REF_THROUGHPUT
    available_min_ref = REF_CMAX_H * 60.0
    available_min_thr13 = target_thr13_cmax * 60.0

    agv_current, agv_fixed, agv_bins_current, agv_bins_fixed = _small_bin_agv_total(parts, cfg, pp)
    crane_current = n_plates * PLATE_CRANE_MIN + n_large * cfg.large_transfer_minutes
    crane_dual = n_large * cfg.large_transfer_minutes
    crane_ratio = float(getattr(cfg, "crane_return_ratio", 0.5))
    n_large_bevel = int(
        ((parts["零件类型"] == "large") & (parts["人工坡口长度(mm)"] > 0)).sum()
    )
    n_large_no_bevel = n_large - n_large_bevel
    crane_total_new = (
        n_plates * cfg.plate_setup_minutes * (1 + crane_ratio)
        + n_large_no_bevel * cfg.large_transfer_minutes * (1 + crane_ratio)
        + n_large_bevel
        * (cfg.crane_bevel_transfer_minutes + cfg.large_transfer_minutes)
        * (1 + crane_ratio)
    )

    resources = [
        ("切割机(分项明细+双工位)", total_cut, cfg.cutting_machines, "直切+V坡+空行程+划线+穿孔+换板，残材并行不计"),
        ("直切+V坡纯切割", pure_cut_total, cfg.cutting_machines, "纯切割负荷下限"),
        ("人工坡口(Y+X+K)", part_sums["manual_fixed"], cfg.manual_bevel_stations, "V坡由切割机加工，I坡不计入"),
        ("自动坡口(Y+X+K触发)", part_sums["auto_fixed"], cfg.auto_bevel_machines, "只对真实Y/X/K坡零件计3.5min开销"),
        ("小件自动打磨", part_sums["small_grind"], cfg.small_grinders, "2台小件打磨"),
        ("大件人工打磨", part_sums["large_grind"], cfg.large_grinders, "2台大件打磨"),
        ("桁架分拣(小件)", part_sums["small_sort"], cfg.small_sorters, "2套分拣桁架"),
        ("桁架码垛(小件)", part_sums["palletize"], cfg.small_sorters, "2套桁架"),
        ("桁架分拣+码垛", part_sums["small_sort"] + part_sums["palletize"], cfg.small_sorters, "同一桁架资源"),
        ("AGV小件转运(现状料框口径)", agv_current, cfg.agvs, "按料框数量计，不按零件数重复计"),
        ("AGV小件转运(修正料框口径)", agv_fixed, cfg.agvs, "按料框数量计"),
        ("天车(原料+大件+空返)", crane_total_new, 1, "原料3min、无坡口5min、坡口3+5min，空返比例0.5"),
    ]

    resource_rows = []
    for name, workload, count, note in resources:
        lb = workload / max(1, count) / 60.0
        ratio_ref = lb / REF_CMAX_H
        ratio_13 = lb / target_thr13_cmax
        resource_rows.append({
            "资源/工序": name,
            "总负荷(min)": round(workload, 4),
            "设备/工位数": count,
            "理论下限(h)": round(lb, 6),
            "目标67.25h负荷比": round(ratio_ref, 6),
            "目标13张/班负荷比": round(ratio_13, 6),
            "说明": note,
        })
    resource_df = pd.DataFrame(resource_rows)

    # ── 4. 理想整线完工（只受切割释放 + 组齐套约束，下游资源视为充足）──
    ideal = _ideal_full_line_makespan(part_df, parts_fixed, cfg, pp)

    # ── 5. 用现有 simulate 做几个对照场景，检查口径差异和事件队列 bug ──
    scenarios = [
        _run_scenario(
            "新模型：Y+X+K坡口 + 0残材 + 双工位无天车",
            parts, 0.0, False, plates, parts, cfg, pp, speed_table,
        ),
        _run_scenario(
            "旧口径对照：+25min残材 + 后端天车",
            parts, 25.0, True, plates, parts, cfg, pp, speed_table,
        ),
        _run_scenario(
            "旧口径对照：+25min残材 + 无天车",
            parts, 25.0, False, plates, parts, cfg, pp, speed_table,
        ),
        _run_scenario(
            "新模型含残材参数回退：+25min + 无天车",
            parts, 25.0, False, plates, parts, cfg, pp, speed_table,
        ),
    ]
    scenario_df = pd.DataFrame(scenarios)

    # ── 6. 汇总与发现 ──
    current_manual_bevel_lb = part_sums["manual_current"] / cfg.manual_bevel_stations / 60.0
    fixed_manual_bevel_lb = part_sums["manual_fixed"] / cfg.manual_bevel_stations / 60.0

    findings = [
        "1. 已按要求修改坡口口径：",
        "   load_and_validate 现在把人工坡口和自动坡口长度统一设为 Y坡 + X坡 + K坡，",
        "   V坡由切割机完成，I坡不计入坡口工序。",
        f"   data_0 下人工坡口总负荷约 {part_sums['manual_fixed']:.0f} min"
        f"（{fixed_manual_bevel_lb:.2f} h），自动坡口约 {part_sums['auto_fixed']:.0f} min"
        f"（{part_sums['auto_fixed']/cfg.auto_bevel_machines/60:.2f} h）。",
        "",
        "2. 切割时间已改为分项明细公式：",
        "   直切 + V坡 + 空行程/24m/min + 划线/24m/min + 穿孔数×0.1min；",
        "   双工位下残材/换料与另一工位切割并行，默认不再串行加25min。",
        f"   data_0 直切+V坡纯切割约 {pure_cut_total:.1f} min（{pure_cut_total/60:.2f} h），",
        f"   空行程/划线/穿孔辅助约 {auxiliary_total:.1f} min（{auxiliary_total/60:.2f} h），",
        f"   切割总负荷约 {total_cut:.1f} min（{total_cut/60:.2f} h），"
        f"   两台机理论下限约 {total_cut/cfg.cutting_machines/60:.2f} h。",
        "",
        "3. 大件流程已按双工位逻辑调整：",
        "   切割完成 -> 切割胎架自由边打磨 -> 人工天车吊运 -> 人工坡口 -> 成品区；",
        "   大件不再占用桁架分拣资源。",
        "",
        "4. 切割排程已去掉每板 5min 天车串行：",
        "   main()、improved_optimizer.schedule_from_order、backend 动态重排均改为"
        "   双工位下直接按机台空闲时间开始下一张板。",
        "",
        "5. simulate 的事件队列仍存在齐套门控 bug：",
        "   group_arrived 按“料框”计数但 group_total 按“零件数”计数，多件料框的组几乎永远不满足齐套条件，",
        "   导致 AGV转齐套缓存 不触发，同时只有部分料框进入半框缓存。",
        f"   当前脚本对照中，AGV转齐套缓存次数 = {scenario_df.iloc[0]['AGV转齐套缓存次数']}，"
        "   “到齐套区”实际混用了未更新的初始值，不能作为物理极限依据。",
        "",
        "6. 新模型下的结果：",
        f"   LPT 切割最大机时约 {scenario_df.iloc[0]['LPT切割最大机时(h)']:.2f} h，",
        f"   simulate 报告总完工时间约 {scenario_df.iloc[0]['仿真报告总完工时间(h)']:.2f} h，"
        f"   整体产能约 {scenario_df.iloc[0]['整体产能(张/8h班)']:.2f} 张/班。",
        "   旧口径 +25min残材/每板天车仅作为对照保留，不再作为默认模型。",
    ]
    findings_text = "\n".join(findings)
    (out / "findings.md").write_text(findings_text + "\n", encoding="utf-8")

    summary = {
        "数据校验": checks,
        "数据规模": {
            "钢板数": n_plates,
            "零件数": int(len(parts)),
            "小件数": n_small,
            "大件数": n_large,
            "齐套组数": checks["齐套组数(分段+优先级)"],
        },
        "目标口径": {
            "题目参考总完工时间(h)": REF_CMAX_H,
            "13张/班对应总完工时间(h)": round(target_thr13_cmax, 6),
            "说明": "新模型残材默认0，目标仅作产能对比参考",
        },
        "切割汇总": {
            "直切总时间(min)": round(sum(plate_cut_df["直切时间(min)"]), 4),
            "V坡总时间(min)": round(sum(plate_cut_df["V坡时间(min)"]), 4),
            "空行程总时间(min)": round(sum(plate_cut_df["空行程时间24m/min(min)"]), 4),
            "划线总时间(min)": round(sum(plate_cut_df["划线时间24m/min(min)"]), 4),
            "穿孔总时间(min)": round(sum(plate_cut_df["穿孔时间(min)"]), 4),
            "切割总工时(min)": round(total_cut, 4),
            "两台机理论下限(h)": round(total_cut / cfg.cutting_machines / 60, 6),
            "cut_duration最大误差(min)": max_cut_error,
        },
        "人工坡口": {
            "现状坡口总长总负荷(min)": round(part_sums["manual_current"], 4),
            "现状单工位下限(h)": round(current_manual_bevel_lb, 6),
            "修正Y+X+K总负荷(min)": round(part_sums["manual_fixed"], 4),
            "修正单工位下限(h)": round(fixed_manual_bevel_lb, 6),
        },
        "自动坡口": {
            "现状触发总负荷(min)": round(part_sums["auto_current"], 4),
            "修正Y+X+K总负荷(min)": round(part_sums["auto_fixed"], 4),
        },
        "理想整线(修正+双工位)": ideal,
        "场景对照": scenarios,
        "资源下限": resource_rows,
        "发现": findings,
    }
    (out / "physical_limit_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    step_agg = part_df.groupby("套料图名").agg(
        零件数=("零件名", "size"),
        小件数=("零件类型", lambda s: int((s == "small").sum())),
        大件数=("零件类型", lambda s: int((s == "large").sum())),
        分拣合计=("分拣(min)", "sum"),
        自动打磨合计=("自动打磨(min)", "sum"),
        自动坡口修正合计=("自动坡口修正Y+X+K(min)", "sum"),
        自动坡口现状合计=("自动坡口现状按坡口总长触发(min)", "sum"),
        桁架码垛合计=("桁架码垛(min)", "sum"),
        自由边打磨合计=("自由边打磨(min)", "sum"),
        人工坡口修正合计=("人工坡口修正Y+X+K(min)", "sum"),
        人工坡口现状合计=("人工坡口现状按坡口总长(min)", "sum"),
        天车吊运合计=("天车吊运(min)", "sum"),
        链长最大值修正=("下游链长(修正,min)", "max"),
        链长最大值现状=("下游链长(现状,min)", "max"),
    ).reset_index()
    plate_out = plate_cut_df.merge(step_agg, on="套料图名", how="left")
    plate_out.to_csv(out / "plate_step_times.csv", index=False, encoding="utf-8-sig")
    part_df.to_csv(out / "part_step_times.csv", index=False, encoding="utf-8-sig")
    part_df_current.to_csv(out / "part_step_times_current_scope.csv", index=False, encoding="utf-8-sig")
    resource_df.to_csv(out / "resource_lower_bounds.csv", index=False, encoding="utf-8-sig")
    scenario_df.to_csv(out / "scenario_check.csv", index=False, encoding="utf-8-sig")

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
