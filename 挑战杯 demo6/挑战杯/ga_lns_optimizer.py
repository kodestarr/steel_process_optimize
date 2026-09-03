"""GA + LNS 混合优化器。

保留“排程顺序 -> 仿真 -> 目标值”的黑盒评估，只替换搜索算法：
  - 染色体 = 钢板排列顺序
  - 适应度 = 线性评价或 FIFO 相对二次评价
  - 选择 = 锦标赛
  - 交叉 = OX / PMX 排列交叉
  - 变异 = 复用 SA+Tabu 的邻域算子
  - LNS = 毁坏 + 启发式修复，作为精英个体的局部精修
"""
from __future__ import annotations

import hashlib
import math
import multiprocessing as mp
import os
import random
import sys
import time
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).parent))

from improved_optimizer import SATabuOptimizer  # noqa: E402
from pareto_optimizer import _build_init_orders  # noqa: E402
from steel_schedule_model import (  # noqa: E402
    ModelConfig,
    build_crane_aware_orders,
    unified_capacity_objective,
    select_best_by_capacity,
    legacy_balanced_objective,
    select_best_by_track,
)


_GA_PARALLEL_STATE: dict = {}


def _ga_init_worker(
    features,
    parts,
    cfg,
    pp,
    fifo_cmax: float,
    fifo_kit: float,
    fifo_load: float,
    fifo_waiting: float,
    objective_type: str,
):
    """在每个并行子进程中加载一次评估器，worker 内的 eval cache 跨任务共享。"""
    _GA_PARALLEL_STATE.clear()
    builder = SATabuOptimizer(features, parts, cfg, pp, seed=cfg.random_seed)
    builder.fifo_cmax = fifo_cmax
    builder.fifo_kit_span = fifo_kit
    builder.fifo_load_diff = fifo_load
    builder._eval_cache = {}
    _GA_PARALLEL_STATE.update({
        "builder": builder,
        "cfg": cfg,
        "fifo_cmax": fifo_cmax,
        "fifo_kit": fifo_kit,
        "fifo_load": fifo_load,
        "fifo_waiting": fifo_waiting,
        "objective_type": objective_type,
    })


def _ga_eval_worker(order: list[str]) -> tuple[list[str], dict, float]:
    """在子进程中评估一个钢板排列，返回 (order, metrics, objective)。"""
    st = _GA_PARALLEL_STATE
    _, met, _ = st["builder"].schedule_from_order(order)
    if getattr(st["cfg"], "eval_track", "capacity") == "balanced":
        obj = legacy_balanced_objective(
            met,
            st["cfg"],
            objective_type=st["objective_type"],
            fifo_cmax=st.get("fifo_cmax"),
            fifo_kit=st.get("fifo_kit"),
            fifo_load=st.get("fifo_load"),
        )
        return order, met, obj
    fifo = {
        "加权平均齐套跨度(h)": st["fifo_kit"],
        "切割负载差(h)": st["fifo_load"],
        "总等待时间(h)": st.get("fifo_waiting"),
    }
    obj = unified_capacity_objective(
        met, st["cfg"], fifo=fifo, objective_type=st["objective_type"]
    )
    return order, met, obj


