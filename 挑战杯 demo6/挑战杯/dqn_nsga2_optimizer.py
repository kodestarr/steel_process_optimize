"""DQN + improved NSGA-II for steel-plate scheduling.

The solver follows the paper's two-layer idea:

1. DQN assigns N2/N5/N8 machine codes to plate operation sequences and produces
   high-quality initial machine maps. If no trained weight file exists, heuristic
   machine maps are used so the algorithm still runs end to end.
2. NSGA-II evolves the combined `(plate order, machine map)` chromosome with a
   vector objective. The elite-retention rule keeps diversity by retaining
   lower-rank solutions with a generation-decaying probability.

Unlike the scalar SA/GA routes, one run returns a Pareto front. The front is
then used twice: once with the capacity-first rule ("重工时") and once with the
comprehensive three-metric rule ("兼顾三指标").
"""
from __future__ import annotations

import math
import multiprocessing as mp
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from improved_optimizer import SATabuOptimizer
from steel_schedule_model import (
    ModelConfig,
    ProcessParams,
    build_crane_aware_orders,
    legacy_balanced_objective,
    machine_names,
    select_best_by_track,
    unified_capacity_objective,
)
from pareto_optimizer import _build_init_orders
from dqn_machine_model import _eligible_machines, dqn_seed_machine_maps


_DQ_PARALLEL_STATE: dict = {}


def _dq_init_worker(features, parts, cfg, pp):
    _DQ_PARALLEL_STATE.clear()
    _DQ_PARALLEL_STATE["builder"] = SATabuOptimizer(
        features, parts, cfg, pp, seed=int(getattr(cfg, "random_seed", 20260723))
    )


def _dq_eval_worker(payload):
    order, machine_map = payload
    builder = _DQ_PARALLEL_STATE["builder"]
    order = builder._enforce_first_plate(order)
    _schedule, metrics, _stages = builder.schedule_from_order(order, machine_map)
    return order, machine_map, metrics


