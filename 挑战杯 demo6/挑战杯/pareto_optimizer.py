"""二次非线性标量评价函数 + SA+Tabu 包装。

不修改原有 SA+Tabu 主循环，只通过子类覆盖 kit_span_objective()，
把 4:4:2 线性加权替换成 FIFO 相对归一化的二次函数。
"""
from __future__ import annotations

import hashlib
import multiprocessing as mp
import os
import threading

from improved_optimizer import (
    SATabuOptimizer,
    _drain_parallel_progress,
    _init_parallel_worker,
    _run_parallel_start,
)
from steel_schedule_model import (
    ModelConfig,
    make_greedy_schedule,
    build_crane_aware_orders,
    unified_capacity_objective,
    select_best_by_capacity,
    legacy_balanced_objective,
    select_best_by_track,
)


def quadratic_objective(
    metrics: dict,
    cfg: ModelConfig,
    fifo_cmax: float | None = None,
    fifo_kit: float | None = None,
    fifo_load: float | None = None,
    fifo_waiting: float | None = None,
) -> float:
    """产能优先统一目标（P4-1），辅助指标使用二次形式。

    与线性评价共用同一结构 J = Cmax/LB + eps*Secondary，
    只有 Secondary 的平方项不同。
    """
    if getattr(cfg, "eval_track", "capacity") == "balanced":
        return legacy_balanced_objective(
            metrics,
            cfg,
            objective_type="quadratic",
            fifo_cmax=fifo_cmax,
            fifo_kit=fifo_kit,
            fifo_load=fifo_load,
        )
    fifo = {
        "加权平均齐套跨度(h)": fifo_kit,
        "切割负载差(h)": fifo_load,
        "总等待时间(h)": fifo_waiting,
    }
    return unified_capacity_objective(metrics, cfg, fifo=fifo, objective_type="quadratic")


class QuadraticSAOptimizer(SATabuOptimizer):
    """原样继承 SA+Tabu，只覆盖评价函数为二次标量式。"""

    def kit_span_objective(self, metrics: dict) -> float:
        return quadratic_objective(
            metrics,
            self.cfg,
            getattr(self, "fifo_cmax", None),
            getattr(self, "fifo_kit_span", None),
            getattr(self, "fifo_load_diff", None),
            getattr(self, "fifo_waiting", None),
        )

    def _select_from_pareto(self, fifo_cmax: float | None = None) -> tuple[list[str], dict]:
        """产能优先最终选解（P4-1）：先比 Cmax/LB，1%内再比辅助指标。"""
        if not self.pareto_archive:
            return [], {}
        candidates = [
            (f"archive{i}", o, m)
            for i, (o, m) in enumerate(self.pareto_archive)
        ]
        _name, best_order, best_met = select_best_by_track(
            candidates,
            self.cfg,
            objective_type="quadratic",
            fifo_cmax=getattr(self, "fifo_cmax", None),
            fifo_kit=getattr(self, "fifo_kit_span", None),
            fifo_load=getattr(self, "fifo_load_diff", None),
        )
        return best_order, best_met


def _build_init_orders(features, plates, speed_table, cfg):
    """复用原有 6 个初始策略。"""
    name_col = next(c for c in features.columns if "套料图名" in c or "plate" in c.lower())
    seg_col = next(c for c in features.columns if "分段号" in c or "segment" in c.lower())
    seq_col = next(c for c in features.columns if "序号" in c or "seq" in c.lower())
    cut_col = "切割工时(min)"
    pri_col = next(c for c in features.columns if "优先" in c or "最低" in c)

    orders = {}
    orders["FIFO"] = features.sort_values(seq_col, kind="stable")[name_col].tolist()

    comp_sched = make_greedy_schedule(features, "completeness", cfg.random_seed)
    comp_seq_col = next(c for c in comp_sched.columns if "序号" in c or "切割序号" in c)
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
    orders.update(build_crane_aware_orders(features, cfg))
    return orders


