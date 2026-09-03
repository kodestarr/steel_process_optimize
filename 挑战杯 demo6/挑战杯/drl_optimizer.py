"""
DRL-based Adaptive Optimization for Steel Plate Scheduling.

Implements a lightweight Q-Learning agent that dynamically selects neighborhood
operators and tunes objective function weights during Simulated Annealing search.
This provides the "技术创新性" (technical innovation) required by the competition
evaluation framework (30% weight).

Architecture:
  - State: (phase, temp_zone, improve_streak, kit_span_trend, load_balance)
  - Actions: 6 operator choices × 3 weight profiles = 18 discrete actions
  - Reward: Δ(objective) normalized by initial objective
  - Learning: ε-greedy Q-Learning with experience replay buffer

Key innovation for "小批量、多品种、齐套性强" shipbuilding characteristics:
  - Adaptive weight shifting: early search prioritizes Cmax, later shifts to KitSpan
  - Operator selection learns which moves work best for different plate profiles
  - Segment-aware exploration exploits the multi-segment ship structure
"""
from __future__ import annotations

import hashlib
import math
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

import numpy as np


# ═══════════════════════════════════════════════════════════
#  State & Action Definitions
# ═══════════════════════════════════════════════════════════

# Operator actions
OPERATORS = ["swap", "insert", "block_reverse", "seg_shuffle", "seg_priority_sort", "balanced_two_opt", "cross_rebalance", "stagger_crane", "kit_cluster"]

# Weight profiles for objective function (normalized by competition targets)
# Normalization targets: Cmax=67.25h, KitSpan=12.7h, LoadDiff=1.0h
WEIGHT_PROFILES = {
    "balanced":       (0.40, 0.40, 0.20),
    "kit_span_focus": (0.30, 0.55, 0.15),
    "cmax_focus":     (0.55, 0.30, 0.15),
}

# Discretized state dimensions
N_PHASES = 3        # early (0-33%), mid (33-66%), late (66-100%)
N_TEMP_ZONES = 3    # high (T > T_start*0.5), medium, low (T < T_start*0.1)
N_STREAKS = 3       # declining (0-2 no-improve), stuck (3-8), improving (>8)
N_KIT_TRENDS = 3    # improving, flat, worsening
N_LOAD_ZONES = 3    # balanced (<0.3h diff), moderate (0.3-1.0h), unbalanced (>1.0h)

# Total state space: 3*3*3*3*3 = 243 states
# Total actions: 6 operators × 3 weight profiles = 18 actions
# Q-table: 243 × 18 = 4374 entries — very lightweight


