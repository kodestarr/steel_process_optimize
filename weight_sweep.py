"""钢板排产模型权重自动扫描脚本。

遍历 (Cmax权重, 齐套权重, 负载差权重) 的多种组合，用 multiprocessing
并行运行 SA+Tabu 优化，输出中文对比表，并找出帕累托最优组合。

使用示例：
    python weight_sweep.py                     # 交互选择 1=SA线性 2=SA二次 3=GA线性 4=GA二次
    python weight_sweep.py --sweep-type 3      # 直接指定 GA+LNS + 线性评价
    python weight_sweep.py --step 0.02 --workers 16
    python weight_sweep.py --step 0.02 --mode full --limit 10
    python weight_sweep.py --step 0.02 --workers 8
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
import random
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
MODEL_DIR = PROJECT_ROOT / "挑战杯 demo6" / "挑战杯"
if str(MODEL_DIR) not in sys.path:
    sys.path.insert(0, str(MODEL_DIR))

DEFAULT_ATTACH2 = PROJECT_ROOT / "产线场景描述" / "附件2：钢板零件数据.xlsx"
DEFAULT_ATTACH3 = PROJECT_ROOT / "产线场景描述" / "附件3：工艺用时计算表.xlsx"

REFERENCE_WEIGHTS = [
    (0.45, 0.40, 0.15),
    (0.55, 0.30, 0.15),
    (0.30, 0.55, 0.15),
    (0.40, 0.40, 0.20),
    (0.35, 0.35, 0.30),
]

SWEEP_INDICATORS = [
    "总完工时间权重",
    "齐套跨度权重",
    "负载差权重",
]

RATIO_LABELS = {
    "总完工时间权重": "齐套跨度权重 / 负载差权重",
    "齐套跨度权重": "负载差权重 / 总完工时间权重",
    "负载差权重": "总完工时间权重 / 齐套跨度权重",
}

Z_METRICS = [
    ("总完工时间(h)", "cmax_h"),
    ("加权平均齐套跨度(h)", "kit_span_h"),
    ("切割负载差(h)", "load_diff_h"),
]

_WORKER: dict = {}

# 内部字段名 -> CSV 中文表头
CSV_LABELS = {
    "w_cmax": "Cmax权重",
    "w_kit": "齐套跨度权重",
    "w_load": "负载差权重",
    "cmax_h": "总完工时间(h)",
    "kit_span_h": "加权平均齐套跨度(h)",
    "load_diff_h": "切割负载差(h)",
    "strategy": "初始策略",
    "tier": "选择档位",
    "elapsed_s": "耗时(秒)",
    "objective_own": "本组权重目标值",
    "objective_default": "默认0.4/0.4/0.2目标值",
    "meets_targets": "是否满足竞赛目标",
    "error": "错误信息",
    "mode": "运行模式",
    "sweep_x": "扫描主轴指标",
    "x_weight": "主轴权重",
    "ratio": "另两个指标比值",
}

INTERNAL_COLUMNS = [
    "w_cmax",
    "w_kit",
    "w_load",
    "cmax_h",
    "kit_span_h",
    "load_diff_h",
    "strategy",
    "tier",
    "elapsed_s",
    "objective_own",
    "objective_default",
    "meets_targets",
    "error",
    "sweep_x",
    "x_weight",
    "ratio",
]

_STRATEGY_LABELS = {
    "FIFO": "先进先出",
    "Completeness": "齐套优先",
    "SegPriShort": "分段优先级短工时",
    "SegPriLong": "分段优先级长工时",
    "SPT": "短工时优先",
    "ThinFirst": "薄板优先",
}


def _label_strategy(name: str) -> str:
    """把内部策略名转换成中文显示名。"""
    if not name:
        return ""
    if name.endswith("-archive"):
        return _STRATEGY_LABELS.get(name[: -len("-archive")], name[: -len("-archive")]) + "-存档"
    return _STRATEGY_LABELS.get(name, name)


def _label_row(row: dict, columns: list[str]) -> dict:
    """把内部字段名转换成中文 CSV 表头。"""
    return {CSV_LABELS.get(col, col): row.get(col) for col in columns}


def _decode_row(row: dict, columns: list[str]) -> dict:
    """把中文 CSV 表头恢复成内部字段名。"""
    return {col: row.get(CSV_LABELS.get(col, col)) for col in columns}


def _find_col(df, keywords):
    for col in df.columns:
        for kw in keywords:
            if kw in col:
                return col
    raise KeyError(f"找不到匹配列：{keywords}，可用列：{list(df.columns)}")


def _init_worker(input_path: str, speed_path: str):
    from steel_schedule_model import load_and_validate, load_process_params

    _WORKER["plates"], _WORKER["parts"], _WORKER["checks"] = load_and_validate(Path(input_path))
    _WORKER["pp"] = None
    _WORKER["speed_table"] = None
    if speed_path and Path(speed_path).exists():
        pp = load_process_params(Path(speed_path))
        _WORKER["pp"] = pp
        _WORKER["speed_table"] = pp.speed_table


def _build_init_orders(features, plates, speed_table, cfg):
    """构建多组初始排序：FIFO、齐套优先、分段优先级、短工时、薄板优先。"""
    from steel_schedule_model import make_greedy_schedule

    name_col = _find_col(features, ["套料图名", "plate"])
    seg_col = _find_col(features, ["分段号", "segment"])
    seq_col = _find_col(features, ["序号", "seq"])
    cut_col = "切割工时(min)"
    pri_col = _find_col(features, ["优先", "最低"])

    orders = {}
    orders["FIFO"] = features.sort_values(seq_col, kind="stable")[name_col].tolist()

    comp_sched = make_greedy_schedule(
        features, "completeness", cfg.random_seed, cfg=cfg
    )
    comp_seq_col = _find_col(comp_sched, ["序号", "切割序号"])
    orders["Completeness"] = comp_sched.sort_values(comp_seq_col)[name_col].tolist()

    orders["SegPriShort"] = features.sort_values(
        [seg_col, pri_col, cut_col], ascending=[True, True, True], kind="stable"
    )[name_col].tolist()
    orders["SegPriLong"] = features.sort_values(
        [seg_col, pri_col, cut_col], ascending=[True, True, False], kind="stable"
    )[name_col].tolist()
    orders["SPT"] = features.sort_values(cut_col, ascending=True, kind="stable")[name_col].tolist()

    thick_cols = [c for c in plates.columns if "厚" in c]
    if thick_cols and speed_table:
        thick_map = dict(zip(plates[name_col], plates[thick_cols[0]]))
        features["_thick"] = features[name_col].map(thick_map)
        orders["ThinFirst"] = features.sort_values("_thick", ascending=True, kind="stable")[name_col].tolist()
        features.drop(columns=["_thick"], inplace=True, errors="ignore")

    return orders


def _select_best(candidates, fifo_cmax):
    from steel_schedule_model import ModelConfig

    cmax_target = ModelConfig.CMAX_TARGET_H
    kit_target = ModelConfig.KITSPAN_TARGET_H
    tier1 = [
        (n, o, m) for n, o, m in candidates
        if m["总完工时间(h)"] <= fifo_cmax and m["加权平均齐套跨度(h)"] <= kit_target
    ]
    tier2 = [
        (n, o, m) for n, o, m in candidates
        if m["总完工时间(h)"] <= cmax_target and m["加权平均齐套跨度(h)"] <= kit_target
    ]
    if tier1:
        best = min(tier1, key=lambda x: x[2]["加权平均齐套跨度(h)"])
        tier = "第1档(Cmax≤FIFO且齐套≤12.7)"
    elif tier2:
        best = min(tier2, key=lambda x: x[2]["总完工时间(h)"])
        tier = "第2档(Cmax≤67.25且齐套≤12.7)"
    else:
        best = min(
            candidates,
            key=lambda x: (
                (x[2]["总完工时间(h)"] - cmax_target) / cmax_target * 10.0
                + (x[2]["加权平均齐套跨度(h)"] - kit_target) / kit_target * 10.0
            ),
        )
        tier = "第3档(兜底)"
    return best[0], best[1], best[2], tier


def _quick_optimize(weights, iterations, time_limit):
    """快速粗扫：每个初始策略使用可控的迭代次数和秒数上限。"""
    from improved_optimizer import SATabuOptimizer
    from steel_schedule_model import ModelConfig, plate_features

    plates = _WORKER["plates"]
    parts = _WORKER["parts"]
    pp = _WORKER["pp"]
    speed_table = _WORKER["speed_table"]

    cfg = ModelConfig()
    cfg.obj_weight_cmax = float(weights[0])
    cfg.obj_weight_kit = float(weights[1])
    cfg.obj_weight_load = float(weights[2])
    cfg = cfg.adapt_to_data(plates, parts, pp)

    features = plate_features(plates, parts, cfg, speed_table)
    init_orders = _build_init_orders(features, plates, speed_table, cfg)
    fifo_order = init_orders["FIFO"]

    fifo_builder = SATabuOptimizer(features, parts, cfg, pp, seed=cfg.random_seed)
    shared_cache: dict = {}
    fifo_builder._eval_cache = shared_cache
    _, fifo_met, _ = fifo_builder.schedule_from_order(fifo_order)
    fifo_cmax = fifo_met["总完工时间(h)"]
    fifo_kit = fifo_met["加权平均齐套跨度(h)"]
    fifo_load = fifo_met["切割负载差(h)"]
    fifo_waiting = fifo_met.get("总等待时间(h)", 0.0)

    candidates = []  # 收集所有初始策略及其帕累托存档解
    for name, order in init_orders.items():
        name_hash = int(hashlib.md5(name.encode("utf-8")).hexdigest(), 16) % 100000
        opt = SATabuOptimizer(features, parts, cfg, pp, seed=cfg.random_seed + name_hash)
        opt._eval_cache = shared_cache
        opt._focused_descent_iterations = 0  # quick 模式跳过 SA 后的 150 次定向精修
        opt.fifo_cmax = fifo_cmax
        opt.fifo_kit_span = fifo_kit
        opt.fifo_load_diff = fifo_load
        opt.fifo_waiting = fifo_waiting
        order_out, metrics, _stats = opt.optimize(
            order,
            max_iterations=max(1, int(iterations)),
            time_limit_seconds=max(1.0, float(time_limit)),
            verbose=False,
        )
        candidates.append((name, order_out, metrics))
        for arch_order, arch_met in opt.pareto_archive:
            candidates.append((f"{name}-archive", arch_order, arch_met))

    best_name, _best_order, best_met, tier = _select_best(candidates, fifo_cmax)
    return {
        "strategy": best_name,
        "tier": tier,
        "cmax_h": float(best_met["总完工时间(h)"]),
        "kit_span_h": float(best_met["加权平均齐套跨度(h)"]),
        "load_diff_h": float(best_met["切割负载差(h)"]),
    }


def _quick_quadratic_optimize(weights, iterations, time_limit):
    """快速二次式扫描：SA+Tabu 原搜索流程，评价函数换成 FIFO 相对二次式。"""
    from improved_optimizer import SATabuOptimizer
    from pareto_optimizer import QuadraticSAOptimizer
    from steel_schedule_model import ModelConfig, plate_features

    plates = _WORKER["plates"]
    parts = _WORKER["parts"]
    pp = _WORKER["pp"]
    speed_table = _WORKER["speed_table"]

    cfg = ModelConfig()
    cfg.obj_weight_cmax = float(weights[0])
    cfg.obj_weight_kit = float(weights[1])
    cfg.obj_weight_load = float(weights[2])
    cfg = cfg.adapt_to_data(plates, parts, pp)

    features = plate_features(plates, parts, cfg, speed_table)
    init_orders = _build_init_orders(features, plates, speed_table, cfg)
    fifo_order = init_orders["FIFO"]

    fifo_builder = QuadraticSAOptimizer(features, parts, cfg, pp, seed=cfg.random_seed)
    shared_cache: dict = {}
    fifo_builder._eval_cache = shared_cache
    _, fifo_met, _ = fifo_builder.schedule_from_order(fifo_order)
    fifo_cmax = fifo_met["总完工时间(h)"]
    fifo_kit = fifo_met["加权平均齐套跨度(h)"]
    fifo_load = fifo_met["切割负载差(h)"]
    fifo_waiting = fifo_met.get("总等待时间(h)", 0.0)

    candidates = []
    for name, order in init_orders.items():
        name_hash = int(hashlib.md5(name.encode("utf-8")).hexdigest(), 16) % 100000
        opt = QuadraticSAOptimizer(features, parts, cfg, pp, seed=cfg.random_seed + name_hash)
        opt._eval_cache = shared_cache
        opt._focused_descent_iterations = 0  # quick 模式跳过 SA 后的 150 次定向精修
        opt.fifo_cmax = fifo_cmax
        opt.fifo_kit_span = fifo_kit
        opt.fifo_load_diff = fifo_load
        opt.fifo_waiting = fifo_waiting
        order_out, metrics, _stats = opt.optimize(
            order,
            max_iterations=max(1, int(iterations)),
            time_limit_seconds=max(1.0, float(time_limit)),
            verbose=False,
        )
        candidates.append((name, order_out, metrics))
        for arch_order, arch_met in opt.pareto_archive:
            candidates.append((f"{name}-archive", arch_order, arch_met))

    best_name, _best_order, best_met, tier = _select_best(candidates, fifo_cmax)
    return {
        "strategy": best_name,
        "tier": tier,
        "cmax_h": float(best_met["总完工时间(h)"]),
        "kit_span_h": float(best_met["加权平均齐套跨度(h)"]),
        "load_diff_h": float(best_met["切割负载差(h)"]),
    }


def _full_optimize(weights, iterations):
    """完整精扫：复用项目原有的多策略 SA+Tabu 管线。"""
    from multiprocessing import current_process

    from improved_optimizer import run_multi_strategy_inline
    from steel_schedule_model import ModelConfig, plate_features

    plates = _WORKER["plates"]
    parts = _WORKER["parts"]
    pp = _WORKER["pp"]
    speed_table = _WORKER["speed_table"]
    checks = _WORKER["checks"]

    cfg = ModelConfig()
    cfg.obj_weight_cmax = float(weights[0])
    cfg.obj_weight_kit = float(weights[1])
    cfg.obj_weight_load = float(weights[2])
    cfg = cfg.adapt_to_data(plates, parts, pp)

    features = plate_features(plates, parts, cfg, speed_table)
    parallel_workers = 1 if current_process().name != "MainProcess" else None
    result = run_multi_strategy_inline(
        plates,
        parts,
        features,
        cfg,
        pp,
        speed_table,
        checks,
        iterations=max(80, int(iterations)),
        parallel_workers=parallel_workers,
    )
    metrics = result["opt_metrics"]
    return {
        "strategy": result["strategy_name"],
        "tier": "完整管线",
        "cmax_h": float(metrics["总完工时间(h)"]),
        "kit_span_h": float(metrics["加权平均齐套跨度(h)"]),
        "load_diff_h": float(metrics["切割负载差(h)"]),
    }


def _full_quadratic_optimize(weights, iterations):
    """完整二次式扫描：调用 demo6 的 run_quadratic_optimization。"""
    from multiprocessing import current_process

    from pareto_optimizer import run_quadratic_optimization
    from steel_schedule_model import ModelConfig, plate_features

    plates = _WORKER["plates"]
    parts = _WORKER["parts"]
    pp = _WORKER["pp"]
    speed_table = _WORKER["speed_table"]
    checks = _WORKER["checks"]

    cfg = ModelConfig()
    cfg.obj_weight_cmax = float(weights[0])
    cfg.obj_weight_kit = float(weights[1])
    cfg.obj_weight_load = float(weights[2])
    cfg = cfg.adapt_to_data(plates, parts, pp)

    features = plate_features(plates, parts, cfg, speed_table)
    result = run_quadratic_optimization(
        plates,
        parts,
        features,
        cfg,
        pp,
        speed_table,
        checks,
        iterations=max(80, int(iterations)),
    )
    metrics = result["opt_metrics"]
    return {
        "strategy": result["strategy_name"],
        "tier": "完整二次管线",
        "cmax_h": float(metrics["总完工时间(h)"]),
        "kit_span_h": float(metrics["加权平均齐套跨度(h)"]),
        "load_diff_h": float(metrics["切割负载差(h)"]),
    }


def _quick_ga_lns_optimize(weights, iterations, time_limit, objective_type):
    """快速 GA+LNS 扫描：评价函数由 objective_type 决定。"""
    from ga_lns_optimizer import GeneticLNSOptimizer
    from improved_optimizer import SATabuOptimizer
    from steel_schedule_model import ModelConfig, plate_features

    plates = _WORKER["plates"]
    parts = _WORKER["parts"]
    pp = _WORKER["pp"]
    speed_table = _WORKER["speed_table"]

    cfg = ModelConfig()
    cfg.obj_weight_cmax = float(weights[0])
    cfg.obj_weight_kit = float(weights[1])
    cfg.obj_weight_load = float(weights[2])
    cfg = cfg.adapt_to_data(plates, parts, pp)

    features = plate_features(plates, parts, cfg, speed_table)
    init_orders = _build_init_orders(features, plates, speed_table, cfg)
    fifo_order = init_orders["FIFO"]

    fifo_builder = SATabuOptimizer(features, parts, cfg, pp, seed=cfg.random_seed)
    shared_cache: dict = {}
    fifo_builder._eval_cache = shared_cache
    _, fifo_met, _ = fifo_builder.schedule_from_order(fifo_order)
    fifo_cmax = fifo_met["总完工时间(h)"]
    fifo_kit = fifo_met["加权平均齐套跨度(h)"]
    fifo_load = fifo_met["切割负载差(h)"]
    fifo_waiting = fifo_met.get("总等待时间(h)", 0.0)

    opt = GeneticLNSOptimizer(
        features,
        parts,
        cfg,
        pp,
        seed=cfg.random_seed,
        objective_type=objective_type,
        parallel_workers=1,
        plates=plates,
        speed_table=speed_table,
    )
    opt._eval_cache = shared_cache
    opt.fifo_cmax = fifo_cmax
    opt.fifo_kit_span = fifo_kit
    opt.fifo_load_diff = fifo_load
    opt.fifo_waiting = fifo_waiting

    order_out, metrics, _stats = opt.optimize(
        fifo_order,
        max_iterations=max(1, int(iterations)),
        time_limit_seconds=max(1.0, float(time_limit)),
        verbose=False,
    )
    return {
        "strategy": "GA+LNS",
        "tier": "GA+LNS",
        "cmax_h": float(metrics["总完工时间(h)"]),
        "kit_span_h": float(metrics["加权平均齐套跨度(h)"]),
        "load_diff_h": float(metrics["切割负载差(h)"]),
    }


def _full_ga_lns_optimize(weights, iterations, objective_type):
    """完整 GA+LNS 扫描：调用 demo6 的 run_ga_lns_optimization。"""
    from multiprocessing import current_process

    from ga_lns_optimizer import run_ga_lns_optimization
    from steel_schedule_model import ModelConfig, plate_features

    plates = _WORKER["plates"]
    parts = _WORKER["parts"]
    pp = _WORKER["pp"]
    speed_table = _WORKER["speed_table"]
    checks = _WORKER["checks"]

    cfg = ModelConfig()
    cfg.obj_weight_cmax = float(weights[0])
    cfg.obj_weight_kit = float(weights[1])
    cfg.obj_weight_load = float(weights[2])
    cfg = cfg.adapt_to_data(plates, parts, pp)

    features = plate_features(plates, parts, cfg, speed_table)
    parallel_workers = 1 if current_process().name != "MainProcess" else None
    result = run_ga_lns_optimization(
        plates,
        parts,
        features,
        cfg,
        pp,
        speed_table,
        checks,
        iterations=max(80, int(iterations)),
        parallel_workers=parallel_workers,
        objective_type=objective_type,
    )
    metrics = result["opt_metrics"]
    return {
        "strategy": result["strategy_name"],
        "tier": "完整GA+LNS管线",
        "cmax_h": float(metrics["总完工时间(h)"]),
        "kit_span_h": float(metrics["加权平均齐套跨度(h)"]),
        "load_diff_h": float(metrics["切割负载差(h)"]),
    }


def _run_one(job):
    """单个 worker 执行的入口：按模式运行优化并返回一行结果。"""
    weights = job["weights"]
    sweep_x = job.get("sweep_x", "")
    x_weight = job.get("x_weight", weights[job.get("x_index", 0)])
    ratio = job.get("ratio", 1.0)
    started = time.time()
    try:
        if job["mode"] == "full":
            result = _full_optimize(weights, job["iterations"])
        else:
            result = _quick_optimize(weights, job["iterations"], job["time_limit"])
        return {
            "w_cmax": weights[0],
            "w_kit": weights[1],
            "w_load": weights[2],
            "cmax_h": result["cmax_h"],
            "kit_span_h": result["kit_span_h"],
            "load_diff_h": result["load_diff_h"],
            "strategy": _label_strategy(result["strategy"]),
            "tier": result["tier"],
            "elapsed_s": round(time.time() - started, 1),
            "error": "",
             "sweep_x": sweep_x,
             "x_weight": x_weight,
             "ratio": ratio,
        }
    except Exception as exc:
        return {
            "w_cmax": weights[0],
            "w_kit": weights[1],
            "w_load": weights[2],
            "cmax_h": "",
            "kit_span_h": "",
            "load_diff_h": "",
            "strategy": "",
            "tier": "",
            "elapsed_s": round(time.time() - started, 1),
            "error": f"错误：{exc!r}",
            "sweep_x": sweep_x,
            "x_weight": x_weight,
            "ratio": ratio,
        }


def _run_one_quadratic(job):
    """二次式扫描的 worker 入口，返回结构与 _run_one 一致。"""
    weights = job["weights"]
    sweep_x = job.get("sweep_x", "")
    x_weight = job.get("x_weight", weights[job.get("x_index", 0)])
    ratio = job.get("ratio", 1.0)
    started = time.time()
    try:
        if job["mode"] == "full":
            result = _full_quadratic_optimize(weights, job["iterations"])
        else:
            result = _quick_quadratic_optimize(weights, job["iterations"], job["time_limit"])
        return {
            "w_cmax": weights[0],
            "w_kit": weights[1],
            "w_load": weights[2],
            "cmax_h": result["cmax_h"],
            "kit_span_h": result["kit_span_h"],
            "load_diff_h": result["load_diff_h"],
            "strategy": _label_strategy(result["strategy"]),
            "tier": result["tier"],
            "elapsed_s": round(time.time() - started, 1),
            "error": "",
            "sweep_x": sweep_x,
            "x_weight": x_weight,
            "ratio": ratio,
        }
    except Exception as exc:
        return {
            "w_cmax": weights[0],
            "w_kit": weights[1],
            "w_load": weights[2],
            "cmax_h": "",
            "kit_span_h": "",
            "load_diff_h": "",
            "strategy": "",
            "tier": "",
            "elapsed_s": round(time.time() - started, 1),
            "error": f"错误：{exc!r}",
            "sweep_x": sweep_x,
            "x_weight": x_weight,
            "ratio": ratio,
        }


def _run_one_ga(job):
    """GA+LNS 扫描的 worker 入口，返回结构与 _run_one 一致。"""
    weights = job["weights"]
    sweep_x = job.get("sweep_x", "")
    x_weight = job.get("x_weight", weights[job.get("x_index", 0)])
    ratio = job.get("ratio", 1.0)
    started = time.time()
    objective_type = "quadratic" if job.get("sweep_kind") == "ga_quadratic" else "linear"
    try:
        if job["mode"] == "full":
            result = _full_ga_lns_optimize(weights, job["iterations"], objective_type)
        else:
            result = _quick_ga_lns_optimize(weights, job["iterations"], job["time_limit"], objective_type)
        return {
            "w_cmax": weights[0],
            "w_kit": weights[1],
            "w_load": weights[2],
            "cmax_h": result["cmax_h"],
            "kit_span_h": result["kit_span_h"],
            "load_diff_h": result["load_diff_h"],
            "strategy": _label_strategy(result["strategy"]),
            "tier": result["tier"],
            "elapsed_s": round(time.time() - started, 1),
            "error": "",
            "sweep_x": sweep_x,
            "x_weight": x_weight,
            "ratio": ratio,
        }
    except Exception as exc:
        return {
            "w_cmax": weights[0],
            "w_kit": weights[1],
            "w_load": weights[2],
            "cmax_h": "",
            "kit_span_h": "",
            "load_diff_h": "",
            "strategy": "",
            "tier": "",
            "elapsed_s": round(time.time() - started, 1),
            "error": f"错误：{exc!r}",
            "sweep_x": sweep_x,
            "x_weight": x_weight,
            "ratio": ratio,
        }


def _run_one_dispatcher(job):
    """按扫描方式选择 worker：覆盖 SA+Tabu / GA+LNS x 线性 / 二次评价。"""
    sweep_kind = job.get("sweep_kind")
    if sweep_kind in ("ga_linear", "ga_quadratic"):
        return _run_one_ga(job)
    if sweep_kind in ("quadratic", "sa_quadratic"):
        return _run_one_quadratic(job)
    return _run_one(job)


def _ratio_sequence():
    """返回另两个指标比值的遍历序列：3, 2.8, ..., 1.2, 1, 0.95, ..., 0.1。"""
    ratios = [round(3.0 - 0.2 * i, 2) for i in range(10)]
    ratios += [round(1.0 - 0.05 * i, 2) for i in range(19)]
    return ratios


def _sweep_combos(x_index: int, step: float):
    """按主轴权重 + 另两个指标比值生成权重组合。

    主轴权重从 0.02 到 0.98 步进 step；另两个指标权重满足
    w_next + w_last = 1 - w_x，且 w_next / w_last = ratio。
    """
    ratios = _ratio_sequence()
    n_x = round((0.98 - 0.02) / step) + 1
    combos = []
    for i in range(n_x):
        x_weight = round(0.02 + i * step, 4)
        rest = 1.0 - x_weight
        for ratio in ratios:
            w_next = round(rest * ratio / (1.0 + ratio), 4)
            w_last = round(1.0 - x_weight - w_next, 4)
            weights = [0.0, 0.0, 0.0]
            weights[x_index] = x_weight
            weights[(x_index + 1) % 3] = w_next
            weights[(x_index + 2) % 3] = w_last
            combos.append({
                "weights": tuple(weights),
                "x_index": x_index,
                "x_weight": weights[x_index],
                "ratio": ratio,
            })
    return combos


def _weight_grid(step):
    """生成权重网格：三个权重都大于等于 step，且总和为 1。"""
    step = round(float(step), 6)
    if step <= 0 or step >= 1.0 / 3.0:
        raise ValueError("step 必须在 (0, 1/3) 范围内")
    n = round(1.0 / step)
    if abs(n * step - 1.0) > 1e-9:
        raise ValueError("1/step 必须是整数，例如 0.05、0.02、0.01")

    combos = set()
    for a in range(1, n - 1):
        for b in range(1, n - a):
            c = n - a - b
            combos.add((round(a * step, 6), round(b * step, 6), round(c * step, 6)))
    return combos


def _random_combos(count, seed):
    """随机补充更多权重组合，覆盖网格没有覆盖到的区域。"""
    rng = random.Random(seed)
    combos = set()
    attempts = 0
    while len(combos) < count and attempts < count * 20:
        attempts += 1
        vals = [rng.uniform(0.05, 0.90) for _ in range(3)]
        total = sum(vals)
        w0 = round(vals[0] / total, 3)
        w1 = round(vals[1] / total, 3)
        w2 = round(1.0 - w0 - w1, 3)
        if w2 < 0:
            continue
        combos.add((w0, w1, w2))
    return combos


def _objective(cmax, kit, load, w_cmax, w_kit, w_load):
    """按指定权重计算归一化目标值。"""
    return (
        w_cmax * cmax / 67.25
        + w_kit * kit / 12.7
        + w_load * load / 1.0
    )


def _pareto_frontier(rows):
    """找出所有不被其他结果支配的帕累托最优行。"""
    pts = [(r["cmax_h"], r["kit_span_h"], r["load_diff_h"]) for r in rows]
    front = []
    for i, p in enumerate(pts):
        dominated = False
        for j, q in enumerate(pts):
            if i == j:
                continue
            if (
                q[0] <= p[0]
                and q[1] <= p[1]
                and q[2] <= p[2]
                and (q[0] < p[0] or q[1] < p[1] or q[2] < p[2])
            ):
                dominated = True
                break
        if not dominated:
            front.append(i)
    return front


def _write_partial_row(path: Path, row: dict, columns: list[str]):
    """把一行结果追加写入 CSV（表头使用中文）。"""
    with path.open("a", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=[CSV_LABELS.get(col, col) for col in columns], extrasaction="ignore")
        writer.writerow(_label_row(row, columns))


def _write_csv(path: Path, rows: list[dict], columns: list[str]):
    """把完整结果写入 CSV（表头使用中文）。"""
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=[CSV_LABELS.get(col, col) for col in columns], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(_label_row(row, columns) for row in rows)


def _build_summary(rows, pareto_rows, out_path: Path):
    """生成中文 Markdown 摘要。"""
    lines = ["# 权重扫描结果", ""]
    ok_rows = [r for r in rows if r.get("error") == ""]
    lines.append(f"- 提交组合：{len(rows)}")
    lines.append(f"- 成功完成：{len(ok_rows)}")
    lines.append(f"- 帕累托非支配组合：{len(pareto_rows)}")
    lines.append("")

    lines.append("## 默认 0.40/0.40/0.20 口径下表现最好的 10 个组合")
    lines.append("")
    lines.append("| 总完工时间权重 | 齐套跨度权重 | 负载差权重 | 总完工时间(h) | 加权平均齐套跨度(h) | 切割负载差(h) | 默认口径目标值 |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in ok_rows[:10]:
        lines.append(
            f"| {r['w_cmax']:.3f} | {r['w_kit']:.3f} | {r['w_load']:.3f} "
            f"| {r['cmax_h']:.2f} | {r['kit_span_h']:.2f} | {r['load_diff_h']:.2f} "
            f"| {r['objective_default']:.3f} |"
        )
    lines.append("")

    lines.append("## 帕累托最优组合")
    lines.append("")
    lines.append("| 总完工时间权重 | 齐套跨度权重 | 负载差权重 | 总完工时间(h) | 加权平均齐套跨度(h) | 切割负载差(h) | 默认口径目标值 |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in pareto_rows:
        lines.append(
            f"| {r['w_cmax']:.3f} | {r['w_kit']:.3f} | {r['w_load']:.3f} "
            f"| {r['cmax_h']:.2f} | {r['kit_span_h']:.2f} | {r['load_diff_h']:.2f} "
            f"| {r['objective_default']:.3f} |"
        )
    lines.append("")
    lines.append(f"详细结果：`{out_path / '权重扫描对比表.csv'}`")
    out_path.joinpath("权重扫描摘要.md").write_text("\n".join(lines), encoding="utf-8")


def _setup_chinese_font() -> bool:
    """注册系统中文字体，避免 matplotlib 图片里的中文变成方框。"""
    import os as _os

    try:
        from matplotlib import font_manager, rcParams
    except ImportError:
        return False

    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        r"/System/Library/Fonts/PingFang.ttc",
        r"/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        r"/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        if _os.path.exists(path):
            try:
                font_manager.fontManager.addfont(path)
                name = font_manager.FontProperties(fname=path).get_name()
                rcParams["font.family"] = "sans-serif"
                rcParams["font.sans-serif"] = [name] + list(rcParams.get("font.sans-serif", []))
                rcParams["axes.unicode_minus"] = False
                return True
            except Exception:
                continue
    return False


def _snap_to_step(value, step: float) -> float:
    """把实际 x 值吸附到最近的遍历步长上，避免 0.0199/0.0201 拆成两个坐标。"""
    step = float(step)
    if step <= 0:
        return round(float(value), 6)
    snapped = round(float(value) / step) * step
    return round(snapped, 10)


def _plot_3d_surface(rows, x_label: str, z_label: str, z_key: str, out_path: Path, step: float) -> bool:
    """绘制单张 3D 图：x=主轴权重，y=另两个指标比值，z=指标实际值。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("未安装 matplotlib，跳过 3D 绘图。可执行：pip install matplotlib")
        return False
    _setup_chinese_font()

    filtered = [
        r for r in rows
        if r.get("sweep_x") == x_label
        and not r.get("error")
        and r.get(z_key) not in (None, "")
    ]
    if not filtered:
        return False

    value_map = {}
    for r in filtered:
        value_map[(_snap_to_step(r["x_weight"], step), float(r["ratio"]))] = float(r[z_key])

    x_vals = sorted({_snap_to_step(r["x_weight"], step) for r in filtered})
    y_vals = sorted({float(r["ratio"]) for r in filtered})
    X, Y = np.meshgrid(x_vals, y_vals)
    Z = np.array([
        [value_map.get((x, y), np.nan) for x in x_vals]
        for y in y_vals
    ])

    fig = plt.figure(figsize=(11, 8))
    ax = fig.add_subplot(111, projection="3d")
    ax.plot_surface(X, Y, Z, cmap="viridis", linewidth=0, antialiased=True, alpha=0.95)
    ax.scatter(X, Y, Z, s=6, color="black", alpha=0.25)
    ax.set_xlabel(f"{x_label}")
    ax.set_ylabel(RATIO_LABELS.get(x_label, "另两个指标比值"))
    ax.set_zlabel(z_label)
    ax.set_title(f"{x_label} 扫描 | z = {z_label}")

    plot_dir = out_path / "3D图"
    plot_dir.mkdir(parents=True, exist_ok=True)
    filename = f"3D_{x_label}_{z_label}.png"
    fig.savefig(plot_dir / filename, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return True


def _plot_heatmap(rows, z_label: str, z_key: str, out_path: Path, step: float, threshold_pct: float = 10.0) -> bool:
    """绘制单张 10000×10000 像素二维热力图。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("未安装 matplotlib，跳过 2D 热力图。可执行：pip install matplotlib")
        return False
    _setup_chinese_font()

    filtered = [
        r for r in rows
        if r.get("sweep_x") == SWEEP_INDICATORS[0]
        and not r.get("error")
        and r.get(z_key) not in (None, "")
    ]
    if not filtered:
        return False

    value_map = {}
    for r in filtered:
        value_map[(_snap_to_step(r["x_weight"], step), float(r["ratio"]))] = float(r[z_key])

    x_vals = sorted({_snap_to_step(r["x_weight"], step) for r in filtered})
    y_vals = sorted({float(r["ratio"]) for r in filtered})
    Z = np.array([
        [value_map.get((x, y), np.nan) for x in x_vals]
        for y in y_vals
    ])

    dark_threshold = float(np.nanpercentile(Z, threshold_pct))
    Z_dark = np.where(Z <= dark_threshold, Z, np.nan)
    z_min = float(np.nanmin(Z_dark))
    z_max = float(np.nanmax(Z_dark))
    if z_max <= z_min:
        z_max = z_min + max(1e-9, abs(z_min) * 1e-9)

    fig = plt.figure(figsize=(100, 100), dpi=100)
    ax = fig.add_subplot(111)
    cmap = plt.get_cmap("RdYlGn")
    cmap.set_bad("white")
    im = ax.imshow(
        Z_dark,
        origin="lower",
        aspect="auto",
        extent=[x_vals[0], x_vals[-1], y_vals[0], y_vals[-1]],
        cmap=cmap,
        vmin=z_min,
        vmax=z_max,
        interpolation="nearest",
    )
    ax.set_xlabel("总完工时间权重", fontsize=160)
    ax.set_ylabel(RATIO_LABELS.get(SWEEP_INDICATORS[0], "另两个指标比值"), fontsize=160)
    ax.tick_params(axis="x", labelsize=100, rotation=0)
    ax.tick_params(axis="y", labelsize=100)
    ax.set_title(f"2D 热力图 | z = {z_label} | 仅第 {threshold_pct:.0f}% 以下", fontsize=200, pad=60)

    cbar = fig.colorbar(im, ax=ax, fraction=0.02, pad=0.02)
    cbar.ax.tick_params(labelsize=90)
    cbar.set_label(z_label, fontsize=140)

    plot_dir = out_path / "2D热力图"
    plot_dir.mkdir(parents=True, exist_ok=True)
    filename = f"2D热力图_{z_label}.png"
    fig.savefig(plot_dir / filename, dpi=100)
    plt.close(fig)
    return True


def _plot_heatmaps(rows, out_path: Path, step: float, threshold_pct: float = 10.0):
    """生成 3 张二维热力图，每张对应一个实际指标。"""
    print("")
    for z_label, z_key in Z_METRICS:
        ok = _plot_heatmap(rows, z_label, z_key, out_path, step, threshold_pct)
        if ok:
            print(f"  2D 热力图已生成：{z_label}")
    plot_dir = out_path / "2D热力图"
    print(f"2D 热力图目录：{plot_dir}")


def _plot_all_3d(rows, out_path: Path, step: float):
    """生成 3 张图：总完工时间权重 × 3 个 z 轴实际指标。"""
    print("")
    x_label = SWEEP_INDICATORS[0]
    for z_label, z_key in Z_METRICS:
        ok = _plot_3d_surface(rows, x_label, z_label, z_key, out_path, step)
        if ok:
            print(f"  3D 图已生成：{x_label} × {z_label}")
    plot_dir = out_path / "3D图"
    print(f"3D 图目录：{plot_dir}")


def _read_partial_rows(partial_path: Path, columns: list[str]) -> list[dict]:
    """读取已保存的部分结果，并把数值列转换回 float。"""
    rows = []
    with partial_path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            row = _decode_row(row, columns)
            for key in ("w_cmax", "w_kit", "w_load", "cmax_h", "kit_span_h", "load_diff_h", "elapsed_s", "x_weight", "ratio"):
                if row.get(key) not in ("", None):
                    row[key] = float(row[key])
            row["objective_own"] = float(row["objective_own"]) if row.get("objective_own") not in ("", None) else None
            row["objective_default"] = (
                float(row["objective_default"]) if row.get("objective_default") not in ("", None) else None
            )
            row["meets_targets"] = (
                str(row["meets_targets"]) if row.get("meets_targets") not in ("", None) else ""
            )
            rows.append(row)
    return rows


def _find_common_area(rows, out_path: Path, threshold_pct: float = 10.0):
    """找出三张热力图中都偏深色的公共区域。

    默认以“值越低颜色越深”为口径，对每个指标取 threshold_pct 分位数作为
    深色阈值，三个指标同时低于各自阈值的组合即为公共深色区域。
    """
    import numpy as np

    metric_keys = [
        ("总完工时间(h)", "cmax_h"),
        ("加权平均齐套跨度(h)", "kit_span_h"),
        ("切割负载差(h)", "load_diff_h"),
    ]
    data = []
    for r in rows:
        if r.get("error"):
            continue
        try:
            cmax = float(r["cmax_h"])
            kit = float(r["kit_span_h"])
            load = float(r["load_diff_h"])
        except (TypeError, ValueError):
            continue
        data.append({
            "x": float(r["x_weight"]),
            "ratio": float(r["ratio"]),
            "cmax_h": cmax,
            "kit_span_h": kit,
            "load_diff_h": load,
        })

    if not data:
        print("没有可用于分析的指标数据")
        return

    thresholds = {}
    for label, key in metric_keys:
        values = np.array([d[key] for d in data])
        thresholds[key] = float(np.percentile(values, threshold_pct))

    rows_out = []
    for d in data:
        flags = {
            "cmax_h": d["cmax_h"] <= thresholds["cmax_h"],
            "kit_span_h": d["kit_span_h"] <= thresholds["kit_span_h"],
            "load_diff_h": d["load_diff_h"] <= thresholds["load_diff_h"],
        }
        rows_out.append({
            "主轴权重": d["x"],
            "另两个指标比值": d["ratio"],
            "总完工时间(h)": d["cmax_h"],
            "加权平均齐套跨度(h)": d["kit_span_h"],
            "切割负载差(h)": d["load_diff_h"],
            "总完工深色": "是" if flags["cmax_h"] else "否",
            "齐套深色": "是" if flags["kit_span_h"] else "否",
            "负载差深色": "是" if flags["load_diff_h"] else "否",
            "深色指标数": sum(flags.values()),
            "公共深色区域": "是" if all(flags.values()) else "否",
        })

    out_csv = out_path / "公共深色区域.csv"
    with out_csv.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows_out[0].keys()))
        writer.writeheader()
        writer.writerows(rows_out)

    common = [r for r in rows_out if r["公共深色区域"] == "是"]
    common.sort(key=lambda r: r["总完工时间(h)"] + r["加权平均齐套跨度(h)"] + r["切割负载差(h)"])

    print("")
    print(f"公共深色区域分析（阈值：各指标第 {threshold_pct:.0f} 百分位）")
    print(f"  Cmax 深色阈值：{thresholds['cmax_h']:.3f} h")
    print(f"  齐套深色阈值：{thresholds['kit_span_h']:.3f} h")
    print(f"  负载差深色阈值：{thresholds['load_diff_h']:.3f} h")
    print(f"  公共深色组合数：{len(common)} / {len(rows_out)}")
    if common:
        xs = [r["主轴权重"] for r in common]
        rs = [r["另两个指标比值"] for r in common]
        print(f"  主轴权重范围：{min(xs):.2f} ~ {max(xs):.2f}")
        print(f"  比值范围：{min(rs):.2f} ~ {max(rs):.2f}")
        print("  表现最好的 10 个公共深色组合：")
        for r in common[:10]:
            print(
                f"    x={r['主轴权重']:.2f}, ratio={r['另两个指标比值']:.2f}, "
                f"Cmax={r['总完工时间(h)']:.3f}, Kit={r['加权平均齐套跨度(h)']:.3f}, "
                f"Load={r['切割负载差(h)']:.3f}"
            )
    print(f"  完整结果：{out_csv}")


SWEEP_KIND_META = {
    "sa_linear": ("SA+Tabu", "线性评价"),
    "sa_quadratic": ("SA+Tabu", "二次非线性评价"),
    "ga_linear": ("GA+LNS", "线性评价"),
    "ga_quadratic": ("GA+LNS", "二次非线性评价"),
}

SWEEP_KIND_ALIASES = {
    "1": "sa_linear",
    "2": "sa_quadratic",
    "3": "ga_linear",
    "4": "ga_quadratic",
    "linear": "sa_linear",
    "quadratic": "sa_quadratic",
    "sa_linear": "sa_linear",
    "sa_quadratic": "sa_quadratic",
    "ga_linear": "ga_linear",
    "ga_quadratic": "ga_quadratic",
}


def _resolve_sweep_kind(value) -> str:
    """把命令行或交互输入转换成内部四种扫描模式之一。"""
    key = str(value).strip().lower()
    return SWEEP_KIND_ALIASES.get(key, "sa_linear")


def _run_all_sweeps(args) -> None:
    """输入5时按顺序运行 1->2->3->4，每种模式独立输出目录。"""
    if args.plot_only:
        print("错误：--plot-only 不能与模式5一起使用，请先运行完整扫描")
        sys.exit(2)

    kind_dirs = {
        "1": "weight_sweep_sa_linear",
        "2": "weight_sweep_sa_quadratic",
        "3": "weight_sweep_ga_linear",
        "4": "weight_sweep_ga_quadratic",
    }
    script = str(Path(__file__).resolve())
    for kind, label in kind_dirs.items():
        print(f"\n{'=' * 60}")
        print(f"开始按顺序运行模式 {kind}：{SWEEP_KIND_META[_resolve_sweep_kind(kind)][0]} + {SWEEP_KIND_META[_resolve_sweep_kind(kind)][1]}")
        print("=" * 60)

        cmd = [sys.executable, script, "--sweep-type", kind]
        cmd += ["--input", str(args.input)]
        cmd += ["--speed-table", str(args.speed_table)]
        cmd += ["--mode", args.mode]
        cmd += ["--workers", str(args.workers)]
        cmd += ["--iterations", str(args.iterations)]
        cmd += ["--time-limit", str(args.time_limit)]
        cmd += ["--step", str(args.step)]
        if args.limit > 0:
            cmd += ["--limit", str(args.limit)]
        if args.force:
            cmd.append("--force")
        if args.find_common:
            cmd.append("--find-common")
            cmd += ["--common-threshold", str(args.common_threshold)]
        if args.refine:
            cmd.append("--refine")
            cmd += ["--refine-iterations", str(args.refine_iterations)]
        if args.output:
            cmd += ["--output", str(Path(args.output) / label)]

        result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
        if result.returncode != 0:
            print(f"\n模式 {kind} 运行失败，停止后续模式")
            sys.exit(result.returncode)
    print("\n全部完成：1->2->3->4 已按顺序运行")


def main():
    parser = argparse.ArgumentParser(description="并行扫描目标函数权重并输出帕累托最优组合")
    parser.add_argument("--input", default=str(DEFAULT_ATTACH2), help="附件2 Excel 路径")
    parser.add_argument("--speed-table", default=str(DEFAULT_ATTACH3), help="附件3 Excel 路径")
    parser.add_argument("--output", default=None, help="结果输出目录；默认按扫描方式自动区分")
    parser.add_argument("--step", type=float, default=0.02, help="主轴权重步长，默认 0.02")
    parser.add_argument("--iterations", type=int, default=60, help="每个初始策略的 SA 迭代次数")
    parser.add_argument("--time-limit", type=float, default=6.0, help="每个初始策略的秒数上限")
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 4, help="并行 worker 数，默认使用全部 CPU")
    parser.add_argument("--mode", choices=["quick", "full"], default="quick", help="quick=轻量扫描，full=完整多策略管线")
    parser.add_argument(
        "--sweep-type",
        choices=["1", "2", "3", "4", "5", "linear", "quadratic", "sa_linear", "sa_quadratic", "ga_linear", "ga_quadratic"],
        default=None,
        help="1/2/3/4/5（5=按顺序全部跑）或 sa_linear/sa_quadratic/ga_linear/ga_quadratic",
    )
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 个组合，0 表示全部")
    parser.add_argument("--force", action="store_true", help="忽略已有 partial 结果，重新运行")
    parser.add_argument("--plot-only", action="store_true", help="只用已有 partial 结果重新出图，不跑模型")
    parser.add_argument("--find-common", action="store_true", help="分析三张热力图的公共深色区域")
    parser.add_argument("--common-threshold", type=float, default=10.0, help="公共深色分析的分位数阈值，默认 10")
    parser.add_argument("--refine", action="store_true", help="扫描后用完整管线复验帕累托组合")
    parser.add_argument("--refine-iterations", type=int, default=260, help="复验时的迭代次数")
    args = parser.parse_args()

    if args.sweep_type is None:
        print("请选择进化算法与评价函数：")
        print("  1 = SA+Tabu + 线性评价")
        print("  2 = SA+Tabu + 二次非线性评价")
        print("  3 = GA+LNS + 线性评价")
        print("  4 = GA+LNS + 二次非线性评价")
        print("  5 = 按顺序运行 1 -> 2 -> 3 -> 4")
        raw = input("请输入数字（默认 1）：").strip().lower()
        args.sweep_type = raw if raw in ("1", "2", "3", "4", "5") else "1"

    if str(args.sweep_type).strip().lower() in ("5", "all"):
        _run_all_sweeps(args)
        return

    sweep_kind = _resolve_sweep_kind(args.sweep_type)
    algorithm_label, objective_label = SWEEP_KIND_META[sweep_kind]
    print(f"扫描方式：算法={algorithm_label}，评价函数={objective_label}")
    if not args.output:
        default_dir = f"weight_sweep_{sweep_kind}"
        args.output = str(PROJECT_ROOT / "outputs" / default_dir)

    input_path = Path(args.input)
    speed_path = Path(args.speed_table)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        parser.error(f"找不到附件2：{input_path}")
    if not speed_path.exists():
        parser.error(f"找不到附件3：{speed_path}")

    x_index = 0
    x_label = SWEEP_INDICATORS[x_index]
    combos = []
    for item in _sweep_combos(x_index, args.step):
        combos.append({
            "weights": item["weights"],
            "sweep_x": x_label,
            "x_index": x_index,
            "x_weight": item["x_weight"],
            "ratio": item["ratio"],
            "sweep_kind": sweep_kind,
            "mode": args.mode,
            "iterations": args.iterations,
            "time_limit": args.time_limit,
        })

    if args.limit > 0:
        combos = combos[: args.limit]

    columns = INTERNAL_COLUMNS
    partial_path = out_dir / "权重扫描部分结果.csv"

    if args.plot_only:
        if not partial_path.exists():
            parser.error(f"找不到已有结果：{partial_path}")
        with partial_path.open("r", encoding="utf-8-sig", newline="") as fh:
            headers = set(csv.DictReader(fh).fieldnames or [])
        required_headers = {CSV_LABELS["sweep_x"], CSV_LABELS["x_weight"], CSV_LABELS["ratio"]}
        if not required_headers.issubset(headers):
            parser.error("现有 partial 结果不是新版本生成的，无法直接出图；请删除后重新扫描或加 --force")
        rows = _read_partial_rows(partial_path, columns)
        ok_rows = [r for r in rows if not r.get("error")]
        ok_rows.sort(key=lambda r: (r["objective_default"] if r["objective_default"] is not None else float("inf")))
        _write_csv(out_dir / "权重扫描对比表.csv", rows, columns)
        pareto_idx = _pareto_frontier(ok_rows)
        pareto_rows = [ok_rows[i] for i in pareto_idx]
        pareto_rows.sort(key=lambda r: r["objective_default"])
        _write_csv(out_dir / "帕累托最优组合.csv", pareto_rows, columns)
        _build_summary(ok_rows, pareto_rows, out_dir)
        _plot_all_3d(ok_rows, out_dir, args.step)
        _plot_heatmaps(ok_rows, out_dir, args.step, args.common_threshold)
        if args.find_common:
            _find_common_area(ok_rows, out_dir, args.common_threshold)
        print("")
        print("已完成：仅使用已有结果重新生成图表")
        return

    finished_keys = set()
    if partial_path.exists() and not args.force:
        with partial_path.open("r", encoding="utf-8-sig", newline="") as fh:
            headers = set(csv.DictReader(fh).fieldnames or [])
        required_headers = {CSV_LABELS["sweep_x"], CSV_LABELS["x_weight"], CSV_LABELS["ratio"]}
        if required_headers.issubset(headers):
            with partial_path.open("r", encoding="utf-8-sig", newline="") as fh:
                for row in csv.DictReader(fh):
                    row = _decode_row(row, columns)
                    if not row.get("error"):
                        finished_keys.add((
                            row.get("sweep_x", ""),
                            (float(row["w_cmax"]), float(row["w_kit"]), float(row["w_load"])),
                        ))
            print(f"发现已有 partial 结果，将跳过 {len(finished_keys)} 个已完成组合")
        else:
            print("检测到旧版 partial 结果，已自动清理，开始重新扫描")
            partial_path.unlink()
    elif partial_path.exists():
        partial_path.unlink()

    jobs = []
    for item in combos:
        key = (item["sweep_x"], item["weights"])
        if key in finished_keys:
            continue
        jobs.append(item)

    print(f"附件2：{input_path}")
    print(f"附件3：{speed_path}")
    print(f"组合数：{len(combos)}，本次需要运行：{len(jobs)}，workers={args.workers}")

    if jobs:
        import multiprocessing as mp

        if not partial_path.exists():
            with partial_path.open("w", newline="", encoding="utf-8-sig") as fh:
                csv.DictWriter(fh, fieldnames=[CSV_LABELS.get(col, col) for col in columns]).writeheader()

        started = time.time()
        with mp.Pool(
            processes=args.workers,
            initializer=_init_worker,
            initargs=(str(input_path), str(speed_path)),
            maxtasksperchild=6,
        ) as pool:
            for i, result in enumerate(pool.imap_unordered(_run_one_dispatcher, jobs, chunksize=1), 1):
                result["objective_own"] = ""
                result["objective_default"] = ""
                result["meets_targets"] = ""
                if not result.get("error"):
                    result["objective_own"] = round(
                        _objective(
                            float(result["cmax_h"]),
                            float(result["kit_span_h"]),
                            float(result["load_diff_h"]),
                            float(result["w_cmax"]),
                            float(result["w_kit"]),
                            float(result["w_load"]),
                        ),
                        4,
                    )
                    result["objective_default"] = round(
                        _objective(
                            float(result["cmax_h"]),
                            float(result["kit_span_h"]),
                            float(result["load_diff_h"]),
                            0.40,
                            0.40,
                            0.20,
                        ),
                        4,
                    )
                    result["meets_targets"] = (
                        "是"
                        if (
                            float(result["cmax_h"]) <= 67.25
                            and float(result["kit_span_h"]) <= 12.7
                            and float(result["load_diff_h"]) <= 1.0
                        )
                        else "否"
                    )
                _write_partial_row(partial_path, result, columns)
                done = i + len(finished_keys)
                elapsed = time.time() - started
                speed = elapsed / max(i, 1)
                remaining = speed * (len(jobs) - i)
                print(
                    f"  [{done}/{len(combos)}] "
                    f"主轴={result['sweep_x']} "
                    f"w=({result['w_cmax']:.2f},{result['w_kit']:.2f},{result['w_load']:.2f}) "
                    f"总完工={result['cmax_h']} 齐套={result['kit_span_h']} 负载差={result['load_diff_h']} "
                    f"{result.get('error', '')} | 预计剩余 {remaining / 60:.1f}min"
                )

    rows = []
    with partial_path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            row = _decode_row(row, columns)
            for key in ("w_cmax", "w_kit", "w_load", "cmax_h", "kit_span_h", "load_diff_h", "elapsed_s", "x_weight", "ratio"):
                if row.get(key) not in ("", None):
                    row[key] = float(row[key])
            row["objective_own"] = float(row["objective_own"]) if row.get("objective_own") not in ("", None) else None
            row["objective_default"] = (
                float(row["objective_default"]) if row.get("objective_default") not in ("", None) else None
            )
            row["meets_targets"] = (
                str(row["meets_targets"]) if row.get("meets_targets") not in ("", None) else ""
            )
            rows.append(row)

    ok_rows = [r for r in rows if not r.get("error")]
    ok_rows.sort(key=lambda r: (r["objective_default"] if r["objective_default"] is not None else float("inf")))
    _write_csv(out_dir / "权重扫描对比表.csv", rows, columns)

    pareto_idx = _pareto_frontier(ok_rows)
    pareto_rows = [ok_rows[i] for i in pareto_idx]
    pareto_rows.sort(key=lambda r: r["objective_default"])
    _write_csv(out_dir / "帕累托最优组合.csv", pareto_rows, columns)
    _build_summary(ok_rows, pareto_rows, out_dir)
    _plot_all_3d(ok_rows, out_dir, args.step)
    _plot_heatmaps(ok_rows, out_dir, args.step, args.common_threshold)
    if args.find_common:
        _find_common_area(ok_rows, out_dir, args.common_threshold)

    print("")
    print(f"完成：{len(ok_rows)}/{len(rows)} 个组合成功")
    print(f"对比表：{out_dir / '权重扫描对比表.csv'}")
    print(f"帕累托表：{out_dir / '帕累托最优组合.csv'}")
    print(f"摘要：{out_dir / '权重扫描摘要.md'}")

    if args.refine and pareto_rows:
        print("")
        print(f"开始用完整管线复验 {len(pareto_rows)} 个帕累托组合 ...")
        refine_columns = columns + ["mode"]
        refine_path = out_dir / "权重扫描复验结果.csv"
        with refine_path.open("w", newline="", encoding="utf-8-sig") as fh:
            csv.DictWriter(fh, fieldnames=[CSV_LABELS.get(col, col) for col in refine_columns]).writeheader()

        refine_jobs = [
            {
                "weights": (float(r["w_cmax"]), float(r["w_kit"]), float(r["w_load"])),
                "sweep_x": r.get("sweep_x", ""),
                "x_index": SWEEP_INDICATORS.index(r["sweep_x"]) if r.get("sweep_x") in SWEEP_INDICATORS else 0,
                "x_weight": float(r["x_weight"]),
                "ratio": float(r["ratio"]),
                "sweep_kind": sweep_kind,
                "mode": "full",
                "iterations": args.refine_iterations,
                "time_limit": 0.0,
            }
            for r in pareto_rows
        ]
        import multiprocessing as mp

        with mp.Pool(
            processes=args.workers,
            initializer=_init_worker,
            initargs=(str(input_path), str(speed_path)),
            maxtasksperchild=3,
        ) as pool:
            for result in pool.imap_unordered(_run_one_dispatcher, refine_jobs, chunksize=1):
                result["mode"] = {
                    "sa_linear": "完整SA+Tabu线性管线",
                    "sa_quadratic": "完整SA+Tabu二次管线",
                    "ga_linear": "完整GA+LNS线性管线",
                    "ga_quadratic": "完整GA+LNS二次管线",
                }.get(sweep_kind, "完整管线")
                result["objective_own"] = ""
                result["objective_default"] = ""
                result["meets_targets"] = ""
                if not result.get("error"):
                    result["objective_own"] = round(
                        _objective(
                            float(result["cmax_h"]),
                            float(result["kit_span_h"]),
                            float(result["load_diff_h"]),
                            float(result["w_cmax"]),
                            float(result["w_kit"]),
                            float(result["w_load"]),
                        ),
                        4,
                    )
                    result["objective_default"] = round(
                        _objective(
                            float(result["cmax_h"]),
                            float(result["kit_span_h"]),
                            float(result["load_diff_h"]),
                            0.40,
                            0.40,
                            0.20,
                        ),
                        4,
                    )
                    result["meets_targets"] = (
                        "是"
                        if (
                            float(result["cmax_h"]) <= 67.25
                            and float(result["kit_span_h"]) <= 12.7
                            and float(result["load_diff_h"]) <= 1.0
                        )
                        else "否"
                    )
                _write_partial_row(refine_path, result, refine_columns)
        print(f"完整复验结果：{refine_path}")


if __name__ == "__main__":
    main()