class DqnNsga2Optimizer(SATabuOptimizer):
    """Vector-objective NSGA-II with optional DQN machine-code seeds."""

    def __init__(
        self,
        features,
        parts,
        cfg: ModelConfig,
        pp: ProcessParams | None = None,
        seed: int = 20260723,
        plates=None,
        speed_table=None,
        parallel_workers: int = 1,
    ):
        super().__init__(features, parts, cfg, pp, seed=seed)
        self.features = features
        self.plates = plates
        self.speed_table = speed_table
        self.machines = machine_names(cfg)
        self.parallel_workers = max(1, int(parallel_workers or 1))
        self._pool = None
        self._all_records: list[dict] = []
        self._record_keys: set[str] = set()
        self._archive: list[dict] = []
        self._metrics_cache: dict[str, dict] = {}
        self.fifo_cmax: float | None = None
        self.fifo_kit: float | None = None
        self.fifo_load: float | None = None
        self.fifo_waiting: float | None = None
        self.fifo_met: dict | None = None

    # ── Hash / cache ──────────────────────────────────────
    def _record_key(self, order: list[str], machine_map: dict[str, str]) -> str:
        return self._hash_order(order, machine_map)

    def _eval_metrics(self, order: list[str], machine_map: dict[str, str]) -> dict:
        order = self._enforce_first_plate(order)
        key = self._record_key(order, machine_map)
        met = self._metrics_cache.get(key)
        if met is None:
            _schedule, met, _stages = self.schedule_from_order(order, machine_map)
            self._metrics_cache[key] = met
        return met

    def _evaluate_many(self, raw: list[dict]) -> list[dict]:
        unique: list[dict] = []
        seen: set[str] = set()
        for rec in raw:
            order = self._enforce_first_plate(rec["order"])
            key = self._record_key(order, rec["machine_map"])
            if key not in seen:
                seen.add(key)
                unique.append({**rec, "order": order[:]})
        if self._pool is None:
            results = [
                (
                    r["order"],
                    r["machine_map"],
                    self._eval_metrics(r["order"], r["machine_map"]),
                )
                for r in unique
            ]
        else:
            results = self._pool.map(
                _dq_eval_worker,
                [(r["order"], r["machine_map"]) for r in unique],
            )
        records: list[dict] = []
        for order, machine_map, met in results:
            key = self._record_key(order, machine_map)
            if key in self._record_keys:
                continue
            self._record_keys.add(key)
            rec = {
                "id": key,
                "order": order[:],
                "machine_map": machine_map,
                "metrics": met,
                "obj": self._metric_vector(met),
                "rank": 0,
                "crowd": 0.0,
            }
            records.append(rec)
            self._all_records.append(rec)
            self._register_archive(rec)
        return records

    # ── Vector objective and dominance ────────────────────
    def _metric_vector(self, met: dict) -> list[float]:
        cmax = float(met["总完工时间(h)"])
        lb = float(met.get("理论下界(h)", 0.0) or 0.0)
        if lb <= 0:
            lb = max(cmax, 1e-9)
        cap = cmax / max(lb, 1e-9)
        kit = float(met["加权平均齐套跨度(h)"])
        load = float(met["切割负载差(h)"])
        wait = float(met.get("总等待时间(h)", 0.0))
        f_kit = float(self.fifo_kit or 0.0) if self.fifo_kit else kit
        f_load = float(self.fifo_load or 0.0) if self.fifo_load else load
        f_wait = float(self.fifo_waiting or 0.0) if self.fifo_waiting else max(wait, 1e-9)
        return [
            cap,
            kit / max(f_kit, 1e-9),
            load / max(f_load, 1e-9),
            wait / max(f_wait, 1e-9),
        ]

    @staticmethod
    def _dominates(a: list[float], b: list[float]) -> bool:
        tol = 1e-9
        return all(x <= y + tol for x, y in zip(a, b)) and any(
            x < y - tol for x, y in zip(a, b)
        )

    def _fronts(self, records: list[dict]) -> list[list[dict]]:
        remaining = list(records)
        fronts: list[list[dict]] = []
        while remaining:
            current: list[dict] = []
            dominated: list[dict] = []
            for i, rec in enumerate(remaining):
                if any(
                    self._dominates(other["obj"], rec["obj"])
                    for j, other in enumerate(remaining)
                    if j != i
                ):
                    dominated.append(rec)
                else:
                    current.append(rec)
            if not current:
                current = remaining
            fronts.append(current)
            remaining = [r for r in remaining if r not in current]
        return fronts

    def _non_dominated(self, records: list[dict]) -> list[dict]:
        return self._fronts(records)[0] if records else []

    def _register_archive(self, rec: dict) -> None:
        self._archive = [
            old for old in self._archive if not self._dominates(rec["obj"], old["obj"])
        ]
        if not any(self._dominates(old["obj"], rec["obj"]) for old in self._archive):
            self._archive.append(rec)

    def _assign_rank_crowd(self, records: list[dict]) -> None:
        for rec in records:
            rec["rank"] = 0
            rec["crowd"] = 0.0
        fronts = self._fronts(records)
        for rank, front in enumerate(fronts, start=1):
            for rec in front:
                rec["rank"] = rank
            self._assign_crowding(front)

    def _assign_crowding(self, front: list[dict]) -> None:
        n_obj = len(front[0]["obj"]) if front else 0
        for rec in front:
            rec["crowd"] = 0.0
        for k in range(n_obj):
            front.sort(key=lambda r: r["obj"][k])
            if len(front) <= 2:
                for rec in front:
                    rec["crowd"] = math.inf
                continue
            lo = front[0]["obj"][k]
            hi = front[-1]["obj"][k]
            denom = max(1e-9, hi - lo)
            front[0]["crowd"] = math.inf
            front[-1]["crowd"] = math.inf
            for i in range(1, len(front) - 1):
                front[i]["crowd"] += (front[i + 1]["obj"][k] - front[i - 1]["obj"][k]) / denom

    # ── Initial population ────────────────────────────────
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

    def _segment_randomize(self, order: list[str]) -> list[str]:
        seg_order = self._segment_priority_order()
        blocked: list[str] = []
        for s in seg_order:
            plates = [p for p in order if str(p) in set(self.seg_map[s])]
            self.rng.shuffle(plates)
            blocked.extend(plates)
        return blocked

    def _initial_orders(self, initial_order: list[str]) -> list[list[str]]:
        pop_size = max(4, int(getattr(self.cfg, "ga_population_size", 16)))
        orders: list[list[str]] = []
        seen: set[str] = set()

        heuristic_orders: list[list[str]] = []
        if self.plates is not None:
            try:
                heuristic_orders = list(
                    _build_init_orders(
                        self.features, self.plates, self.speed_table, self.cfg
                    ).values()
                )
            except Exception:
                heuristic_orders = []
        if initial_order:
            heuristic_orders.append(initial_order[:])
        for order in heuristic_orders:
            order = self._enforce_first_plate(order)
            key = self._hash_order(order)
            if key not in seen:
                seen.add(key)
                orders.append(order[:])
        for order in build_crane_aware_orders(self.features, self.cfg).values():
            order = self._enforce_first_plate(order)
            key = self._hash_order(order)
            if key not in seen:
                seen.add(key)
                orders.append(order[:])
        while len(orders) < pop_size:
            base = self.rng.choice(orders or [initial_order])
            order = self._segment_randomize(base)
            order = self._enforce_first_plate(order)
            key = self._hash_order(order)
            if key not in seen:
                seen.add(key)
                orders.append(order[:])
        return orders[:pop_size]

    def _machine_map_variant(self, base_map: dict[str, str]) -> dict[str, str]:
        new_map = dict(base_map)
        for _ in range(1 + int(self.rng.random() * 2)):
            plate = self.rng.choice(list(new_map.keys()))
            elig = _eligible_machines(self.features, plate, self.machines)
            others = [m for m in elig if m != new_map.get(plate)]
            if others:
                new_map[plate] = self.rng.choice(others)
        return new_map

    def _build_initial_population(self, initial_order: list[str]) -> list[dict]:
        pop_size = max(4, int(getattr(self.cfg, "ga_population_size", 16)))
        orders = self._initial_orders(initial_order)
        dqn_request = max(0, int(getattr(self.cfg, "dqn_seed_count", 8)))
        machine_maps = dqn_seed_machine_maps(
            self.features,
            orders,
            self.cfg,
            self.machines,
            dqn_request,
            population_size=pop_size,
        )
        raw: list[dict] = []
        for i in range(pop_size):
            order = orders[i % len(orders)]
            machine_map = machine_maps[i % len(machine_maps)]
            raw.append({"order": order[:], "machine_map": dict(machine_map)})
        while len(raw) < pop_size:
            order = orders[self.rng.randrange(len(orders))]
            base_map = machine_maps[self.rng.randrange(len(machine_maps))]
            raw.append({"order": order[:], "machine_map": self._machine_map_variant(base_map)})
        return raw[:pop_size]

    # ── Crossover / mutation ──────────────────────────────
    def _order_ox(self, p1: list[str], p2: list[str]) -> list[str]:
        n = len(p1)
        a, b = sorted(self.rng.sample(range(n), 2))
        child: list[str | None] = [None] * n
        child[a:b] = p1[a:b]
        block = set(p1[a:b])
        rest = [x for x in p2 if x not in block]
        j = 0
        for i in range(n):
            if child[i] is None:
                child[i] = rest[j]
                j += 1
        return [str(x) for x in child]

    def _crossover(
        self,
        p1: dict,
        p2: dict,
    ) -> tuple[dict, dict]:
        c1_order = self._order_ox(p1["order"], p2["order"])
        c2_order = self._order_ox(p2["order"], p1["order"])
        c1_map: dict[str, str] = {}
        c2_map: dict[str, str] = {}
        for plate in c1_order:
            c1_map[plate] = p1["machine_map"].get(plate, p2["machine_map"].get(plate, self.machines[0]))
        for plate in c2_order:
            c2_map[plate] = p2["machine_map"].get(plate, p1["machine_map"].get(plate, self.machines[0]))
        return (
            {"order": c1_order, "machine_map": c1_map},
            {"order": c2_order, "machine_map": c2_map},
        )

    def _mutate(self, rec: dict, generation: int) -> dict:
        order = rec["order"][:]
        machine_map = dict(rec["machine_map"])
        mutation_rate = float(getattr(self.cfg, "ga_mutation_rate", 0.20))
        if self.rng.random() < mutation_rate:
            order, _op, _key = self._neighbor(order, generation + 1)
        machine_mutation_rate = float(getattr(self.cfg, "ga_mutation_rate", 0.20)) * 0.8
        if self.rng.random() < machine_mutation_rate:
            machine_map = self._machine_map_variant(machine_map)
        order = self._enforce_first_plate(order)
        return {"order": order[:], "machine_map": machine_map}

    def _generate_offspring(self, population: list[dict], generation: int) -> list[dict]:
        pop_size = max(4, int(getattr(self.cfg, "ga_population_size", 16)))
        crossover_rate = float(getattr(self.cfg, "ga_crossover_rate", 0.85))
        raw: list[dict] = []
        while len(raw) < pop_size:
            p1 = self._tournament(population)
            p2 = self._tournament(population)
            if self.rng.random() < crossover_rate:
                c1, c2 = self._crossover(p1, p2)
            else:
                c1 = {"order": p1["order"][:], "machine_map": dict(p1["machine_map"])}
                c2 = {"order": p2["order"][:], "machine_map": dict(p2["machine_map"])}
            raw.append(self._mutate(c1, generation))
            raw.append(self._mutate(c2, generation))
        return raw[:pop_size]

    def _tournament(self, population: list[dict]) -> dict:
        k = min(max(2, int(getattr(self.cfg, "ga_tournament_size", 3))), len(population))
        contenders = self.rng.sample(population, k)
        return min(
            contenders,
            key=lambda r: (r["rank"], -r["crowd"]),
        )

    def _probabilistic_next(
        self,
        merged: list[dict],
        generation: int,
        generations: int,
    ) -> list[dict]:
        pop_size = max(4, int(getattr(self.cfg, "ga_population_size", 16)))
        self._assign_rank_crowd(merged)
        fronts = self._fronts(merged)
        n_total = max(1, len(merged))
        selected: list[dict] = []
        selected_ids: set[str] = set()

        for front in fronts:
            if len(selected) >= pop_size:
                break
            ni = len(front)
            if ni == 0:
                continue
            keep_base = math.sqrt(max(0.0, 1.0 - ni / (2.0 * n_total)))
            decay = math.exp(-max(0, generation) / max(1.0, generations)) - 1.0 + 1.0
            p_i = keep_base * decay
            ranked = sorted(front, key=lambda r: (-r["crowd"], r["obj"][0]))
            if len(selected) + ni <= pop_size:
                for rec in ranked:
                    if rec["id"] not in selected_ids:
                        selected.append(rec)
                        selected_ids.add(rec["id"])
            else:
                for rec in ranked:
                    if len(selected) >= pop_size:
                        break
                    if rec["id"] in selected_ids:
                        continue
                    if self.rng.random() <= p_i or len(selected) < max(2, pop_size // 4):
                        selected.append(rec)
                        selected_ids.add(rec["id"])
        if len(selected) < pop_size:
            for rec in sorted(merged, key=lambda r: (r["rank"], -r["crowd"])):
                if len(selected) >= pop_size:
                    break
                if rec["id"] not in selected_ids:
                    selected.append(rec)
                    selected_ids.add(rec["id"])
        return selected[:pop_size]

    # ── Main search ───────────────────────────────────────
    def optimize(
        self,
        initial_order: list[str],
        max_iterations: int = 260,
        time_limit_seconds: float = float("inf"),
        progress_callback: object = None,
    ) -> tuple[dict, dict, list[dict], str]:
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
                initializer=_dq_init_worker,
                initargs=(self.features, self.parts, self.cfg, self.pp),
            )

        total_evals = 0
        best_cap_obj = math.inf
        try:
            raw_pop = self._build_initial_population(initial_order)
            population = self._evaluate_many(raw_pop)
            total_evals += len(population)
            self._assign_rank_crowd(population)
            for gen in range(generations):
                if time.time() - t0 > time_limit_seconds:
                    break
                offspring = self._generate_offspring(population, gen)
                offspring = self._evaluate_many(offspring)
                total_evals += len(offspring)
                merged = population + offspring
                population = self._probabilistic_next(merged, gen, generations)
                if progress_callback:
                    caps = [
                        unified_capacity_objective(
                            r["metrics"],
                            self.cfg,
                            fifo={
                                "加权平均齐套跨度(h)": self.fifo_kit,
                                "切割负载差(h)": self.fifo_load,
                                "总等待时间(h)": self.fifo_waiting,
                            },
                            objective_type="linear",
                        )
                        for r in population
                    ]
                    cur_obj = min(caps) if caps else 0.0
                    best_cap_obj = min(best_cap_obj, cur_obj)
                    progress_callback(
                        gen + 1,
                        generations,
                        cur_obj,
                        best_cap_obj,
                        max(0.01, 12.0 * (0.985 ** (gen + 1))),
                    )
        finally:
            if self._pool is not None:
                self._pool.close()
                self._pool.join()
                self._pool = None

        front = self._non_dominated(self._all_records)
        capacity = self._select_record(front, "capacity")
        balanced = self._select_record(front, "balanced")
        elapsed = time.time() - t0
        stats = (
            f"DQN+NSGA-II: {generations} gens, {total_evals} evals, "
            f"{len(front)} front, {elapsed:.1f}s"
        )
        return capacity, balanced, front, stats

    def _select_record(self, front: list[dict], track: str) -> dict | None:
        if not front:
            return None
        candidates = [(r["id"], r["order"], r["metrics"]) for r in front]
        cfg = self.cfg
        prev_track = getattr(cfg, "eval_track", "capacity")
        cfg.eval_track = track
        try:
            _name, _order, _met = select_best_by_track(
                candidates,
                cfg,
                objective_type="auto",
                fifo_cmax=self.fifo_cmax,
                fifo_kit=self.fifo_kit,
                fifo_load=self.fifo_load,
            )
        finally:
            cfg.eval_track = prev_track
        if _order is None:
            return None
        by_id = {r["id"]: r for r in front}
        rec = by_id.get(str(_name))
        if rec is None:
            rec = next((r for r in front if r["order"] == _order), None)
        return rec


