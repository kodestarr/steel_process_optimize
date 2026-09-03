"""
DRL Pre-training: bootstrap the Q-Learning agent from SA+Tabu trajectories.

Runs SA+Tabu on all 9 datasets (data_0 through data_8), recording
(state, action, reward, next_state) transitions from the best trajectories.
These transitions are then used to train the Q-table via batch Q-learning,
so the DRL agent starts with informed decisions rather than random exploration.

Usage: python pretrain_drl.py [--output drl_qtable.npy]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))


def pretrain(
    datasets: list[Path] | None = None,
    output_path: Path = Path("drl_qtable.npy"),
    iterations_per_dataset: int = 300,
    q_learning_epochs: int = 50,
) -> np.ndarray:
    """
    Pre-train the DRL Q-table from SA+Tabu runs on multiple datasets.

    Returns the trained Q-table (243 × 24 numpy array).
    """
    from steel_schedule_model import (
        ModelConfig, load_and_validate, load_process_params,
        plate_features, machine_names, simulate, objective,
    )
    from improved_optimizer import SATabuOptimizer
    from drl_optimizer import DRLAgent, WEIGHT_PROFILES, OPERATORS

    agent = DRLAgent()  # Q-table: 243 states × 24 actions (8 ops × 3 weights)

    # Default datasets if not provided
    if datasets is None:
        data_dir = Path("data")
        datasets = sorted(data_dir.glob("data_*"))

    all_transitions: list[tuple[int, int, float, int, bool]] = []

    print("=" * 60)
    print("  DRL Pre-training: Collecting SA+Tabu Trajectories")
    print(f"  Datasets: {len(datasets)}")
    print(f"  Iterations per dataset: {iterations_per_dataset}")
    print("=" * 60)

    for ds_idx, ds_path in enumerate(datasets):
        plate_file = ds_path / "附件2：钢板零件数据.xlsx"
        speed_file = ds_path / "附件3：工艺用时计算表.xlsx"

        if not plate_file.exists():
            print(f"  [{ds_idx+1}/{len(datasets)}] {ds_path.name}: SKIP (missing data)")
            continue

        t0 = time.time()

        try:
            plates, parts, checks = load_and_validate(plate_file)
            pp = load_process_params(speed_file) if speed_file.exists() else None
        except Exception as e:
            print(f"  [{ds_idx+1}/{len(datasets)}] {ds_path.name}: SKIP ({e})")
            continue

        cfg = ModelConfig()
        cfg.cutting_machines = 3
        cfg.local_search_iterations = iterations_per_dataset
        cfg.random_seed = 20260723 + ds_idx

        features = plate_features(plates, parts, cfg, pp.speed_table if pp else None)

        # ── P0-1 修复：按列名匹配而非硬编码位置索引 ──
        _name_cands = [c for c in features.columns if '套料图名' in c or 'plate' in c.lower()]
        name_col = _name_cands[0] if _name_cands else features.columns[2]
        _seq_cands = [c for c in features.columns if '序号' in c or 'seq' in c.lower()]
        seq_col = _seq_cands[0] if _seq_cands else features.columns[0]
        _seg_cands = [c for c in features.columns if '分段号' in c or 'section' in c.lower()]
        seg_col = _seg_cands[0] if _seg_cands else features.columns[1]
        cut_cands = [c for c in features.columns if '工时' in c and 'min' in c]
        if not cut_cands:
            raise KeyError(f"未找到切割工时列，可用列: {list(features.columns)}")
        cut_col = cut_cands[0]
        pri_cands = [c for c in features.columns if '优先' in c or '最低' in c]
        if not pri_cands:
            raise KeyError(f"未找到优先级列，可用列: {list(features.columns)}")
        pri_col = pri_cands[0]

        # Build initial solutions
        init_solutions = {
            "FIFO": features.sort_values(seq_col, kind="stable")[name_col].tolist(),
            "SegPriShort": features.sort_values(
                [seg_col, pri_col, cut_col], ascending=[True, True, True], kind="stable"
            )[name_col].tolist(),
            "SegPriLong": features.sort_values(
                [seg_col, pri_col, cut_col], ascending=[True, True, False], kind="stable"
            )[name_col].tolist(),
        }

        dataset_transitions = 0

        for start_name, init_order in init_solutions.items():
            opt = SATabuOptimizer(features, parts, cfg, pp, seed=cfg.random_seed + hash(start_name) % 100000)

            _, init_met, _ = opt.schedule_from_order(init_order)
            current_order = init_order[:]
            current_met = init_met
            current_obj = objective(init_met)
            best_obj = current_obj

            T = opt.T_start
            kit_history: list[float] = [init_met["加权平均齐套跨度(h)"]]
            obj_history: list[float] = [current_obj]
            no_improve = 0

            for it in range(iterations_per_dataset // len(init_solutions)):
                if time.time() - t0 > 180:  # 3 min max per dataset
                    break

                # ── Build state ──
                progress = it / max(iterations_per_dataset // len(init_solutions) - 1, 1)
                if progress < 0.33:
                    phase = 0
                elif progress < 0.66:
                    phase = 1
                else:
                    phase = 2

                if T > opt.T_start * 0.5:
                    temp_zone = 0
                elif T > opt.T_start * 0.1:
                    temp_zone = 1
                else:
                    temp_zone = 2

                if no_improve <= 2:
                    streak = 0
                elif no_improve <= 8:
                    streak = 1
                else:
                    streak = 2

                if len(kit_history) >= 10:
                    recent_kit = kit_history[-10:]
                    kit_trend_val = (recent_kit[-1] - recent_kit[0]) / max(abs(recent_kit[0]), 1e-6)
                else:
                    kit_trend_val = 0.0

                if kit_trend_val < -0.05:
                    kit_trend = 0
                elif kit_trend_val < 0.05:
                    kit_trend = 1
                else:
                    kit_trend = 2

                load_diff = current_met.get("切割负载差(h)", 0.5)
                if load_diff < 0.3:
                    load_zone = 0
                elif load_diff < 1.0:
                    load_zone = 1
                else:
                    load_zone = 2

                state = (phase * 81 + temp_zone * 27 + streak * 9 + kit_trend * 3 + load_zone)

                # ── Select action (ε-greedy during collection) ──
                import random
                if random.random() < 0.15:  # 15% random exploration
                    action = random.randint(0, 26)  # P0-3 修复：27 actions (9 ops × 3 weights)
                else:
                    action = int(np.argmax(agent.q_table[state]))

                # ── Execute action ──
                operator, _weights = agent.decode_action(action)

                if operator == "swap":
                    neighbor = opt._swap(current_order)
                elif operator == "insert":
                    neighbor = opt._insert(current_order)
                elif operator == "block_reverse":
                    neighbor = opt._block_reverse(current_order)
                elif operator == "seg_shuffle":
                    neighbor = opt._segment_shuffle(current_order)
                elif operator == "seg_priority_sort":
                    if len(opt.seg_map) >= 2:
                        seg = opt.rng.choice(list(opt.seg_map.keys()))
                        seg_plates = opt.seg_map[seg]
                        if len(seg_plates) >= 3:
                            seg_f = opt.fidx.loc[seg_plates].sort_values([pri_col, cut_col], ascending=[True, True])
                            neighbor = current_order[:]
                            indices = [i for i, n in enumerate(neighbor) if n in seg_plates]
                            for idx, (pn, _) in zip(indices, seg_f.iterrows()):
                                neighbor[idx] = str(pn)
                        else:
                            neighbor = opt._swap(current_order)
                    else:
                        neighbor = opt._swap(current_order)
                elif operator == "balanced_two_opt":
                    i = opt.rng.randint(0, max(1, opt.n - 30))
                    j = min(opt.n, i + opt.rng.randint(5, 25))
                    neighbor = current_order[:]
                    neighbor[i:j] = reversed(neighbor[i:j])
                elif operator == "cross_rebalance":
                    neighbor = opt._cross_machine_rebalance(current_order)
                elif operator == "kit_cluster":
                    neighbor = opt._kit_cluster(current_order)
                else:
                    neighbor = opt._swap(current_order)

                # Tabu
                tkey = opt._hash_order(neighbor)
                if tkey in opt.tabu:
                    continue
                opt.tabu.add(tkey)

                # Evaluate
                _, met, _ = opt.schedule_from_order(neighbor)
                new_obj = objective(met)
                delta = new_obj - current_obj

                accepted = False
                prev_ks = current_met["加权平均齐套跨度(h)"]
                new_ks = met["加权平均齐套跨度(h)"]

                # P0-9 FIX: Save prev_obj BEFORE overwriting current_obj, so reward delta is correct
                _prev_obj = current_obj

                if delta < 0:
                    current_order = neighbor
                    current_met = met
                    current_obj = new_obj
                    accepted = True
                    no_improve = 0
                    if new_obj < best_obj:
                        best_obj = new_obj
                else:
                    p_accept = np.exp(-delta / max(T, 1e-8))
                    if opt.rng.random() < p_accept:
                        current_order = neighbor
                        current_met = met
                        current_obj = new_obj
                        accepted = True
                    no_improve += 1

                kit_history.append(new_ks)
                obj_history.append(new_obj)

                # ── Compute next state (P1-2 修复：基于动作执行后的真实状态) ──
                new_progress = (it + 1) / max(iterations_per_dataset // len(init_solutions) - 1, 1)
                if new_progress < 0.33:
                    n_phase = 0
                elif new_progress < 0.66:
                    n_phase = 1
                else:
                    n_phase = 2

                new_T = T * opt.cooling_rate
                if no_improve >= opt.patience:
                    new_T = max(new_T, opt.T_start * opt.reheat_factor)
                new_T = max(new_T, opt.T_end)

                if new_T > opt.T_start * 0.5:
                    n_temp_zone = 0
                elif new_T > opt.T_start * 0.1:
                    n_temp_zone = 1
                else:
                    n_temp_zone = 2

                if no_improve <= 2:
                    n_streak = 0
                elif no_improve <= 8:
                    n_streak = 1
                else:
                    n_streak = 2

                # P1-2 修复：重新计算动作执行后的 kit_trend 和 load_zone
                if len(kit_history) >= 10:
                    recent_kit_n = kit_history[-10:]
                    kit_trend_n_val = (recent_kit_n[-1] - recent_kit_n[0]) / max(abs(recent_kit_n[0]), 1e-6)
                else:
                    kit_trend_n_val = 0.0

                if kit_trend_n_val < -0.05:
                    kit_trend_n = 0
                elif kit_trend_n_val < 0.05:
                    kit_trend_n = 1
                else:
                    kit_trend_n = 2

                # 使用动作执行后的新 metrics 计算 load_zone
                new_load_diff = new_met.get("切割负载差(h)", current_met.get("切割负载差(h)", 0.5))
                if new_load_diff < 0.3:
                    load_zone_n = 0
                elif new_load_diff < 1.0:
                    load_zone_n = 1
                else:
                    load_zone_n = 2

                next_state_val = (n_phase * 81 + n_temp_zone * 27 + n_streak * 9 + kit_trend_n * 3 + load_zone_n)

                # ── Compute reward ── (P0-9 FIX: use _prev_obj for correct delta)
                reward = agent.compute_reward(_prev_obj, new_obj, accepted, prev_ks, new_ks)

                # ── Record transition ──
                all_transitions.append((state, action, reward, next_state_val, False))
                dataset_transitions += 1

                # Cool / reheat
                T = new_T
                if no_improve >= opt.patience:
                    no_improve = 0

        elapsed = time.time() - t0
        print(f"  [{ds_idx+1}/{len(datasets)}] {ds_path.name}: "
              f"{checks['钢板数']}p/{checks['零件数']}pt, "
              f"{dataset_transitions} transitions, {elapsed:.1f}s")

    # ── Batch Q-Learning on collected transitions ──
    print(f"\n{'='*60}")
    print(f"  Batch Q-Learning: {len(all_transitions)} total transitions")
    print(f"  Epochs: {q_learning_epochs}")
    print("=" * 60)

    import random
    rng = random.Random(42)

    for epoch in range(q_learning_epochs):
        rng.shuffle(all_transitions)
        total_loss = 0.0
        for s, a, r, ns, d in all_transitions:
            target = r + (0 if d else agent.gamma * np.max(agent.q_table[ns]))
            old_val = agent.q_table[s, a]
            agent.q_table[s, a] += agent.alpha * (target - old_val)
            total_loss += abs(target - old_val)

        if epoch % 10 == 0 or epoch == q_learning_epochs - 1:
            print(f"  Epoch {epoch:3d}: avg_loss={total_loss/max(1,len(all_transitions)):.6f}")

    # ── Save Q-table ──
    np.save(output_path, agent.q_table)
    print(f"\n  Q-table saved to {output_path} ({agent.q_table.shape})")

    # Print Q-table statistics
    nonzero = np.count_nonzero(agent.q_table)
    print(f"  Non-zero Q-values: {nonzero}/{agent.q_table.size} ({100*nonzero/agent.q_table.size:.1f}%)")
    print(f"  Q-value range: [{agent.q_table.min():.4f}, {agent.q_table.max():.4f}]")

    return agent.q_table


if __name__ == "__main__":
    pretrain()