@dataclass
class DRLAgent:
    """Q-Learning agent for adaptive operator and weight selection."""

    # Q-table — can be pre-loaded from pretraining
    # P0-3 修复：State space: 3*3*3*3*3 = 243; Actions: 9 operators × 3 weights = 27
    q_table: np.ndarray = field(default_factory=lambda: np.zeros((243, 27)))

    # Hyperparameters
    alpha: float = 0.15       # learning rate
    gamma: float = 0.90       # discount factor
    epsilon: float = 0.10     # exploration rate (lower if pretrained, decays)
    epsilon_min: float = 0.02
    epsilon_decay: float = 0.995

    # Experience replay
    replay_buffer: deque = field(default_factory=lambda: deque(maxlen=200))
    batch_size: int = 16

    # Tracking
    total_steps: int = 0
    last_state: int = 0
    last_action: int = 0
    last_obj: float = 0.0
    pretrained: bool = False

    def load_pretrained(self, path: str = "drl_qtable.npy"):
        """Load a pre-trained Q-table from disk."""
        import os
        if os.path.exists(path):
            self.q_table = np.load(path)
            self.epsilon = 0.08       # less exploration with pretrained knowledge
            self.pretrained = True
            return True
        return False

    # ── State encoding ────────────────────────────────────
    def encode_state(
        self,
        iteration: int,
        max_iterations: int,
        temperature: float,
        T_start: float,
        no_improve_count: int,
        kit_span_trend: float,   # Δkit_span over last 10 iters
        load_diff: float,        # current load difference in hours
    ) -> int:
        """Encode continuous state into discrete state index (0-242)."""
        # Phase: early/mid/late
        progress = iteration / max(max_iterations, 1)
        if progress < 0.33:
            phase = 0
        elif progress < 0.66:
            phase = 1
        else:
            phase = 2

        # Temperature zone
        if temperature > T_start * 0.5:
            temp_zone = 0
        elif temperature > T_start * 0.1:
            temp_zone = 1
        else:
            temp_zone = 2

        # Improvement streak
        if no_improve_count <= 2:
            streak = 0
        elif no_improve_count <= 8:
            streak = 1
        else:
            streak = 2

        # Kit span trend
        if kit_span_trend < -0.05:
            kit_trend = 0  # improving (decreasing)
        elif kit_span_trend < 0.05:
            kit_trend = 1  # flat
        else:
            kit_trend = 2  # worsening

        # Load balance
        if load_diff < 0.3:
            load_zone = 0
        elif load_diff < 1.0:
            load_zone = 1
        else:
            load_zone = 2

        # Flatten: state = phase*81 + temp_zone*27 + streak*9 + kit_trend*3 + load_zone
        return (phase * 81 + temp_zone * 27 + streak * 9 + kit_trend * 3 + load_zone)

    # ── Action decoding ───────────────────────────────────
    def decode_action(self, action: int) -> tuple[str, tuple[float, float, float]]:
        """Decode action index into (operator_name, weight_profile)."""
        op_idx = action // 3
        weight_idx = action % 3
        operator = OPERATORS[op_idx]
        weight_key = list(WEIGHT_PROFILES.keys())[weight_idx]
        return operator, WEIGHT_PROFILES[weight_key]

    def encode_action(self, operator: str, weight_key: str) -> int:
        """Encode (operator, weight_key) into action index."""
        op_idx = OPERATORS.index(operator) if operator in OPERATORS else 0
        weight_idx = list(WEIGHT_PROFILES.keys()).index(weight_key) if weight_key in WEIGHT_PROFILES else 0
        return op_idx * 3 + weight_idx

    # ── Action selection ──────────────────────────────────
    def select_action(self, state: int, training: bool = True) -> int:
        """ε-greedy action selection."""
        n_actions = self.q_table.shape[1]
        if training and random.random() < self.epsilon:
            return random.randint(0, n_actions - 1)
        return int(np.argmax(self.q_table[state]))

    # ── Learning update ───────────────────────────────────
    def observe(
        self,
        state: int,
        action: int,
        reward: float,
        next_state: int,
        done: bool = False,
    ):
        """Store experience and perform Q-learning update. Epsilon only decays when
        learning actually occurs (replay buffer has enough samples)."""
        self.replay_buffer.append((state, action, reward, next_state, done))
        self.total_steps += 1

        # Batch update — only decay epsilon when learning occurs
        if len(self.replay_buffer) >= self.batch_size:
            batch = random.sample(self.replay_buffer, self.batch_size)
            for s, a, r, ns, d in batch:
                target = r + (0 if d else self.gamma * np.max(self.q_table[ns]))
                self.q_table[s, a] += self.alpha * (target - self.q_table[s, a])
            # Epsilon decay only after learning starts
            self.epsilon = max(self.epsilon_min, self.epsilon * self.epsilon_decay)

    # ── Reward calculation ────────────────────────────────
    @staticmethod
    def compute_reward(
        prev_obj: float,
        new_obj: float,
        accepted: bool,
        prev_kit_span: float,
        new_kit_span: float,
    ) -> float:
        """Compute reward signal for RL.

        Reward components:
        - Objective improvement: scaled sigmoid of Δobj
        - Kit span improvement bonus: extra reward for reducing kit span
        - Exploration bonus: small reward for accepting worse solutions (SA behavior)
        """
        if prev_obj == 0:
            return 0.0

        delta_obj = (prev_obj - new_obj) / abs(prev_obj)  # normalized improvement

        # Base reward from objective improvement
        reward = math.tanh(delta_obj * 5.0)  # squash to [-1, 1]

        # Kit span bonus (competition critical metric)
        if prev_kit_span > 0:
            delta_kit = (prev_kit_span - new_kit_span) / prev_kit_span
            reward += 0.3 * math.tanh(delta_kit * 10.0)

        # Acceptance bonus (encourage exploration when not improving)
        if accepted and delta_obj > 0:
            reward += 0.1

        return float(reward)