def run_quadratic_optimization(
    plates,
    parts,
    features,
    cfg: ModelConfig,
    pp,
    speed_table,
    checks: dict,
    iterations: int = 260,
    progress_callback: object = None,
    parallel_workers: int | None = None,
) -> dict:
    """多策略 SA+Tabu + 二次评价入口，返回结构与 run_multi_strategy_inline 一致。"""
    init_solutions = _build_init_orders(features, plates, speed_table, cfg)
    fifo_order = init_solutions["FIFO"]

    shared_cache: dict = {}
    fifo_builder = SATabuOptimizer(features, parts, cfg, pp, seed=cfg.random_seed)
    fifo_builder._eval_cache = shared_cache
    _, fifo_met, _ = fifo_builder.schedule_from_order(fifo_order)
    fifo_baseline_cmax = fifo_met["总完工时间(h)"]
    fifo_baseline_kit = fifo_met["加权平均齐套跨度(h)"]
    fifo_baseline_load = fifo_met["切割负载差(h)"]
    fifo_baseline_waiting = fifo_met.get("总等待时间(h)", 0.0)

    total_starts = len(init_solutions)
    iter_per_start = max(80, iterations // total_starts)
    time_per_start = max(30, 120 // total_starts)

    all_candidates = []
    all_stats = []

    if parallel_workers is None:
        _env_parallel = os.environ.get("OPTIMIZER_PARALLEL", "1").strip().lower()
        if _env_parallel in ("0", "false", "no", "off"):
            parallel_workers = 1
        else:
            parallel_workers = min(os.cpu_count() or 1, total_starts)
    if mp.current_process().name != "MainProcess":
        parallel_workers = 1  # 防止在已有 worker 进程内再次嵌套进程池
    parallel_workers = max(1, min(int(parallel_workers or 1), total_starts))

    if parallel_workers <= 1:
        for start_idx, (name, init_order) in enumerate(init_solutions.items()):
            name_hash = int(hashlib.md5(name.encode("utf-8")).hexdigest(), 16) % 100000
            opt = QuadraticSAOptimizer(features, parts, cfg, pp, seed=cfg.random_seed + name_hash)
            opt.fifo_cmax = fifo_baseline_cmax
            opt.fifo_kit_span = fifo_baseline_kit
            opt.fifo_load_diff = fifo_baseline_load
            opt.fifo_waiting = fifo_baseline_waiting
            opt._eval_cache = shared_cache

            def _wrap_cb(it, mx, cur, best, T, _si=start_idx, _ts=total_starts):
                if progress_callback:
                    progress_callback(_si * iter_per_start + it, _ts * iter_per_start, cur, best, T)

            order, metrics, stats = opt.optimize(
                init_order,
                max_iterations=iter_per_start,
                time_limit_seconds=time_per_start,
                verbose=False,
                progress_callback=_wrap_cb if progress_callback else None,
            )
            all_stats.append(f"  [{name}] {stats}")
            all_candidates.append((name, order, metrics))
            for arch_order, arch_met in opt.pareto_archive:
                all_candidates.append((f"{name}-archive", arch_order, arch_met))
    else:
        ctx = mp.get_context("spawn" if os.name == "nt" else "fork")
        progress_manager = mp.Manager() if progress_callback else None
        progress_queue = progress_manager.Queue() if progress_manager else None
        names = list(init_solutions.keys())
        pool = ctx.Pool(
            processes=parallel_workers,
            initializer=_init_parallel_worker,
            initargs=(
                features,
                parts,
                cfg,
                pp,
                fifo_baseline_cmax,
                fifo_baseline_kit,
                fifo_baseline_load,
                fifo_baseline_waiting,
                iter_per_start,
                time_per_start,
                progress_queue,
                QuadraticSAOptimizer,
            ),
        )

        progress_thread = None
        if progress_callback and progress_queue is not None:
            progress_thread = threading.Thread(
                target=_drain_parallel_progress,
                args=(progress_queue, names, iter_per_start, progress_callback),
                daemon=True,
            )
            progress_thread.start()

        try:
            results = pool.map(_run_parallel_start, list(init_solutions.items()), chunksize=1)
        finally:
            pool.close()
            pool.join()
            if progress_queue is not None:
                progress_queue.put(None)
            if progress_thread is not None:
                progress_thread.join(timeout=5)
            if progress_manager is not None:
                progress_manager.shutdown()

        for result in results:
            name = result["name"]
            all_stats.append(f"  [{name}] {result['stats']}")
            all_candidates.append((name, result["order"], result["metrics"]))
            for arch_order, arch_met in result["archive"]:
                all_candidates.append((f"{name}-archive", arch_order, arch_met))

    # P4-1：按 eval_track 选择最终解（产能优先 / 原三指标）
    best_name, best_order, _best_metrics = select_best_by_track(
        all_candidates,
        cfg,
        objective_type="quadratic",
        fifo_cmax=fifo_baseline_cmax,
        fifo_kit=fifo_baseline_kit,
        fifo_load=fifo_baseline_load,
    )
    tier_label = (
        "原三指标兼顾(平衡)"
        if getattr(cfg, "eval_track", "capacity") == "balanced"
        else "产能优先(Cmax/LB最小，1%内选辅助最优)"
    )

    builder = SATabuOptimizer(features, parts, cfg, pp, seed=cfg.random_seed)
    builder.fifo_cmax = fifo_baseline_cmax
    builder.fifo_kit_span = fifo_baseline_kit
    builder.fifo_load_diff = fifo_baseline_load
    builder.fifo_waiting = fifo_baseline_waiting

    opt_schedule, opt_metrics, opt_stages = builder.schedule_from_order(best_order)
    base_schedule, base_metrics, base_stages = builder.schedule_from_order(fifo_order)

    all_stats.insert(0, f"  [Selection] {tier_label}")
    print(f"[Quadratic] Best: {best_name}")
    for s in all_stats:
        print(s)
    print(
        f"[Quadratic] KitSpan: {base_metrics['加权平均齐套跨度(h)']:.2f}h "
        f"→ {opt_metrics['加权平均齐套跨度(h)']:.2f}h, "
        f"Cmax: {base_metrics['总完工时间(h)']:.2f}h → {opt_metrics['总完工时间(h)']:.2f}h"
    )

    return {
        "schedule": opt_schedule,
        "base_schedule": base_schedule,
        "base_metrics": base_metrics,
        "opt_metrics": opt_metrics,
        "stages": opt_stages,
        "strategy_name": best_name,
    }
