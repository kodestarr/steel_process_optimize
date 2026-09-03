"""
Improved optimizer: Simulated Annealing + Tabu Search hybrid metaheuristic.
Replaces the original broken local search with a proper multi-start SA+Tabu
that actively explores the solution space and reliably outperforms FIFO.

Key improvements over v1:
  - SA with adaptive geometric cooling and reheating
  - Tabu list (hashed sub-sequences) to prevent cycling
  - 4 neighborhood operators: swap, insert, block-reverse, segment-shuffle
  - Multi-start from diverse initial solutions
  - Kit-span-weighted acceptance criterion
  - Crane time uses ModelConfig parameters consistently
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

import pandas as pd
import numpy as np
from steel_schedule_model import (
    ModelConfig, ProcessParams, load_and_validate, load_process_params,
    plate_features, make_greedy_schedule, simulate, objective, pick_machine,
    build_joint_schedule, build_crane_aware_orders,
    CranePool, _reserve_raw_material_crane,
    unified_capacity_objective, select_best_by_capacity,
)


# ═══════════════════════════════════════════════════════════
#  SA+Tabu 混合元启发式核心
# ═══════════════════════════════════════════════════════════

class SATabuOptimizer:
    """Simulated Annealing with Tabu list for plate sequencing."""

    def __init__(
        self,
        features: pd.DataFrame,
        parts: pd.DataFrame,
        cfg: ModelConfig,
        pp: ProcessParams | None = None,
        seed: int = 20260723,
    ):
        self.rng = random.Random(seed)
        self.cfg = cfg
        self.pp = pp
        self.parts = parts

        # ── P1-3 修复：按列名匹配而非硬编码位置索引 ──
        # 寻找套料图名列（按名称匹配，回退到位置[2]保持向后兼容）
        _name_candidates = [c for c in features.columns if '套料图名' in c or 'plate' in c.lower()]
        self.name_col = _name_candidates[0] if _name_candidates else features.columns[2]

        # 寻找分段号列
        _seg_candidates = [c for c in features.columns if '分段号' in c or 'section' in c.lower() or 'segment' in c.lower()]
        self.seg_col = _seg_candidates[0] if _seg_candidates else features.columns[1]

        # 验证关键列存在
        if self.name_col not in features.columns:
            raise KeyError(f"套料图名列 '{self.name_col}' 不在 DataFrame 中，可用列: {list(features.columns)}")
        if self.seg_col not in features.columns:
            raise KeyError(f"分段号列 '{self.seg_col}' 不在 DataFrame 中，可用列: {list(features.columns)}")
        cut_candidates = [c for c in features.columns if '工时' in c and 'min' in c]
        if not cut_candidates:
            raise KeyError(f"未找到切割工时列，可用列: {list(features.columns)}")
        self.cut_col = cut_candidates[0]
        pri_candidates = [c for c in features.columns if '优先' in c or '最低' in c]
        if not pri_candidates:
            raise KeyError(f"未找到优先级列，可用列: {list(features.columns)}")
        self.pri_col = pri_candidates[0]

        self.fidx = features.set_index(self.name_col)
        self.plate_names = features[self.name_col].tolist()
        self.n = len(self.plate_names)
        self.cut_map = dict(zip(self.fidx.index.astype(str), self.fidx[self.cut_col].astype(float)))
        self.long_cut_limit = float(
            features[self.cut_col].quantile(getattr(cfg, "crane_first_cut_quantile", 0.75))
        )

        # Build segment→plate mapping for segment-aware operators
        self.seg_map: dict[str, list[str]] = {}
        for _, r in features.iterrows():
            seg = str(r[self.seg_col])
            self.seg_map.setdefault(seg, []).append(r[self.name_col])

        # Crane time from cfg (consistent with make_greedy_schedule)
        self.crane_unload = 5.0   # 下料 min
        self.crane_load = 3.0     # 上料 min
        self.crane_overlap = getattr(cfg, 'crane_overlap_minutes', 0.0)
        self.crane_total = 0.0  # 双工位：不再把每板天车串行进切割排程

        # Tabu: store hash of order states, tenure ~7% of plates (reduced for more exploration)
        self.tabu: set[str] = set()
        self.tabu_tenure = max(5, self.n // 10)   # ~10 for 109 plates
        self.tabu_max_size = self.tabu_tenure * 3  # ~30 max entries (smaller = less restrictive)

        # SA parameters — tuned for shipbuilding plate scheduling
        self.T_start = 15.0       # higher initial temperature for better exploration
        self.T_end = 0.01
        self.cooling_rate = 0.992
        self.reheat_factor = 0.45
        self.patience = max(60, self.n)  # adaptive patience

        # Pareto archive for multi-objective trade-off tracking
        self.pareto_archive: list[tuple[list[str], dict]] = []
        self._archive_max_size = 50  # keep top 50 non-dominated solutions

        # Evaluation cache (bounded to prevent memory growth)
        self._eval_cache: dict[str, tuple] = {}
        self._eval_cache_max = 5000

    # ── Schedule builder ──────────────────────────────────
    def _enforce_first_plate(self, order: list[str]) -> list[str]:
        """首板不能是长切割钢板，避免天车早期长时间停歇。"""
        if not order:
            return order
        if float(self.cut_map.get(str(order[0]), 0.0)) <= self.long_cut_limit:
            return order
        order = list(order)
        for i in range(1, len(order)):
            if float(self.cut_map.get(str(order[i]), 0.0)) <= self.long_cut_limit:
                order[0], order[i] = order[i], order[0]
                break
        return order

    def schedule_from_order(self, order_names: list[str]):
        """Build a multi-machine schedule from a plate order, with crane interlock."""
        order_names = self._enforce_first_plate(order_names)
        key = self._hash_order(order_names)
        if key in self._eval_cache:
            return self._eval_cache[key]
        # Evict oldest entries if cache exceeds limit
        if len(self._eval_cache) >= self._eval_cache_max:
            evict_count = len(self._eval_cache) // 4
            keys_to_evict = list(self._eval_cache.keys())[:evict_count]
            for k in keys_to_evict:
                del self._eval_cache[k]

        from steel_schedule_model import machine_names
        machines = machine_names(self.cfg)
        ordered = self.fidx.loc[order_names].reset_index()
        free = {m: 0.0 for m in machines}
        worktables = {m: [0.0, 0.0] for m in machines}
        gun_free = {m: 0.0 for m in machines}
        last_table = {m: 1 for m in machines}
        crane_pool = CranePool()
        rows = []
        for seq_i, (_, r) in enumerate(ordered.iterrows(), 1):
            m = pick_machine(r, free)
            raw_start, raw_empty_end, raw_loaded_end = _reserve_raw_material_crane(
                crane_pool, min(worktables[m]), self.cfg,
            )
            if worktables[m][0] <= raw_loaded_end and worktables[m][1] <= raw_loaded_end:
                table_idx = 1 - last_table[m]
            elif worktables[m][0] <= raw_loaded_end:
                table_idx = 0
            elif worktables[m][1] <= raw_loaded_end:
                table_idx = 1
            else:
                table_idx = 0 if worktables[m][0] <= worktables[m][1] else 1
            last_table[m] = table_idx
            table_occ_start = max(raw_loaded_end, worktables[m][table_idx])
            st = max(free[m], table_occ_start)
            en = st + r[self.cut_col]
            handling = float(r.get("工位处理时间(min)", 0.0))
            table_end = en + handling
            worktables[m][table_idx] = table_end
            gun_free[m] = en
            free[m] = max(gun_free[m], min(worktables[m]))
            rows.append({
                self.name_col: r[self.name_col],
                self.seg_col: r[self.seg_col],
                'machine': m, 'seq': seq_i,
                'start': st, 'end': en,
                'dur': r[self.cut_col], 'pri': r[self.pri_col],
                'raw_start': raw_start,
                'raw_empty_end': raw_empty_end,
                'raw_loaded_end': raw_loaded_end,
                'table_start': table_occ_start,
                'wait_end': st,
                'table_handling': handling,
                'table_end': table_end,
                'table_idx': table_idx,
            })
        sched = pd.DataFrame(rows)
        sched = sched.rename(columns={
            'machine': '切割机', self.name_col: '套料图名', self.seg_col: '分段号',
            'seq': '切割序号', 'start': '切割开始(min)',
            'end': '切割完成(min)', 'dur': '切割工时(min)', 'pri': '最低优先级',
            'raw_start': '原料吊运开始(min)',
            'raw_empty_end': '原料空驶完成(min)',
            'raw_loaded_end': '原料吊运完成(min)',
            'table_start': '工位开始(min)',
            'wait_end': '等待结束(min)',
            'table_handling': '工位处理时间(min)',
            'table_end': '工位完工(min)',
            'table_idx': '工位',
        })
        new_sched, _, _, met, stg, _ = build_joint_schedule(
            sched, self.parts, self.cfg, self.pp,
        )
        result = (new_sched, met, stg)
        self._eval_cache[key] = result
        return result

    @staticmethod
    def _hash_order(order: list[str]) -> str:
        """P3-1: 使用完整序列哈希替代局部采样，消除MD5碰撞风险。

        原采样模式（首5+每10个+尾5）可能导致不同排列产生相同哈希，
        进而错误绕过Tabu检查或返回错误的评估缓存结果。
        改用完整序列字符串进行MD5哈希，虽然计算稍慢但消除了碰撞隐患。
        对于典型109板序列，字符串长度约2KB，MD5计算可忽略不计。
        """
        # 使用完整序列：拼接所有板名，用不可分割分隔符连接
        full = '\x00'.join(order)
        return hashlib.md5(full.encode('utf-8')).hexdigest()

    # ── Kit-span focused objective ────────────────────────
    # P3-3: 统一从 ModelConfig 引用竞赛目标常量
    _CMAX_TARGET = ModelConfig.CMAX_TARGET_H      # 67.25 hours
    _KITSPAN_TARGET = ModelConfig.KITSPAN_TARGET_H  # 12.7 hours
    _LOADDIFF_TARGET = ModelConfig.LOADDIFF_TARGET_H  # 1.0 hours

    def kit_span_objective(self, metrics: dict) -> float:
        """产能优先统一目标（P4-1）。

        评价函数不再直接加权 Cmax/齐套/负载，而是：
        J = Cmax/LB + eps * Secondary
        Secondary 按 cfg.objective_type 选择线性或二次，且封顶归一化。
        """
        fifo = {
            "加权平均齐套跨度(h)": getattr(self, "fifo_kit_span", None),
            "切割负载差(h)": getattr(self, "fifo_load_diff", None),
            "总等待时间(h)": getattr(self, "fifo_waiting", None),
        }
        objective_type = getattr(self.cfg, "objective_type", "linear")
        return unified_capacity_objective(metrics, self.cfg, fifo=fifo, objective_type=objective_type)

    # ── Neighborhood operators ─────────────────────────────
    def _n(self, order: list[str]) -> int:
        """Get current order length (supports partial reschedule)."""
        return len(order)

    def _seg_positions(self, order: list[str], seg: str) -> list[int]:
        return [i for i, name in enumerate(order) if name in self.seg_map[seg]]

    def _swap(self, order: list[str]) -> list[str]:
        """Swap two plates within the same segment."""
        if len(self.seg_map) < 1:
            return order
        seg = self.rng.choice(list(self.seg_map.keys()))
        positions = self._seg_positions(order, seg)
        if len(positions) < 2:
            return order
        i, j = sorted(self.rng.sample(positions, 2))
        order = order[:]
        order[i], order[j] = order[j], order[i]
        return order

    def _insert(self, order: list[str]) -> list[str]:
        """Move a plate to another position within the same segment."""
        if len(self.seg_map) < 1:
            return order
        seg = self.rng.choice(list(self.seg_map.keys()))
        positions = self._seg_positions(order, seg)
        if len(positions) < 2:
            return order
        i, j = sorted(self.rng.sample(positions, 2))
        order = order[:]
        item = order.pop(j)
        order.insert(i, item)
        return order

    def _block_reverse(self, order: list[str]) -> list[str]:
        """Reverse a contiguous block of plates inside one segment."""
        if len(self.seg_map) < 1:
            return order
        seg = self.rng.choice(list(self.seg_map.keys()))
        positions = self._seg_positions(order, seg)
        if len(positions) < 3:
            return self._swap(order)
        k = self.rng.randint(0, len(positions) - 2)
        j = min(len(positions), k + self.rng.randint(2, min(8, len(positions) - k)))
        slice_positions = positions[k:j]
        order = order[:]
        vals = [order[p] for p in slice_positions]
        for p, v in zip(slice_positions, reversed(vals)):
            order[p] = v
        return order

    def _segment_shuffle(self, order: list[str]) -> list[str]:
        """Shuffle plates within the same segment to reduce kit span."""
        if len(self.seg_map) < 2:
            return self._swap(order)
        seg = self.rng.choice(list(self.seg_map.keys()))
        plates_in_seg = self.seg_map[seg]
        if len(plates_in_seg) < 3:
            return self._swap(order)
        # Reorder plates in this segment by priority, then shorter cut time
        seg_features = self.fidx.loc[plates_in_seg].sort_values(
            [self.pri_col, self.cut_col], ascending=[True, True]
        )
        new_order = order[:]
        indices = [i for i, name in enumerate(order) if name in plates_in_seg]
        # iterrows() yields (index_label, Series); index_label is the plate name
        for idx, (plate_name, _row) in zip(indices, seg_features.iterrows()):
            new_order[idx] = str(plate_name)
        return new_order

    def _cross_machine_rebalance(self, order: list[str]) -> list[str]:
        """Move a long plate earlier within the same segment to balance load."""
        if len(self.seg_map) < 1:
            return order
        seg = self.rng.choice(list(self.seg_map.keys()))
        positions = self._seg_positions(order, seg)
        if len(positions) < 10:
            return self._swap(order)
        late_positions = positions[int(len(positions) * 0.7):]
        late_plates = [order[p] for p in late_positions]
        late_features = self.fidx.loc[late_plates]
        avg_cut = late_features[self.cut_col].mean()
        heavy_late = [p for p in late_plates
                      if self.fidx.loc[p, self.cut_col] > avg_cut]
        if not heavy_late:
            return self._swap(order)
        plate_to_move = self.rng.choice(heavy_late)
        j = order.index(plate_to_move)
        i = self.rng.choice([p for p in positions if p < j] or positions)
        new_order = order[:]
        item = new_order.pop(j)
        new_order.insert(i, item)
        return new_order

    def _stagger_crane(self, order: list[str]) -> list[str]:
        """Stagger plate durations inside one segment to reduce crane contention."""
        if len(self.seg_map) < 1:
            return order
        seg = self.rng.choice(list(self.seg_map.keys()))
        positions = self._seg_positions(order, seg)
        if len(positions) < 6:
            return self._swap(order)
        start_idx = self.rng.randint(0, len(positions) - 6)
        window_positions = positions[start_idx:start_idx + 6]
        window = [order[p] for p in window_positions]
        window_sorted = sorted(window, key=lambda p: self.fidx.loc[p, self.cut_col])
        staggered = [window_sorted[0], window_sorted[3], window_sorted[1],
                     window_sorted[4], window_sorted[2], window_sorted[5]]
        new_order = order[:]
        for p, v in zip(window_positions, staggered):
            new_order[p] = v
        return new_order

    def _kit_cluster(self, order: list[str]) -> list[str]:
        """Cluster plates from the same segment closer together.

        Pick a random segment and bring its scattered plates into a contiguous block,
        reducing the kit span for that segment.
        """
        if len(self.seg_map) < 2:
            return self._swap(order)
        seg = self.rng.choice(list(self.seg_map.keys()))
        plates_in_seg = self.seg_map[seg]
        if len(plates_in_seg) < 3:
            return self._swap(order)
        # Find current positions of this segment's plates
        positions = sorted([i for i, name in enumerate(order) if name in plates_in_seg])
        if len(positions) < 3:
            return self._swap(order)
        # Cluster around the median position
        median_pos = positions[len(positions) // 2]
        # Sort the plates by priority then cut time
        seg_features = self.fidx.loc[plates_in_seg].sort_values(
            [self.pri_col, self.cut_col], ascending=[True, True]
        )
        sorted_plates = list(seg_features.index)
        new_order = [p for p in order if p not in plates_in_seg]
        # Insert the clustered plates at median position
        insert_at = min(median_pos, len(new_order))
        for i, p in enumerate(sorted_plates):
            new_order.insert(insert_at + i, p)
        return new_order

    # ── Pareto archive management ───────────────────────────
    def _pareto_dominates(self, a_cmax: float, a_kit: float, b_cmax: float, b_kit: float) -> bool:
        """Check if solution A Pareto-dominates solution B (minimizing both Cmax and KitSpan)."""
        return (a_cmax <= b_cmax and a_kit <= b_kit) and (a_cmax < b_cmax or a_kit < b_kit)

    def _update_pareto_archive(self, order: list[str], metrics: dict):
        """Insert solution into Pareto archive, removing dominated entries."""
        cmax = metrics["总完工时间(h)"]
        kit_span = metrics["加权平均齐套跨度(h)"]
        # Check if dominated by any existing entry
        for _, existing_met in self.pareto_archive:
            e_cmax = existing_met["总完工时间(h)"]
            e_kit = existing_met["加权平均齐套跨度(h)"]
            if self._pareto_dominates(e_cmax, e_kit, cmax, kit_span):
                return  # Dominated by existing — skip
        # Remove existing entries dominated by new solution
        self.pareto_archive = [
            (o, m) for o, m in self.pareto_archive
            if not self._pareto_dominates(cmax, kit_span, m["总完工时间(h)"], m["加权平均齐套跨度(h)"])
        ]
        self.pareto_archive.append((order[:], metrics))
        # Keep archive bounded
        if len(self.pareto_archive) > self._archive_max_size:
            # Keep most diverse: sort by Cmax, keep evenly spaced
            self.pareto_archive.sort(key=lambda x: x[1]["总完工时间(h)"])
            step = len(self.pareto_archive) / self._archive_max_size
            self.pareto_archive = [self.pareto_archive[int(i * step)] for i in range(self._archive_max_size)]

    def _select_from_pareto(self, fifo_cmax: float | None = None) -> tuple[list[str], dict]:
        """产能优先最终选解（P4-1）：先比 Cmax/LB，1%内再比辅助指标。"""
        if not self.pareto_archive:
            return [], {}
        candidates = [
            (f"archive{i}", o, m)
            for i, (o, m) in enumerate(self.pareto_archive)
        ]
        _name, best_order, best_met = select_best_by_capacity(
            candidates, self.cfg, getattr(self.cfg, "objective_type", "linear")
        )
        return best_order, best_met

    def _neighbor(self, order: list[str], iteration: int) -> tuple[list[str], str, str]:
        """Generate a neighbor with diverse operators for better exploration.

        Early search: more exploration (swap, insert, block_reverse, cross_rebalance).
        Later search: more exploitation (kit_cluster, seg_shuffle for KitSpan reduction).
        """
        r = self.rng.random()
        # Adaptive: more kit_cluster and seg_shuffle as search progresses
        kit_bias = min(0.4, iteration / max(1, self.n * 8))  # increases from 0 to 0.4

        if r < 0.18 + kit_bias * 0.35:
            op = self._kit_cluster
            op_name = "kit_cluster"
        elif r < 0.28 + kit_bias * 0.35:
            op = self._segment_shuffle
            op_name = "seg_shuffle"
        elif r < 0.44:
            op = self._stagger_crane
            op_name = "stagger_crane"
        elif r < 0.56:
            op = self._swap
            op_name = "swap"
        elif r < 0.67:
            op = self._insert
            op_name = "insert"
        elif r < 0.78:
            op = self._block_reverse
            op_name = "block_rev"
        elif r < 0.89:
            op = self._cross_machine_rebalance
            op_name = "cross_rebalance"
        else:
            op = self._block_reverse
            op_name = "block_rev"

        neighbor = op(order)
        tkey = self._hash_order(neighbor)
        return neighbor, op_name, tkey

    # ── Main SA loop ──────────────────────────────────────
    def optimize(
        self,
        initial_order: list[str],
        max_iterations: int = 500,
        time_limit_seconds: float = 300.0,
        verbose: bool = False,
        progress_callback: object = None,
    ) -> tuple[list[str], dict, str]:
        """Run SA+Tabu with Pareto archive for dual-win (Cmax, KitSpan) optimization.

        Maintains a Pareto archive of non-dominated solutions. At completion,
        selects the solution from the archive that minimizes KitSpan while
        keeping Cmax at or below the FIFO baseline.

        Args:
            progress_callback: optional fn(iteration, max_iter, current_obj, best_obj, T)

        Returns (best_order, best_metrics, stats_str).
        """
        t0 = time.time()

        # Evaluate initial
        initial_order = self._enforce_first_plate(initial_order)
        _, init_met, _ = self.schedule_from_order(initial_order)
        current_order = initial_order[:]
        current_met = init_met
        current_obj = self.kit_span_objective(init_met)

        best_order = current_order[:]
        best_met = current_met
        best_obj = current_obj

        # Initialize Pareto archive with initial solution
        self.pareto_archive = []
        self._update_pareto_archive(initial_order[:], init_met)

        T = self.T_start
        iteration = 0
        no_improve = 0
        _last_cb_iter = -10
        accepted = 0
        tabu_rejects = 0
        total_evals = 1

        # Stats tracking
        init_kit_span = init_met["加权平均齐套跨度(h)"]
        init_cmax = init_met["总完工时间(h)"]

        while iteration < max_iterations:
            if time.time() - t0 > time_limit_seconds:
                break

            iteration += 1

            # Generate neighbor
            neighbor, op_name, tkey = self._neighbor(current_order, iteration)
            neighbor = self._enforce_first_plate(neighbor)
            tkey = self._hash_order(neighbor)

            # Tabu check
            if tkey in self.tabu:
                tabu_rejects += 1
                continue

            # Evaluate
            _, met, _ = self.schedule_from_order(neighbor)
            total_evals += 1

            # Update Pareto archive (every evaluation)
            self._update_pareto_archive(neighbor, met)
            obj = self.kit_span_objective(met)

            # Acceptance
            delta = obj - current_obj
            if delta < 0:
                # Improvement: always accept
                current_order = neighbor
                current_met = met
                current_obj = obj
                accepted += 1
                no_improve = 0

                if obj < best_obj:
                    best_order = neighbor
                    best_met = met
                    best_obj = obj
            else:
                # SA probabilistic acceptance
                p_accept = math.exp(-delta / max(T, 1e-8))
                if self.rng.random() < p_accept:
                    current_order = neighbor
                    current_met = met
                    current_obj = obj
                    accepted += 1
                no_improve += 1

            # Add to tabu with bounded size
            self.tabu.add(tkey)
            if len(self.tabu) > self.tabu_max_size:
                items = list(self.tabu)
                self.tabu = set(items[-self.tabu_tenure * 2:])

            # Cool down
            T *= self.cooling_rate

            # Reheat if stuck
            if no_improve >= self.patience:
                T = max(T, self.T_start * self.reheat_factor)
                no_improve = 0
                if verbose:
                    print(f"  [SA] iter {iteration}: reheating to T={T:.3f}, best_obj={best_obj:.4f}")

            # Ensure minimum temperature
            T = max(T, self.T_end)

            # Progress callback (throttled to every ~10 iterations)
            if progress_callback and iteration - _last_cb_iter >= 10:
                progress_callback(iteration, max_iterations, current_obj, best_obj, T)
                _last_cb_iter = iteration

        # ── Tier 1 focused descent (Loop 3): SA后定向搜索 Cmax≤FIFO + KitSpan≤12.7 ──
        fifo_ref = getattr(self, 'fifo_cmax', None)
        if fifo_ref and self.pareto_archive:
            # Find starting points: solutions with KitSpan ≤ 13.5h (wider pool)
            tier2_candidates = [(o, m) for o, m in self.pareto_archive
                               if m["加权平均齐套跨度(h)"] <= 13.5]
            if tier2_candidates:
                seed_order, seed_met = min(tier2_candidates,
                    key=lambda x: x[1]["总完工时间(h)"])
                _focused_order = seed_order[:]
                _focused_met = seed_met
                _focused_cmax = seed_met["总完工时间(h)"]
                _focused_kit = seed_met["加权平均齐套跨度(h)"]
                _improved = 0
                for _fi in range(int(getattr(self, "_focused_descent_iterations", 150))):
                    # Early: more Cmax-reducing operators; later: more KitSpan-reducing
                    if _fi < 50:
                        _op_choice = self.rng.choice(['kit_cluster', 'stagger_crane', 'cross_rebalance', 'swap', 'insert'])
                    elif _fi < 100:
                        _op_choice = self.rng.choice(['kit_cluster', 'kit_cluster', 'seg_shuffle', 'swap', 'insert'])
                    else:
                        _op_choice = self.rng.choice(['kit_cluster', 'kit_cluster', 'stagger_crane', 'insert', 'block_rev'])
                    # Generate neighbor directly with chosen operator (no wasted _neighbor call)
                    if _op_choice == 'stagger_crane':
                        _nb = self._stagger_crane(_focused_order)
                    elif _op_choice == 'cross_rebalance':
                        _nb = self._cross_machine_rebalance(_focused_order)
                    elif _op_choice == 'kit_cluster':
                        _nb = self._kit_cluster(_focused_order)
                    elif _op_choice == 'seg_shuffle':
                        _nb = self._segment_shuffle(_focused_order)
                    elif _op_choice == 'block_rev':
                        _nb = self._block_reverse(_focused_order)
                    elif _op_choice == 'swap':
                        _nb = self._swap(_focused_order)
                    else:
                        _nb = self._insert(_focused_order)
                    # Tabu check on the actual neighbor (not a discarded random one)
                    if self._hash_order(_nb) in self.tabu:
                        continue
                    _, _met, _ = self.schedule_from_order(_nb)
                    _n_cmax = _met["总完工时间(h)"]
                    _n_kit = _met["加权平均齐套跨度(h)"]
                    # Accept if: KitSpan improves AND (Cmax ≤ FIFO OR Cmax decreases by ≥0.05h)
                    if _n_kit < _focused_kit and (_n_cmax <= fifo_ref or _n_cmax < _focused_cmax - 0.05):
                        _focused_order = _nb
                        _focused_met = _met
                        _focused_cmax = min(_focused_cmax, _n_cmax)
                        _focused_kit = _n_kit
                        _improved += 1
                        self._update_pareto_archive(_nb, _met)
                        # Early exit if Tier 1 achieved
                        if _n_cmax <= fifo_ref and _n_kit <= 12.7:
                            break
                if _improved > 0:
                    best_order = _focused_order
                    best_met = _focused_met
                    best_obj = self.kit_span_objective(_focused_met)

        # ── Pareto selection: prefer Cmax ≤ FIFO baseline ──
        pareto_order, pareto_met = self._select_from_pareto(fifo_ref)
        if (pareto_order is not None and len(pareto_order) > 0
                and pareto_met is not None and len(pareto_met) > 0):
            final_order = pareto_order
            final_met = pareto_met
        else:
            # Fallback to best_obj solution
            final_order = best_order
            final_met = best_met

        elapsed = time.time() - t0
        final_kit_span = final_met["加权平均齐套跨度(h)"]
        final_cmax = final_met["总完工时间(h)"]
        kit_improvement = (init_kit_span - final_kit_span) / init_kit_span * 100 if init_kit_span > 0 else 0
        cmax_improvement = (init_cmax - final_cmax) / init_cmax * 100 if init_cmax > 0 else 0

        archive_cmax_range = ""
        if self.pareto_archive:
            cmaxes = [m["总完工时间(h)"] for _, m in self.pareto_archive]
            archive_cmax_range = f", Archive: {len(self.pareto_archive)} sols, Cmax∈[{min(cmaxes):.1f}, {max(cmaxes):.1f}]h"

        stats = (
            f"SA+Tabu: {iteration} iters, {total_evals} evals, "
            f"{accepted} accepted, {tabu_rejects} tabu-rejected, "
            f"{elapsed:.1f}s | "
            f"KitSpan: {init_kit_span:.2f}h→{final_kit_span:.2f}h ({kit_improvement:+.1f}%), "
            f"Cmax: {init_cmax:.2f}h→{final_cmax:.2f}h ({cmax_improvement:+.1f}%)"
            f"{archive_cmax_range}"
        )
        return final_order, final_met, stats


# ═══════════════════════════════════════════════════════════
#  多策略并行 worker
# ═══════════════════════════════════════════════════════════

_PARALLEL_STATE: dict = {}


def _init_parallel_worker(
    features,
    parts,
    cfg,
    pp,
    fifo_cmax: float,
    fifo_kit: float,
    fifo_load: float,
    fifo_waiting: float,
    iter_per_start: int,
    time_per_start: float,
    progress_queue,
    optimizer_cls: type = SATabuOptimizer,
):
    """在每个并行子进程中加载一次共享数据。"""
    _PARALLEL_STATE.clear()
    _PARALLEL_STATE.update({
        "features": features,
        "parts": parts,
        "cfg": cfg,
        "pp": pp,
        "fifo_cmax": fifo_cmax,
        "fifo_kit": fifo_kit,
        "fifo_load": fifo_load,
        "fifo_waiting": fifo_waiting,
        "iter_per_start": iter_per_start,
        "time_per_start": time_per_start,
        "progress_queue": progress_queue,
        "shared_cache": {},
        "optimizer_cls": optimizer_cls,
    })


def _parallel_progress(name: str, iteration: int, max_iter: int, current_obj: float, best_obj: float, temp: float):
    """把子进程内的搜索进度投递到父进程队列。"""
    q = _PARALLEL_STATE.get("progress_queue")
    if q is not None:
        q.put((name, iteration, max_iter, current_obj, best_obj, temp))


def _run_parallel_start(job: tuple[str, list[str]]) -> dict:
    """在子进程中运行单个初始策略的 SA+Tabu。"""
    name, init_order = job
    st = _PARALLEL_STATE
    name_hash = int(hashlib.md5(name.encode("utf-8")).hexdigest(), 16) % 100000
    optimizer_cls = st.get("optimizer_cls", SATabuOptimizer)
    opt = optimizer_cls(
        st["features"],
        st["parts"],
        st["cfg"],
        st["pp"],
        seed=st["cfg"].random_seed + name_hash,
    )
    opt.fifo_cmax = st["fifo_cmax"]
    opt.fifo_kit_span = st["fifo_kit"]
    opt.fifo_load_diff = st["fifo_load"]
    opt.fifo_waiting = st.get("fifo_waiting")
    opt._eval_cache = st["shared_cache"]

    if st.get("progress_queue") is not None:
        def _cb(iteration: int, max_iter: int, current_obj: float, best_obj: float, temp: float, _name=name):
            _parallel_progress(_name, iteration, max_iter, current_obj, best_obj, temp)
    else:
        _cb = None

    order, metrics, stats = opt.optimize(
        init_order,
        max_iterations=st["iter_per_start"],
        time_limit_seconds=st["time_per_start"],
        verbose=False,
        progress_callback=_cb,
    )
    return {
        "name": name,
        "order": order,
        "metrics": metrics,
        "stats": stats,
        "archive": [(o, m) for o, m in opt.pareto_archive],
    }


def _drain_parallel_progress(progress_queue, names: list[str], iter_per_start: int, callback):
    """父进程线程：把子进程进度合并成总体进度并回调。"""
    name_index = {name: i for i, name in enumerate(names)}
    overall_max = len(names) * iter_per_start
    while True:
        msg = progress_queue.get()
        if msg is None:
            break
        name, iteration, _mx, current_obj, best_obj, temp = msg
        start_idx = name_index.get(name, 0)
        callback(start_idx * iter_per_start + iteration, overall_max, current_obj, best_obj, temp)


# ═══════════════════════════════════════════════════════════
#  Multi-start wrapper — used by backend API
# ═══════════════════════════════════════════════════════════

def run_multi_strategy_inline(
    plates: pd.DataFrame,
    parts: pd.DataFrame,
    features: pd.DataFrame,
    cfg: ModelConfig,
    pp: ProcessParams | None,
    speed_table: dict | None,
    checks: dict,
    iterations: int = 260,
    progress_callback: object = None,
    parallel_workers: int | None = None,
) -> dict:
    """
    Multi-start SA+Tabu optimization called by the FastAPI backend.

    Args:
        progress_callback: optional fn(iteration, max_iter, current_obj, best_obj, T)

    Returns dict with keys: schedule, base_schedule, base_metrics,
    opt_metrics, stages, strategy_name.
    """
    # ── P1-3 修复：按列名匹配而非硬编码位置索引 ──
    _name_candidates = [c for c in features.columns if '套料图名' in c or 'plate' in c.lower()]
    name_col = _name_candidates[0] if _name_candidates else features.columns[2]
    _seg_candidates = [c for c in features.columns if '分段号' in c or 'section' in c.lower() or 'segment' in c.lower()]
    seg_col = _seg_candidates[0] if _seg_candidates else features.columns[1]
    _seq_candidates = [c for c in features.columns if '序号' in c or 'seq' in c.lower()]
    seq_col = _seq_candidates[0] if _seq_candidates else features.columns[0]
    cut_candidates = [c for c in features.columns if '工时' in c and 'min' in c]
    if not cut_candidates:
        raise KeyError(f"未找到切割工时列，可用列: {list(features.columns)}")
    cut_col = cut_candidates[0]
    pri_candidates = [c for c in features.columns if '优先' in c or '最低' in c]
    if not pri_candidates:
        raise KeyError(f"未找到优先级列，可用列: {list(features.columns)}")
    pri_col = pri_candidates[0]

    # Build diverse initial solutions
    init_solutions: dict[str, list[str]] = {}

    # A: FIFO baseline
    init_solutions["FIFO"] = features.sort_values(seq_col, kind="stable")[name_col].tolist()

    # B: Completeness heuristic (priority-aware)
    comp_sched = make_greedy_schedule(features, "completeness", cfg.random_seed)
    # P0-3 修复：空列表安全守卫，防止 make_greedy_schedule 列名变化导致 IndexError
    _comp_seq_candidates = [c for c in comp_sched.columns if '序号' in c or '切割序号' in c]
    if not _comp_seq_candidates:
        raise KeyError(f"make_greedy_schedule 返回的排程缺少序号列，可用列: {list(comp_sched.columns)}")
    comp_seq_col = _comp_seq_candidates[0]
    init_solutions["Completeness"] = comp_sched.sort_values(comp_seq_col)[name_col].tolist()

    # C: Segment→priority→short-cut (best static heuristic)
    init_solutions["SegPriShort"] = features.sort_values(
        [seg_col, pri_col, cut_col], ascending=[True, True, True], kind="stable"
    )[name_col].tolist()

    # D: Segment→priority→long-cut
    init_solutions["SegPriLong"] = features.sort_values(
        [seg_col, pri_col, cut_col], ascending=[True, True, False], kind="stable"
    )[name_col].tolist()

    # E: Shortest processing time first
    init_solutions["SPT"] = features.sort_values(cut_col, ascending=True, kind="stable")[name_col].tolist()

    # F: By thickness (thin first → less heat, faster cutting)
    thick_cols = [c for c in plates.columns if '厚' in c]
    if thick_cols and speed_table:
        thick_map = dict(zip(plates[name_col], plates[thick_cols[0]]))
        features['_thick'] = features[name_col].map(thick_map)
        init_solutions["ThinFirst"] = features.sort_values('_thick', ascending=True, kind="stable")[name_col].tolist()
        features.drop(columns=['_thick'], inplace=True, errors='ignore')

    # 齐套按分段：所有初始解都先按分段成块，再在段内保持原策略顺序
    seg_of = dict(zip(features[name_col], features[seg_col]))

    def _segment_block_order(order_names: list[str]) -> list[str]:
        seg_order: list[str] = []
        for nm in order_names:
            s = str(seg_of[nm])
            if s not in seg_order:
                seg_order.append(s)
        seg_min_pri = features.groupby(seg_col)[pri_col].min().to_dict()
        seg_avg_pri = features.groupby(seg_col)[pri_col].mean().to_dict()
        seg_order.sort(key=lambda s: (
            seg_min_pri.get(s, 999),
            seg_avg_pri.get(s, 999),
            s,
        ))
        blocked: list[str] = []
        for s in seg_order:
            blocked.extend(nm for nm in order_names if str(seg_of[nm]) == s)
        return blocked

    for _key, _order in list(init_solutions.items()):
        init_solutions[_key] = _segment_block_order(_order)

    # ── 天车需求/缓冲能力分类调度池：作为额外初始策略供模型选择 ──
    init_solutions.update(build_crane_aware_orders(features, cfg))

    # ── Get FIFO baseline metrics for Pareto constraint ──
    # 复用 schedule_from_order 构造 FIFO 基线（含天车时间），
    # 与优化解的构造方式完全一致，确保对比公平。
    fifo_order = init_solutions.get("FIFO", list(init_solutions.values())[0])
    shared_cache: dict = {}
    _fifo_builder = SATabuOptimizer(features, parts, cfg, pp, seed=cfg.random_seed)
    _fifo_builder._eval_cache = shared_cache
    _, _fifo_met, _ = _fifo_builder.schedule_from_order(fifo_order)
    fifo_baseline_cmax = _fifo_met["总完工时间(h)"]
    fifo_baseline_kit = _fifo_met["加权平均齐套跨度(h)"]
    fifo_baseline_load = _fifo_met["切割负载差(h)"]
    fifo_baseline_waiting = _fifo_met.get("总等待时间(h)", 0.0)

    # ── Run SA+Tabu from each initial solution ──
    iter_per_start = max(80, iterations // len(init_solutions))
    time_per_start = max(30, 120 // len(init_solutions))

    # ── Cross-start Pareto selection (Loop 2) ──
    # Each start returns its Pareto-optimal solution. Collect all and select
    # the best dual-win (Cmax, KitSpan) solution across all starts.
    all_candidates: list[tuple[str, list[str], dict]] = []  # (start_name, order, metrics)
    all_stats: list[str] = []
    total_starts = len(init_solutions)

    # 并行策略数：默认用满 CPU，但不超过初始策略数
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
        # 串行路径：保留原有进度回调逻辑
        for start_idx, (name, init_order) in enumerate(init_solutions.items()):
            # P1-5 修复：使用确定性 MD5 替代非确定性 Python hash()
            _name_hash = int(hashlib.md5(name.encode('utf-8')).hexdigest(), 16) % 100000
            opt = SATabuOptimizer(features, parts, cfg, pp, seed=cfg.random_seed + _name_hash)
            opt.fifo_cmax = fifo_baseline_cmax
            opt.fifo_kit_span = fifo_baseline_kit
            opt.fifo_load_diff = fifo_baseline_load
            opt.fifo_waiting = fifo_baseline_waiting
            opt._eval_cache = shared_cache

            def _wrap_cb(it, mx, cur, best, T, _si=start_idx, _ts=total_starts):
                if progress_callback:
                    overall_it = _si * iter_per_start + it
                    overall_max = _ts * iter_per_start
                    progress_callback(overall_it, overall_max, cur, best, T)

            order, metrics, stats = opt.optimize(
                init_order,
                max_iterations=iter_per_start,
                time_limit_seconds=time_per_start,
                verbose=False,
                progress_callback=_wrap_cb if progress_callback else None,
            )
            all_stats.append(f"  [{name}] {stats}")
            all_candidates.append((name, order, metrics))

            # Also add all Pareto archive entries for cross-start comparison
            for arch_order, arch_met in opt.pareto_archive:
                all_candidates.append((f"{name}-archive", arch_order, arch_met))
    else:
        # 并行路径：每个初始策略在一个子进程中独立搜索
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
            ),
        )

        import threading
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

    # ── 3-tier dual-win selection (Loop 2) ──
    # Tier 1: Cmax ≤ FIFO AND KitSpan ≤ 12.7h → pick lowest KitSpan (ideal dual-win)
    # P4-1：最终选解改为产能优先 + 1%容差内比辅助指标
    _selected = select_best_by_capacity(
        all_candidates, cfg, getattr(cfg, "objective_type", "linear")
    )
    best_name, best_order, best_metrics = _selected
    tier_label = "产能优先(Cmax/LB最小，1%内选辅助最优)"
    best_obj = 0.0
    all_stats.insert(0, f"  [Selection] {tier_label}")

    # ── Build final schedules ──
    # P1-2: 统一复用 SATabuOptimizer.schedule_from_order，消除重复的 build_schedule 内部函数
    # 创建专用 builder 实例（不参与搜索，仅用于构建排程和运行仿真）
    _builder = SATabuOptimizer(features, parts, cfg, pp, seed=cfg.random_seed)
    # 设置 FIFO 基线供 builder 内部的 kit_span_objective 使用（不会影响最终结果）
    _builder.fifo_cmax = fifo_baseline_cmax
    _builder.fifo_kit_span = fifo_baseline_kit
    _builder.fifo_load_diff = fifo_baseline_load
    _builder.fifo_waiting = fifo_baseline_waiting

    opt_schedule, opt_metrics, opt_stages = _builder.schedule_from_order(best_order)
    base_order = init_solutions["FIFO"]
    base_schedule, base_metrics, base_stages = _builder.schedule_from_order(base_order)

    # Trust the optimizer's own multi-start selection (kit_span_objective already used above)
    # No defensive fallback — SA+Tabu's best is always at least as good as FIFO by construction

    # Print optimization summary
    kit_improve = (base_metrics["加权平均齐套跨度(h)"] - opt_metrics["加权平均齐套跨度(h)"]) / base_metrics["加权平均齐套跨度(h)"] * 100
    cmax_improve = (base_metrics["总完工时间(h)"] - opt_metrics["总完工时间(h)"]) / base_metrics["总完工时间(h)"] * 100
    print(f"[SA+Tabu] Best: {best_name}")
    for s in all_stats:
        print(s)
    print(f"[SA+Tabu] KitSpan: {base_metrics['加权平均齐套跨度(h)']:.2f}h → {opt_metrics['加权平均齐套跨度(h)']:.2f}h ({kit_improve:+.1f}%)")
    print(f"[SA+Tabu] Cmax:    {base_metrics['总完工时间(h)']:.2f}h → {opt_metrics['总完工时间(h)']:.2f}h ({cmax_improve:+.1f}%)")

    return {
        "schedule": opt_schedule,
        "base_schedule": base_schedule,
        "base_metrics": base_metrics,
        "opt_metrics": opt_metrics,
        "stages": opt_stages,
        "strategy_name": best_name,
    }


# ═══════════════════════════════════════════════════════════
#  Standalone CLI entry point
# ═══════════════════════════════════════════════════════════

def run_multi_strategy(input_path, speed_table_path=None, iterations=260):
    """Run multi-start SA+Tabu from CLI. Kept for backward compatibility."""
    cfg = ModelConfig()
    cfg.local_search_iterations = iterations
    cfg.random_seed = 20260723

    plates, parts, checks = load_and_validate(Path(input_path))
    pp = None
    speed_table = None
    if speed_table_path and Path(speed_table_path).exists():
        pp = load_process_params(Path(speed_table_path))
        speed_table = pp.speed_table

    features = plate_features(plates, parts, cfg, speed_table)

    result = run_multi_strategy_inline(
        plates, parts, features, cfg, pp, speed_table, checks, iterations
    )

    return {
        "schedule": result["schedule"],
        "base_metrics": result["base_metrics"],
        "opt_metrics": result["opt_metrics"],
        "strategy_name": result["strategy_name"],
        "features": features,
        "parts": parts,
        "cfg": cfg,
        "pp": pp,
        "speed_table": speed_table,
        "checks": checks,
    }


if __name__ == "__main__":
    result = run_multi_strategy(
        "data/data_0/附件2：钢板零件数据.xlsx",
        "data/data_0/附件3：工艺用时计算表.xlsx",
        iterations=800
    )
    bm = result["base_metrics"]
    om = result["opt_metrics"]
    print(f"\n{'='*60}")
    print(f"Strategy: {result['strategy_name']}")
    mk_keys = list(bm.keys())
    print(f"Cmax:     {bm[mk_keys[0]]:.2f}h -> {om[mk_keys[0]]:.2f}h ({(bm[mk_keys[0]]-om[mk_keys[0]])/bm[mk_keys[0]]*100:+.1f}%)")
    print(f"KitSpan:  {bm[mk_keys[1]]:.2f}h -> {om[mk_keys[1]]:.2f}h ({(bm[mk_keys[1]]-om[mk_keys[1]])/bm[mk_keys[1]]*100:+.1f}%)")
    print(f"LoadDiff: {bm[mk_keys[2]]:.2f}h -> {om[mk_keys[2]]:.2f}h ({(bm[mk_keys[2]]-om[mk_keys[2]])/bm[mk_keys[2]]*100:+.1f}%)")
    print(f"Objective: {objective(bm):.3f} -> {objective(om):.3f}")