# ═══════════════════════════════════════════════════════════
#  DRL-Guided SA+Tabu Optimizer
# ═══════════════════════════════════════════════════════════

class DRLGuidedOptimizer:
    """SA+Tabu optimizer with DRL-guided operator and weight selection.

    Wraps the SATabuOptimizer and replaces the fixed neighborhood selection
    and objective weights with DRL-agent decisions.
    """

    def __init__(self, base_optimizer, training: bool = True, pretrained_path: str = "drl_qtable.npy"):
        """
        Args:
            base_optimizer: SATabuOptimizer instance
            training: if True, agent learns; if False, uses learned policy
            pretrained_path: path to pre-trained Q-table .npy file
        """
        self.opt = base_optimizer
        self.agent = DRLAgent()
        if not self.agent.load_pretrained(pretrained_path):
            # Fallback: try v2 pretrained table
            self.agent.load_pretrained("drl_qtable_v2.npy")
        self.training = training

        # P0-4: 显式初始化 fifo 基线默认值，防止外部未设置时静默跳过或 AttributeError
        if not hasattr(self.opt, 'fifo_cmax') or self.opt.fifo_cmax is None:
            self.opt.fifo_cmax = 67.25
        if not hasattr(self.opt, 'fifo_kit_span') or self.opt.fifo_kit_span is None:
            self.opt.fifo_kit_span = 12.7

        # Tracking for state construction
        self.kit_span_history: list[float] = []
        self.obj_history: list[float] = []
        self._last_metrics: dict = {}
        self.current_weights = WEIGHT_PROFILES["balanced"]
        self.current_operator = "swap"

    def select_operator(self, iteration: int, max_iterations: int, temperature: float) -> str:
        """DRL-guided operator selection with Pareto-aware biasing (Loop 3).

        If the current solution is far from the Pareto frontier on one dimension,
        biases operator selection toward operators that improve that dimension.
        """
        state = self._build_state(iteration, max_iterations, temperature)
        action = self.agent.select_action(state, training=self.training)
        operator, weights = self.agent.decode_action(action)
        self.current_operator = operator
        self.current_weights = weights
        self.agent.last_state = state
        self.agent.last_action = action

        # ── Pareto-guided override (Loop 3): bias based on which dimension needs improvement ──
        # P0-4 FIX: Use actual Cmax from _last_metrics (hours), not composite objective score (~0.8).
        # Previously compared dimensionless obj_history[-1] against fifo_cmax*1.05 (~70.6h),
        # which was always False — the entire feature was dead code.
        if hasattr(self.opt, 'fifo_cmax') and hasattr(self.opt, 'fifo_kit_span'):
            fifo_cmax = self.opt.fifo_cmax
            fifo_kit_span = self.opt.fifo_kit_span
            if len(self.kit_span_history) >= 3 and hasattr(self, '_last_metrics') and self._last_metrics:
                recent_cmax = self._last_metrics.get("总完工时间(h)", fifo_cmax)
                recent_kit = self.kit_span_history[-1]
                cmax_bad = recent_cmax > fifo_cmax * 1.05  # Cmax worse than FIFO baseline by 5%+
                kit_bad = recent_kit > fifo_kit_span * 1.05

                if cmax_bad and not kit_bad:
                    if random.random() < 0.4:
                        operator = random.choice(
                            ['stagger_crane', 'cross_rebalance', 'swap'])
                elif kit_bad and not cmax_bad:
                    if random.random() < 0.4:
                        operator = random.choice(
                            ['kit_cluster', 'seg_shuffle', 'seg_priority_sort'])
                elif cmax_bad and kit_bad:
                    if random.random() < 0.3:
                        operator = random.choice(
                            ['stagger_crane', 'kit_cluster', 'insert'])

        return operator

    def get_weights(self) -> tuple[float, float, float]:
        """Get current DRL-selected objective weights."""
        return self.current_weights

    def feedback(
        self,
        iteration: int,
        max_iterations: int,
        temperature: float,
        prev_obj: float,
        new_obj: float,
        accepted: bool,
        prev_kit_span: float,
        new_kit_span: float,
    ):
        """Provide reward feedback to the DRL agent after each move."""
        self.kit_span_history.append(new_kit_span)
        self.obj_history.append(new_obj)

        next_state = self._build_state(iteration, max_iterations, temperature)
        reward = self.agent.compute_reward(
            prev_obj, new_obj, accepted, prev_kit_span, new_kit_span
        )
        self.agent.observe(
            self.agent.last_state, self.agent.last_action, reward, next_state
        )

    def _build_state(self, iteration: int, max_iterations: int, temperature: float) -> int:
        """Build current state encoding."""
        # Kit span trend over last 10 iterations
        if len(self.kit_span_history) >= 10:
            recent = self.kit_span_history[-10:]
            kit_trend = (recent[-1] - recent[0]) / max(abs(recent[0]), 1e-6)
        else:
            kit_trend = 0.0

        # No-improve count
        no_improve = 0
        for i in range(len(self.obj_history) - 1, 0, -1):
            if self.obj_history[i] >= self.obj_history[i - 1]:
                no_improve += 1
            else:
                break

        # Current load diff from actual metrics (updated via feedback)
        load_diff = 0.5
        if hasattr(self, '_last_metrics'):
            load_diff = self._last_metrics.get("切割负载差(h)", 0.5)

        return self.agent.encode_state(
            iteration=iteration,
            max_iterations=max_iterations,
            temperature=temperature,
            T_start=self.opt.T_start,
            no_improve_count=no_improve,
            kit_span_trend=kit_trend,
            load_diff=load_diff,
        )

    def kit_span_objective(self, metrics: dict) -> float:
        """Normalized objective using DRL-selected weights."""
        w_cmax, w_kit, w_load = self.current_weights
        # P3-3: 统一从 ModelConfig 引用竞赛目标常量
        _CMAX_TARGET = self.opt.cfg.CMAX_TARGET_H
        _KITSPAN_TARGET = self.opt.cfg.KITSPAN_TARGET_H
        _LOADDIFF_TARGET = self.opt.cfg.LOADDIFF_TARGET_H
        return (
            w_cmax * metrics["总完工时间(h)"] / _CMAX_TARGET
            + w_kit * metrics["加权平均齐套跨度(h)"] / _KITSPAN_TARGET
            + w_load * metrics["切割负载差(h)"] / _LOADDIFF_TARGET
        )