class GeneticLNSOptimizer(SATabuOptimizer):
    """GA 为主、LNS 做精英局部精修的混合优化器。"""

    def __init__(
        self,
        features,
        parts,
        cfg: ModelConfig,
        pp=None,
        seed: int = 20260723,
        objective_type: str = "linear",
        parallel_workers: int = 1,
        plates=None,
        speed_table=None,
    ):
        super().__init__(features, parts, cfg, pp, seed=seed)
        self.features = features
        self.plates = plates
        self.speed_table = speed_table
        self.objective_type = objective_type
        self.parallel_workers = max(1, int(parallel_workers or 1))
        self._pool = None
        self._seen_archive = set()
        self._metrics_cache: dict[str, dict] = {}

    # ── 分段成块辅助 ──────────────────────────────────────
    def _seg_of(self, plate_name: str) -> str:
        return str(self.fidx.loc[plate_name, self.seg_col])

    def _segment_priority_order(self) -> list[str]:
        segs = list(self.seg_map.keys())
        pri = self.fidx[self.pri_col]
        return sorted(
            segs,
            key=lambda s: (
                float(pri.loc[self.seg_map[s]].min()),
                float(pri.loc[self.seg_map[s]].mean()),
                str(s),
            ),
        )

    def _segment_block(self, order: list[str]) -> list[str]:
        seg_order = self._segment_priority_order()
        blocked: list[str] = []
        for s in seg_order:
            blocked.extend(p for p in order if self._seg_of(p) == s)
        return blocked

    def _segment_randomize(self, order: list[str]) -> list[str]:
        seg_order = self._segment_priority_order()
        blocked: list[str] = []
        for s in seg_order:
            plates = [p for p in order if self._seg_of(p) == s]
            self.rng.shuffle(plates)
            blocked.extend(plates)
        return blocked

    # ── 评估 ──────────────────────────────────────────────
    def _fitness(self, metrics: dict) -> float:
        if getattr(self.cfg, "eval_track", "capacity") == "balanced":
            return legacy_balanced_objective(
                metrics,
                self.cfg,
                objective_type=self.objective_type,
                fifo_cmax=getattr(self, "fifo_cmax", None),
                fifo_kit=getattr(self, "fifo_kit_span", None),
                fifo_load=getattr(self, "fifo_load_diff", None),
            )
        fifo = {
            "加权平均齐套跨度(h)": getattr(self, "fifo_kit_span", None),
            "切割负载差(h)": getattr(self, "fifo_load_diff", None),
            "总等待时间(h)": getattr(self, "fifo_waiting", None),
        }
        return unified_capacity_objective(
            metrics, self.cfg, fifo=fifo, objective_type=self.objective_type
        )

    def _eval_one(self, order: list[str]) -> tuple[list[str], dict, float]:
        order = self._enforce_first_plate(order)
        key = self._hash_order(order)
        met = self._metrics_cache.get(key)
        if met is None:
            _, met, _ = self.schedule_from_order(order)
            self._metrics_cache[key] = met
        return order, met, self._fitness(met)

    def _evaluate_many(self, orders: list[list[str]]) -> list[tuple[list[str], dict, float]]:
        orders = [self._enforce_first_plate(o) for o in orders]
        unique = []
        seen = set()
        for order in orders:
            key = self._hash_order(order)
            if key not in seen:
                seen.add(key)
                unique.append(order)
        if self._pool is None:
            results = [self._eval_one(o) for o in unique]
        else:
            results = self._pool.map(_ga_eval_worker, unique)
        for order, met, _obj in results:
            key = self._hash_order(order)
            self._metrics_cache[key] = met
            if key not in self._seen_archive:
                self._seen_archive.add(key)
                self._update_pareto_archive(order[:], met)
        return results

    # ── 初始种群 ──────────────────────────────────────────
    def _build_initial_population(self, initial_order: list[str]) -> list[list[str]]:
        pop_size = max(4, int(getattr(self.cfg, "ga_population_size", 16)))
        orders: list[list[str]] = []
        seen = set()

        heuristic_orders: list[list[str]] = []
        if self.plates is not None:
            heuristic_orders = list(_build_init_orders(
                self.features, self.plates, self.speed_table, self.cfg
            ).values())
        if initial_order:
            heuristic_orders.append(initial_order[:])

        for order in heuristic_orders:
            order = self._enforce_first_plate(self._segment_block(order))
            key = self._hash_order(order)
            if key not in seen:
                seen.add(key)
                orders.append(order[:])

        # 天车需求/缓冲能力分类调度池：保持交替结构直接进入初始种群
        for order in build_crane_aware_orders(self.features, self.cfg).values():
            order = self._enforce_first_plate(order)
            key = self._hash_order(order)
            if key not in seen:
                seen.add(key)
                orders.append(order[:])

        while len(orders) < pop_size:
            base = self.rng.choice(orders[: max(1, len(orders))])
            perm = self._enforce_first_plate(self._segment_randomize(base))
            key = self._hash_order(perm)
            if key not in seen:
                seen.add(key)
                orders.append(perm)
        return orders[:pop_size]

    # ── 选择 / 交叉 / 变异 ────────────────────────────────
    def _tournament(
        self, pop: list[tuple[list[str], dict, float]]
    ) -> tuple[list[str], dict, float]:
        k = min(max(2, int(getattr(self.cfg, "ga_tournament_size", 3))), len(pop))
        contenders = self.rng.sample(pop, k)
        return min(contenders, key=lambda x: x[2])

    def _ox_crossover(self, p1: list[str], p2: list[str]) -> list[str]:
        n = len(p1)
        a, b = sorted(self.rng.sample(range(n), 2))
        child: list[str | None] = [None] * n
        child[a:b] = p1[a:b]
        segment = set(p1[a:b])
        rest = [x for x in p2 if x not in segment]
        idx = 0
        for i in range(n):
            if child[i] is None:
                child[i] = rest[idx]
                idx += 1
        return [str(x) for x in child]

    def _pmx_crossover(self, p1: list[str], p2: list[str]) -> list[str]:
        return self._pmx_pair(p1, p2)[0]

    def _pmx_pair(self, p1: list[str], p2: list[str]) -> tuple[list[str], list[str]]:
        n = len(p1)
        a, b = sorted(self.rng.sample(range(n), 2))
        c1 = p1[:]
        c2 = p2[:]
        pos1 = {v: i for i, v in enumerate(c1)}
        pos2 = {v: i for i, v in enumerate(c2)}
        for j in range(a, b):
            t1 = c1[j]
            t2 = c2[j]
            i1 = pos1[t2]
            i2 = pos2[t1]
            c1[j], c1[i1] = t2, t1
            c2[j], c2[i2] = t1, t2
            pos1[t1], pos1[t2] = i1, j
            pos2[t1], pos2[t2] = j, i2
        return c1, c2

    def _crossover(self, p1: list[str], p2: list[str]) -> tuple[list[str], list[str]]:
        seg_order = self._segment_priority_order()
        c1: list[str] = []
        c2: list[str] = []
        for s in seg_order:
            s1 = [p for p in p1 if self._seg_of(p) == s]
            s2 = [p for p in p2 if self._seg_of(p) == s]
            if self.rng.random() < 0.5:
                a1, a2 = self._ox_crossover(s1, s2), self._ox_crossover(s2, s1)
            else:
                a1, a2 = self._pmx_pair(s1, s2)
            c1.extend(a1)
            c2.extend(a2)
        return c1, c2

    def _mutate(self, order: list[str], iteration: int) -> list[str]:
        neighbor, _op, _key = self._neighbor(order, iteration)
        return neighbor

    def _generate_offspring(
        self, pop: list[tuple[list[str], dict, float]], generation: int
    ) -> list[tuple[list[str], dict, float]]:
        pop_size = max(4, int(getattr(self.cfg, "ga_population_size", 16)))
        crossover_rate = float(getattr(self.cfg, "ga_crossover_rate", 0.85))
        mutation_rate = float(getattr(self.cfg, "ga_mutation_rate", 0.20))
        raw: list[list[str]] = []

        while len(raw) < pop_size:
            p1 = self._tournament(pop)
            p2 = self._tournament(pop)
            if self.rng.random() < crossover_rate:
                c1, c2 = self._crossover(p1[0], p2[0])
            else:
                c1, c2 = p1[0][:], p2[0][:]
            for child in (c1, c2):
                if self.rng.random() < mutation_rate:
                    child = self._mutate(child, generation + 1)
                raw.append(child)

        return self._evaluate_many(raw[:pop_size])

    # ── LNS ───────────────────────────────────────────────
    def _destroy(self, order: list[str]) -> tuple[list[str], list[str]]:
        n = len(order)
        ratio = float(getattr(self.cfg, "lns_destroy_ratio", 0.25))
        remove_count = max(3, int(n * ratio))
        if self.rng.random() < 0.5 and len(self.seg_map) >= 2:
            seg = self.rng.choice(list(self.seg_map.keys()))
            seg_plates = [p for p in order if p in set(self.seg_map[seg])]
            if len(seg_plates) >= 3:
                removed = self.rng.sample(seg_plates, min(len(seg_plates), remove_count))
                base = [p for p in order if p not in set(removed)]
                return base, removed
        removed = self.rng.sample(order, min(remove_count, n))
        removed_set = set(removed)
        return [p for p in order if p not in removed_set], removed

    def _repair_heuristic(self, base: list[str], removed: list[str]) -> list[str]:
        order = base[:]
        removed_order = removed[:]
        self.rng.shuffle(removed_order)
        for plate in removed_order:
            seg = str(self.fidx.loc[plate, self.seg_col])
            prio = self.fidx.loc[plate, self.pri_col]
            same_seg = [i for i, x in enumerate(order)
                        if str(self.fidx.loc[x, self.seg_col]) == seg]
            same_pri = [i for i, x in enumerate(order)
                        if self.fidx.loc[x, self.pri_col] == prio]
            if same_seg:
                pos = same_seg[len(same_seg) // 2]
            elif same_pri:
                pos = same_pri[len(same_pri) // 2]
            else:
                pos = self.rng.randint(0, len(order))
            order.insert(pos, plate)
        return self._segment_block(order)

    def _repair_random(self, base: list[str], removed: list[str]) -> list[str]:
        order = base[:]
        for plate in removed:
            seg = self._seg_of(plate)
            same_seg = [i for i, x in enumerate(order) if self._seg_of(x) == seg]
            if same_seg:
                pos = same_seg[self.rng.randint(0, len(same_seg) - 1)]
            else:
                pos = len(order)
            order.insert(pos, plate)
        return self._segment_block(order)

    def _lns_search(
        self,
        order: list[str],
        current_obj: float,
        t0: float,
        time_limit_seconds: float,
    ) -> tuple[list[str], dict, float]:
        best_order = order[:]
        best_met = self._eval_metrics(order)
        best_obj = current_obj
        lns_iterations = max(1, int(getattr(self.cfg, "lns_iterations", 4)))
        for i in range(lns_iterations):
            if time.time() - t0 > time_limit_seconds:
                break
            base, removed = self._destroy(best_order)
            candidates = [
                self._repair_heuristic(base, removed),
                self._repair_random(base, removed),
            ]
            for cand in candidates:
                met = self._eval_metrics(cand)
                obj = self._fitness(met)
                if obj < best_obj - 1e-9:
                    best_order = cand
                    best_met = met
                    best_obj = obj
                else:
                    temp = max(0.1, 5.0 * (1.0 - i / max(1, lns_iterations)))
                    if self.rng.random() < math.exp(-(obj - best_obj) / temp):
                        best_order = cand
                        best_met = met
                        best_obj = obj
        return best_order, best_met, best_obj

    def _eval_metrics(self, order: list[str]) -> dict:
        order = self._enforce_first_plate(order)
        key = self._hash_order(order)
        met = self._metrics_cache.get(key)
        if met is None:
            _, met, _ = self.schedule_from_order(order)
            self._metrics_cache[key] = met
        return met

    # ── 主循环 ────────────────────────────────────────────
    def optimize(
        self,
        initial_order: list[str],
        max_iterations: int = 500,
        time_limit_seconds: float = 300.0,
        verbose: bool = False,
        progress_callback: object = None,
    ) -> tuple[list[str], dict, str]:
        t0 = time.time()
        pop_size = max(4, int(getattr(self.cfg, "ga_population_size", 16)))
        generations = max(
            1,
            min(
                int(getattr(self.cfg, "ga_generations", 10)),
                max(1, int(max_iterations / max(1, pop_size // 2))),
            ),
        )

        self._pool = None
        if self.parallel_workers > 1 and mp.current_process().name == "MainProcess":
            ctx = mp.get_context("spawn" if os.name == "nt" else "fork")
            self._pool = ctx.Pool(
                processes=self.parallel_workers,
                initializer=_ga_init_worker,
                initargs=(
                    self.features,
                    self.parts,
                    self.cfg,
                    self.pp,
                    getattr(self, "fifo_cmax", None),
                    getattr(self, "fifo_kit_span", None),
                    getattr(self, "fifo_load_diff", None),
                    getattr(self, "fifo_waiting", None),
                    self.objective_type,
                ),
            )

        total_evals = 0
        try:
            population = self._build_initial_population(initial_order)
            population = self._evaluate_many(population)
            total_evals += len(population)
            population.sort(key=lambda x: x[2])
            best_order, best_met, best_obj = population[0]

            for gen in range(generations):
                if time.time() - t0 > time_limit_seconds:
                    break
                offspring = self._generate_offspring(population, gen)
                total_evals += len(offspring)
                merged = population + offspring
                merged.sort(key=lambda x: x[2])
                population = merged[:pop_size]

                # GA 为主，LNS 对精英个体做局部精修
                elite_count = max(2, pop_size // 4)
                lns_improved = 0
                for elite_order, _met, elite_obj in population[:elite_count]:
                    new_order, new_met, new_obj = self._lns_search(
                        elite_order, elite_obj, t0, time_limit_seconds
                    )
                    total_evals += 2  # LNS 每次迭代生成两个候选并评估
                    if new_obj < elite_obj - 1e-9:
                        lns_improved += 1
                        population.append((new_order, new_met, new_obj))
                        self._update_pareto_archive(new_order[:], new_met)
                population.sort(key=lambda x: x[2])
                population = population[:pop_size]

                if population[0][2] < best_obj - 1e-9:
                    best_order, best_met, best_obj = population[0]

                if progress_callback:
                    T = max(0.01, 15.0 * (0.992 ** (gen + 1)))
                    progress_callback(gen + 1, generations, population[0][2], best_obj, T)
                if verbose:
                    print(f"[GA+LNS] gen {gen+1}/{generations} best={best_obj:.4f}")
        finally:
            if self._pool is not None:
                self._pool.close()
                self._pool.join()
                self._pool = None

        final_order, final_met = self._select_final(best_order, best_met)
        elapsed = time.time() - t0
        stats = (
            f"GA+LNS: {generations} gens, {total_evals} evals, {elapsed:.1f}s | "
            f"Cmax: {best_met['总完工时间(h)']:.2f}h -> {final_met['总完工时间(h)']:.2f}h, "
            f"KitSpan: {best_met['加权平均齐套跨度(h)']:.2f}h -> {final_met['加权平均齐套跨度(h)']:.2f}h"
        )
        return final_order, final_met, stats

    def _select_final(
        self, best_order: list[str], best_met: dict
    ) -> tuple[list[str], dict]:
        # P4-1：产能优先最终选解，1%容差内再比辅助指标
        candidates = [
            (f"archive{i}", o, m)
            for i, (o, m) in enumerate(self.pareto_archive)
        ]
        if not candidates:
            return best_order, best_met
        _name, final_order, final_met = select_best_by_track(
            candidates,
            self.cfg,
            objective_type=self.objective_type,
            fifo_cmax=getattr(self, "fifo_cmax", None),
            fifo_kit=getattr(self, "fifo_kit_span", None),
            fifo_load=getattr(self, "fifo_load_diff", None),
        )
        return final_order, final_met


def run_ga_lns_optimization(
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
    objective_type: str = "linear",
) -> dict:
    """GA+LNS 入口，返回结构与 run_multi_strategy_inline 一致。"""
    name_col = next(c for c in features.columns if "套料图名" in c or "plate" in c.lower())
    seq_col = next(c for c in features.columns if "序号" in c or "seq" in c.lower())
    seg_col = next(c for c in features.columns if "分段号" in c)
    fifo_order = features.sort_values(seq_col, kind="stable")[name_col].tolist()
    seg_of = dict(zip(features[name_col], features[seg_col]))
    pri_col = next(c for c in features.columns if "优先" in c or "最低" in c)
    seg_min_pri = features.groupby(seg_col)[pri_col].min().to_dict()
    seg_avg_pri = features.groupby(seg_col)[pri_col].mean().to_dict()
    seg_order = sorted(
        set(str(v) for v in seg_of.values()),
        key=lambda s: (seg_min_pri.get(s, 999), seg_avg_pri.get(s, 999), s),
    )
    fifo_order = [
        p for s in seg_order for p in fifo_order if str(seg_of[p]) == s
    ]

    if parallel_workers is None:
        env = os.environ.get("OPTIMIZER_PARALLEL", "1").strip().lower()
        parallel_workers = 1 if env in ("0", "false", "no", "off") else min(os.cpu_count() or 1, 8)
    if mp.current_process().name != "MainProcess":
        parallel_workers = 1
    parallel_workers = max(1, int(parallel_workers or 1))

    builder = SATabuOptimizer(features, parts, cfg, pp, seed=cfg.random_seed)
    _, fifo_met, _ = builder.schedule_from_order(fifo_order)
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
        parallel_workers=parallel_workers,
        plates=plates,
        speed_table=speed_table,
    )
    opt.fifo_cmax = fifo_cmax
    opt.fifo_kit_span = fifo_kit
    opt.fifo_load_diff = fifo_load
    opt.fifo_waiting = fifo_waiting

    # GA 按固定代数/固定评估次数运行，避免机器负载导致提前截断、结果不可复现。
    # 如需强制限制运行时间，可在 optimize() 里单独传入更小的 time_limit_seconds。
    time_limit = float("inf")

    def _wrap_cb(it, mx, cur, best, T):
        if progress_callback:
            progress_callback(it, mx, cur, best, T)

    order, metrics, stats = opt.optimize(
        fifo_order,
        max_iterations=iterations,
        time_limit_seconds=time_limit,
        verbose=False,
        progress_callback=_wrap_cb if progress_callback else None,
    )

    opt_schedule, opt_metrics, opt_stages = builder.schedule_from_order(order)
    base_schedule, base_metrics, base_stages = builder.schedule_from_order(fifo_order)

    print(f"[GA+LNS] {stats}")
    return {
        "schedule": opt_schedule,
        "base_schedule": base_schedule,
        "base_metrics": base_metrics,
        "opt_metrics": opt_metrics,
        "stages": opt_stages,
        "strategy_name": "GA+LNS",
    }


if __name__ == "__main__":
    from steel_schedule_model import (
        load_and_validate,
        load_process_params,
        plate_features,
    )

    cfg = ModelConfig()
    data = Path("question/产线场景描述/附件2：钢板零件数据.xlsx")
    speed = Path("question/产线场景描述/附件3：工艺用时计算表.xlsx")
    plates, parts, checks = load_and_validate(data)
    pp = load_process_params(speed)
    features = plate_features(plates, parts, cfg, pp.speed_table)
    result = run_ga_lns_optimization(
        plates, parts, features, cfg, pp, pp.speed_table, checks,
        iterations=120, objective_type="linear",
    )
    print(result["opt_metrics"])