def run_dq_nsga2_pareto_front(
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
    """Run one Pareto search and return both capacity/balanced delivery results."""
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
    fifo_order = [p for s in seg_order for p in fifo_order if str(seg_of[p]) == s]

    if parallel_workers is None:
        env = os.environ.get("OPTIMIZER_PARALLEL", "1").strip().lower()
        parallel_workers = 1 if env in ("0", "false", "no", "off") else min(os.cpu_count() or 1, 8)
    if mp.current_process().name != "MainProcess":
        parallel_workers = 1
    parallel_workers = max(1, int(parallel_workers or 1))

    fifo_builder = SATabuOptimizer(features, parts, cfg, pp, seed=cfg.random_seed)
    base_schedule, base_metrics, base_stages = fifo_builder.schedule_from_order(fifo_order)
    opt = DqnNsga2Optimizer(
        features,
        parts,
        cfg,
        pp,
        seed=cfg.random_seed,
        plates=plates,
        speed_table=speed_table,
        parallel_workers=parallel_workers,
    )
    opt.fifo_cmax = base_metrics["总完工时间(h)"]
    opt.fifo_kit = base_metrics["加权平均齐套跨度(h)"]
    opt.fifo_load = base_metrics["切割负载差(h)"]
    opt.fifo_waiting = base_metrics.get("总等待时间(h)", 0.0)
    opt.fifo_met = base_metrics

    capacity_rec, balanced_rec, front, stats = opt.optimize(
        fifo_order,
        max_iterations=iterations,
        time_limit_seconds=float("inf"),
        progress_callback=progress_callback,
    )
    if capacity_rec is None or balanced_rec is None:
        raise RuntimeError("DQN+NSGA-II 未产生 Pareto 前沿解")

    cap_schedule, cap_metrics, cap_stages = fifo_builder.schedule_from_order(
        capacity_rec["order"], capacity_rec["machine_map"]
    )
    bal_schedule, bal_metrics, bal_stages = fifo_builder.schedule_from_order(
        balanced_rec["order"], balanced_rec["machine_map"]
    )
    print(f"[DQN+NSGA-II] {stats}")
    print(
        f"[DQN+NSGA-II] capacity Cmax={cap_metrics['总完工时间(h)']:.2f}h "
        f"Kit={cap_metrics['加权平均齐套跨度(h)']:.2f}h | balanced "
        f"Cmax={bal_metrics['总完工时间(h)']:.2f}h Kit={bal_metrics['加权平均齐套跨度(h)']:.2f}h"
    )
    return {
        "base_schedule": base_schedule,
        "base_metrics": base_metrics,
        "base_stages": base_stages,
        "capacity": {
            "schedule": cap_schedule,
            "metrics": cap_metrics,
            "stages": cap_stages,
        },
        "balanced": {
            "schedule": bal_schedule,
            "metrics": bal_metrics,
            "stages": bal_stages,
        },
        "front": [
            {
                "order": r["order"],
                "machine_map": r["machine_map"],
                "metrics": r["metrics"],
                "objective": r["obj"],
            }
            for r in front
        ],
        "stats": stats,
    }


if __name__ == "__main__":
    from steel_schedule_model import (
        load_and_validate,
        load_process_params,
        plate_features,
    )

    cfg = ModelConfig()
    cfg.optimizer_method = "dq_nsga2"
    data = Path("question/产线场景描述/附件2：钢板零件数据.xlsx")
    speed = Path("question/产线场景描述/附件3：工艺用时计算表.xlsx")
    plates, parts, checks = load_and_validate(data)
    pp = load_process_params(speed)
    features = plate_features(plates, parts, cfg, pp.speed_table)
    result = run_dq_nsga2_pareto_front(
        plates, parts, features, cfg, pp, pp.speed_table, checks,
        iterations=60, parallel_workers=1,
    )
    print(result["capacity"]["metrics"])