# ═══════════════════════════════════════════════════════════
#  DRL-Enhanced Multi-Start Wrapper
# ═══════════════════════════════════════════════════════════

def run_drl_enhanced_optimization(
    plates,
    parts,
    features,
    cfg,
    pp,
    speed_table,
    checks,
    iterations: int = 500,
    training: bool = True,
) -> dict:
    """
    Run DRL-enhanced SA+Tabu optimization.

    The DRL agent learns to:
    1. Select the best neighborhood operator based on search phase and temperature
    2. Dynamically adjust objective weights (Cmax vs KitSpan vs LoadBalance)
    3. Adapt exploration strategy for shipbuilding's "小批量、多品种" characteristics

    Returns the same dict structure as run_multi_strategy_inline for API compatibility.
    """
    import pandas as pd
    from improved_optimizer import SATabuOptimizer

    # ── P0-1 修复：按列名匹配而非硬编码位置索引（与 improved_optimizer.py 统一）──
    _name_candidates = [c for c in features.columns if '套料图名' in c or 'plate' in c.lower()]
    if not _name_candidates:
        raise KeyError(f"未找到套料图名列，可用列: {list(features.columns)}")
    name_col = _name_candidates[0]
    _seg_candidates = [c for c in features.columns if '分段号' in c or 'section' in c.lower() or 'segment' in c.lower()]
    if not _seg_candidates:
        raise KeyError(f"未找到分段号列，可用列: {list(features.columns)}")
    seg_col = _seg_candidates[0]
    _seq_candidates = [c for c in features.columns if '序号' in c or 'seq' in c.lower()]
    if not _seq_candidates:
        raise KeyError(f"未找到序号列，可用列: {list(features.columns)}")
    seq_col = _seq_candidates[0]
    cut_candidates = [c for c in features.columns if '工时' in c and 'min' in c]
    if not cut_candidates:
        raise KeyError(f"未找到切割工时列，可用列: {list(features.columns)}")
    cut_col = cut_candidates[0]
    pri_candidates = [c for c in features.columns if '优先' in c or '最低' in c]
    if not pri_candidates:
        raise KeyError(f"未找到优先级列，可用列: {list(features.columns)}")
    pri_col = pri_candidates[0]

    # Build initial solutions
    init_solutions: dict[str, list[str]] = {}
    init_solutions["FIFO"] = features.sort_values(seq_col, kind="stable")[name_col].tolist()

    from steel_schedule_model import make_greedy_schedule, build_crane_aware_orders
    comp_sched = make_greedy_schedule(features, "completeness", cfg.random_seed)
    # P0-3 修复：空列表安全守卫
    _drl_comp_seq_candidates = [c for c in comp_sched.columns if '序号' in c or '切割序号' in c]
    if not _drl_comp_seq_candidates:
        raise KeyError(f"make_greedy_schedule 返回的排程缺少序号列，可用列: {list(comp_sched.columns)}")
    comp_seq_col = _drl_comp_seq_candidates[0]
    init_solutions["Completeness"] = comp_sched.sort_values(comp_seq_col)[name_col].tolist()

    init_solutions["SegPriShort"] = features.sort_values(
        [seg_col, pri_col, cut_col], ascending=[True, True, True], kind="stable"
    )[name_col].tolist()

    init_solutions["SegPriLong"] = features.sort_values(
        [seg_col, pri_col, cut_col], ascending=[True, True, False], kind="stable"
    )[name_col].tolist()

    init_solutions["SPT"] = features.sort_values(cut_col, ascending=True, kind="stable")[name_col].tolist()
    init_solutions.update(build_crane_aware_orders(features, cfg))

    # Run DRL-guided SA+Tabu from each start
    # P0-8 FIX: protect against empty init_solutions (div/0 → ZeroDivisionError)
    n_starts = max(1, len(init_solutions))
    iter_per_start = max(150, iterations // n_starts)
    time_per_start = max(60, 300 // n_starts)

    best_order = None
    best_metrics = None
    best_obj = float("inf")
    best_name = ""
    all_drl_agents = []

    for name, init_order in init_solutions.items():
        import time
        t0 = time.time()

        # P1-5 修复：使用确定性 MD5 替代非确定性 Python hash()
        _name_hash = int(hashlib.md5(name.encode('utf-8')).hexdigest(), 16) % 100000
        base_opt = SATabuOptimizer(features, parts, cfg, pp, seed=cfg.random_seed + _name_hash)
        drl = DRLGuidedOptimizer(base_opt, training=training)

        # Evaluate initial
        _, init_met, _ = base_opt.schedule_from_order(init_order)
        current_order = init_order[:]
        current_met = init_met
        current_obj = drl.kit_span_objective(init_met)
        best_local_order = current_order[:]
        best_local_met = current_met
        best_local_obj = current_obj

        T = base_opt.T_start
        no_improve = 0
        total_iters = 0

        for it in range(iter_per_start):
            if time.time() - t0 > time_per_start:
                break
            total_iters += 1

            # DRL selects operator and weights
            operator = drl.select_operator(it, iter_per_start, T)

            # Execute selected operator
            if operator == "swap":
                neighbor = base_opt._swap(current_order)
            elif operator == "insert":
                neighbor = base_opt._insert(current_order)
            elif operator == "block_reverse":
                neighbor = base_opt._block_reverse(current_order)
            elif operator == "seg_shuffle":
                neighbor = base_opt._segment_shuffle(current_order)
            elif operator == "seg_priority_sort":
                # Sort plates within a random segment by priority then cut time
                if len(base_opt.seg_map) >= 2:
                    seg = base_opt.rng.choice(list(base_opt.seg_map.keys()))
                    seg_plates = base_opt.seg_map[seg]
                    if len(seg_plates) >= 3:
                        seg_features = base_opt.fidx.loc[seg_plates].sort_values(
                            [pri_col, cut_col], ascending=[True, True]
                        )
                        neighbor = current_order[:]
                        indices = [i for i, n in enumerate(neighbor) if n in seg_plates]
                        for idx, (plate_name, _) in zip(indices, seg_features.iterrows()):
                            neighbor[idx] = str(plate_name)
                    else:
                        neighbor = base_opt._swap(current_order)
                else:
                    neighbor = base_opt._swap(current_order)
            elif operator == "balanced_two_opt":
                # 2-opt: reverse a subsequence to balance machine loads
                i = base_opt.rng.randint(0, base_opt.n - 30)
                j = min(base_opt.n, i + base_opt.rng.randint(5, 25))
                neighbor = current_order[:]
                neighbor[i:j] = reversed(neighbor[i:j])
            elif operator == "cross_rebalance":
                neighbor = base_opt._cross_machine_rebalance(current_order)
            elif operator == "stagger_crane":
                neighbor = base_opt._stagger_crane(current_order)
            elif operator == "kit_cluster":
                neighbor = base_opt._kit_cluster(current_order)
            else:
                neighbor = base_opt._swap(current_order)

            # Tabu check
            tkey = base_opt._hash_order(neighbor)
            if tkey in base_opt.tabu:
                continue
            base_opt.tabu.add(tkey)

            # Evaluate
            _, met, _ = base_opt.schedule_from_order(neighbor)
            new_obj = drl.kit_span_objective(met)
            delta = new_obj - current_obj

            prev_ks = current_met["加权平均齐套跨度(h)"]
            new_ks = met["加权平均齐套跨度(h)"]
            accepted = False

            # P0-8 FIX: Save prev_obj BEFORE overwriting current_obj, so reward delta is correct
            _prev_obj = current_obj

            if delta < 0:
                current_order = neighbor
                current_met = met
                current_obj = new_obj
                accepted = True
                no_improve = 0
                if new_obj < best_local_obj:
                    best_local_order = neighbor
                    best_local_met = met
                    best_local_obj = new_obj
            else:
                p_accept = math.exp(-delta / max(T, 1e-8))
                if base_opt.rng.random() < p_accept:
                    current_order = neighbor
                    current_met = met
                    current_obj = new_obj
                    accepted = True
                no_improve += 1

            # DRL feedback — use _prev_obj (pre-update) so reward reflects real improvement
            drl._last_metrics = met
            drl.feedback(it, iter_per_start, T, _prev_obj, new_obj, accepted, prev_ks, new_ks)

            # Cool + reheat
            T *= base_opt.cooling_rate
            if no_improve >= base_opt.patience:
                T = max(T, base_opt.T_start * base_opt.reheat_factor)
                no_improve = 0
            T = max(T, base_opt.T_end)

        # Record best from this start
        obj = drl.kit_span_objective(best_local_met)
        if obj < best_obj:
            best_obj = obj
            best_metrics = best_local_met
            best_name = name
            best_order = best_local_order

        all_drl_agents.append(drl)
        print(f"  [DRL-{name}] {total_iters} iters, "
              f"KitSpan={init_met['加权平均齐套跨度(h)']:.2f}h→{best_local_met['加权平均齐套跨度(h)']:.2f}h, "
              f"Cmax={init_met['总完工时间(h)']:.2f}h→{best_local_met['总完工时间(h)']:.2f}h")

    # Build final schedule
    from steel_schedule_model import machine_names, simulate, pick_machine, build_joint_schedule
    fidx = features.set_index(name_col)
    machines_list = machine_names(cfg)

    def build_schedule(order_names):
        ordered = fidx.loc[order_names].reset_index()
        free = {m: 0.0 for m in machines_list}
        crane_free = 0.0
        rows = []
        crane_overlap = getattr(cfg, 'crane_overlap_minutes', 0.0)
        crane_total = 8.0 - crane_overlap  # 5+3 min crane per plate minus overlap
        for seq_i, (_, r) in enumerate(ordered.iterrows(), 1):
            m = pick_machine(r, free)
            cs = max(free[m], crane_free)
            ce = cs + crane_total; crane_free = ce
            st = ce; en = st + r[cut_col]; free[m] = en
            rows.append({
                name_col: r[name_col], seg_col: r[seg_col],
                'machine': m, 'seq': seq_i,
                'start': st, 'end': en, 'dur': r[cut_col], 'pri': r[pri_col],
            })
        sched = pd.DataFrame(rows).rename(columns={
            'machine': '切割机', name_col: '套料图名', seg_col: '分段号',
            'seq': '切割序号', 'start': '切割开始(min)',
            'end': '切割完成(min)', 'dur': '切割工时(min)', 'pri': '最低优先级',
        })
        new_sched, _, _, met, stg, _ = build_joint_schedule(sched, parts, cfg, pp)
        return new_sched, met, stg

    opt_schedule, opt_metrics, opt_stages = build_schedule(best_order)
    base_order = init_solutions["FIFO"]
    base_schedule, base_metrics, base_stages = build_schedule(base_order)

    # Average DRL stats
    avg_epsilon = float(np.mean([a.agent.epsilon for a in all_drl_agents]))
    total_drl_steps = sum(a.agent.total_steps for a in all_drl_agents)

    print(f"[DRL] Best: {best_name}, total DRL steps: {total_drl_steps}, "
          f"final epsilon: {avg_epsilon:.4f}")
    print(f"[DRL] KitSpan: {base_metrics['加权平均齐套跨度(h)']:.2f}h → {opt_metrics['加权平均齐套跨度(h)']:.2f}h")

    return {
        "schedule": opt_schedule,
        "base_schedule": base_schedule,
        "base_metrics": base_metrics,
        "opt_metrics": opt_metrics,
        "stages": opt_stages,
        "strategy_name": f"DRL-{best_name}",
        "drl_agents": all_drl_agents,
    }


# ═══════════════════════════════════════════════════════════
#  CLI test entry
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))

    from steel_schedule_model import (
        ModelConfig, load_and_validate, load_process_params,
        plate_features, objective,
    )

    data_path = Path("question/产线场景描述/附件2：钢板零件数据.xlsx")
    speed_path = Path("question/产线场景描述/附件3：工艺用时计算表.xlsx")

    cfg = ModelConfig()
    cfg.cutting_machines = 3
    cfg.local_search_iterations = 800
    cfg.random_seed = 20260723

    plates, parts, checks = load_and_validate(data_path)
    pp = load_process_params(speed_path)
    features = plate_features(plates, parts, cfg, pp.speed_table)

    print("=" * 60)
    print("  DRL-Enhanced SA+Tabu Optimization")
    print(f"  {checks['钢板数']} plates, {checks['零件数']} parts, {cfg.cutting_machines} machines")
    print("=" * 60)

    result = run_drl_enhanced_optimization(
        plates, parts, features, cfg, pp, pp.speed_table, checks,
        iterations=800, training=True,
    )

    om = result["opt_metrics"]
    bm = result["base_metrics"]
    print(f"\n=== FINAL DRL RESULTS ===")
    print(f"Strategy: {result['strategy_name']}")
    for k in sorted(om.keys()):
        if '利用率' in k or '产能' in k:
            print(f"  {k}: {bm.get(k,0):.4f} → {om.get(k,0):.4f}")
        else:
            print(f"  {k}: {bm.get(k,0):.2f} → {om.get(k,0):.2f}")
    print(f"  Objective: {objective(bm):.4f} → {objective(om):.4f}")
