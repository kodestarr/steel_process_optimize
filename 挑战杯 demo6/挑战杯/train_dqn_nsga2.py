"""Offline CPU training for the DQN machine-code seed model.

Usage from the demo directory:

    python train_dqn_nsga2.py --output dqn_machine_model.pt

The default source is `D:\\作文\\学校\\项目\\2026擂台赛\\产线场景描述`
containing the official 附件2 and 附件3 files. If your files are elsewhere,
pass them explicitly:

    python train_dqn_nsga2.py --plate-file path\\附件2：钢板零件数据.xlsx --speed-file path\\附件3：工艺用时计算表.xlsx

The script collects machine-assignment episodes from the provided attachment and
trains the small DQN on local load-impact rewards. The resulting weight file is
read by `dqn_seed_machine_maps()` when DQN+NSGA-II runs. When the file is
missing, the optimizer falls back to heuristic machine maps.
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

SCENARIO_DIR = Path(__file__).resolve().parents[2] / "产线场景描述"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data_dir", type=Path, default=SCENARIO_DIR)
    parser.add_argument("--plate-file", "--plate_file", dest="plate_file", type=Path, default=None)
    parser.add_argument("--speed-file", "--speed_file", dest="speed_file", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=Path("dqn_machine_model.pt"))
    parser.add_argument("--episodes_per_dataset", type=int, default=20)
    parser.add_argument("--train_epochs", type=int, default=80)
    parser.add_argument("--batch_size", type=int, default=64)
    parser.add_argument("--learning_rate", type=float, default=1e-3)
    parser.add_argument("--gamma", type=float, default=0.9)
    parser.add_argument("--epsilon", type=float, default=0.2)
    parser.add_argument("--random_seed", type=int, default=20260723)
    args = parser.parse_args()

    import numpy as np

    try:
        import torch
    except Exception as exc:  # pragma: no cover
        raise SystemExit("需要 PyTorch CPU：conda install pytorch-cpu -c pytorch 或 pip install torch") from exc

    from dqn_machine_model import (
        DqnMachineModel,
        _dataset_feature_stats,
        _eligible_machines,
    )
    from improved_optimizer import SATabuOptimizer
    from steel_schedule_model import (
        ModelConfig,
        load_and_validate,
        load_process_params,
        machine_names,
        plate_features,
    )

    torch.manual_seed(args.random_seed)
    rng = random.Random(args.random_seed)

    if args.plate_file is not None:
        datasets = [(args.plate_file.name, args.plate_file, args.speed_file)]
    else:
        direct_plate = args.data_dir / "附件2：钢板零件数据.xlsx"
        direct_speed = args.data_dir / "附件3：工艺用时计算表.xlsx"
        if direct_plate.exists():
            datasets = [("产线场景描述", direct_plate, direct_speed)]
        else:
            datasets = []
            for sub in sorted(args.data_dir.glob("data_*")):
                datasets.append(
                    (
                        sub.name,
                        sub / "附件2：钢板零件数据.xlsx",
                        sub / "附件3：工艺用时计算表.xlsx",
                    )
                )
    datasets = [
        (name, p, s)
        for name, p, s in datasets
        if p is not None and Path(p).exists()
    ]
    if not datasets:
        raise SystemExit(
            f"未找到可用附件2：{args.data_dir}。可改用 --plate-file/--speed-file 指定两个 xlsx。"
        )

    # Use a representative feature width; 16 is enough for the compact state.
    input_dim = 16
    model = DqnMachineModel(input_dim=input_dim, action_dim=5, device="cpu")
    optimizer = torch.optim.Adam(model.net.parameters(), lr=args.learning_rate)
    loss_fn = torch.nn.MSELoss()
    replay: list[tuple] = []
    feature_stats: dict[str, list[float]] = {
        "n_plates": [],
        "thickness_min": [],
        "thickness_max": [],
        "width_min": [],
        "width_max": [],
        "cut_min": [],
        "cut_max": [],
    }

    for ds_idx, (ds_name, plate_file, speed_file) in enumerate(datasets):
        plate_file = Path(plate_file)
        speed_file = Path(speed_file) if speed_file is not None else None
        cfg = ModelConfig()
        cfg.local_search_iterations = 20
        cfg.random_seed = args.random_seed + ds_idx
        try:
            plates, parts, checks = load_and_validate(plate_file)
            pp = load_process_params(speed_file) if speed_file is not None and speed_file.exists() else None
            features = plate_features(plates, parts, cfg, pp.speed_table if pp else None)
        except Exception as exc:
            print(f"[{ds_idx+1}/{len(datasets)}] SKIP {ds_name}: {exc}")
            continue

        print(f"[{ds_idx+1}/{len(datasets)}] training source: {ds_name}")
        stats = _dataset_feature_stats(features)
        for key in feature_stats:
            feature_stats[key].append(float(stats[key]))

        name_col = next(c for c in features.columns if "套料图名" in c or "plate" in c.lower())
        seq_col = next(c for c in features.columns if "序号" in c or "seq" in c.lower())
        seg_col = next(c for c in features.columns if "分段号" in c)
        cut_col = next(c for c in features.columns if "工时" in c and "min" in c)
        machines = machine_names(cfg)
        base_orders = {
            "FIFO": features.sort_values(seq_col, kind="stable")[name_col].tolist(),
            "SegPriShort": features.sort_values(
                [seg_col, next(c for c in features.columns if "优先" in c or "最低" in c), cut_col],
                ascending=[True, True, True], kind="stable",
            )[name_col].tolist(),
        }
        orders = list(base_orders.values())
        for _ in range(args.episodes_per_dataset):
            shuffled = list(base_orders["FIFO"])
            rng.shuffle(shuffled)
            orders.append(shuffled)

        builder = SATabuOptimizer(features, parts, cfg, pp, seed=cfg.random_seed)
        _, fifo_met, _ = builder.schedule_from_order(base_orders["FIFO"])
        fifo_kit = fifo_met["加权平均齐套跨度(h)"]
        fifo_load = fifo_met["切割负载差(h)"]
        fifo_wait = fifo_met.get("总等待时间(h)", 0.0)

        for order in orders[: args.episodes_per_dataset]:
            loads = {m: 0.0 for m in machines}
            episode: list[tuple] = []
            machine_map: dict[str, str] = {}
            for plate in order:
                elig = _eligible_machines(features, plate, machines)
                state = model._state_vector(features, plate, loads)
                state_t = torch.as_tensor(state, dtype=torch.float32)
                if rng.random() < args.epsilon:
                    action = rng.randrange(len(elig))
                else:
                    with torch.no_grad():
                        q = model.net(state_t.unsqueeze(0)).squeeze(0)
                    action = int(torch.argmax(q[: len(elig)]).item())
                chosen = elig[action]
                machine_map[plate] = chosen
                cut_time = float(features.loc[plate, cut_col]) if plate in features.index else float(features.loc[features[name_col] == plate, cut_col].iloc[0])
                old_load = loads.get(chosen, 0.0)
                loads[chosen] = old_load + cut_time
                next_state = model._state_vector(features, plate, loads)
                reward = -float(cut_time) / 120.0 - max(0.0, loads[chosen] - old_load - 1.0)
                episode.append((state, action, next_state, reward, False))

            try:
                _sched, met, _stg = builder.schedule_from_order(order, machine_map)
                cmax = float(met["总完工时间(h)"])
                lb = float(met.get("理论下界(h)", cmax) or cmax)
                cap_reward = -1.5 * (cmax / max(lb, 1e-9))
                kit_reward = -0.4 * float(met["加权平均齐套跨度(h)"]) / max(fifo_kit, 1e-9)
                load_reward = -0.2 * float(met["切割负载差(h)"]) / max(fifo_load, 1e-9)
                wait_reward = -0.2 * float(met.get("总等待时间(h)", 0.0)) / max(fifo_wait, 1e-9)
                if episode:
                    st, ac, _ns, rew, _done = episode[-1]
                    episode[-1] = (st, ac, _ns, rew + cap_reward + kit_reward + load_reward + wait_reward, True)
            except Exception as exc:
                print(f"episode sim failed: {exc}")
            replay.extend(episode)

    if not replay:
        raise SystemExit("没有生成训练样本")

    replay = replay[: max(2000, len(replay))]
    print(f"DQN training: {len(replay)} transitions, epochs={args.train_epochs}")
    model.net.train()
    for epoch in range(args.train_epochs):
        rng.shuffle(replay)
        total_loss = 0.0
        n_batches = 0
        for start in range(0, len(replay), args.batch_size):
            batch = replay[start : start + args.batch_size]
            states = torch.as_tensor(np.stack([b[0] for b in batch]), dtype=torch.float32)
            next_states = torch.as_tensor(np.stack([b[2] for b in batch]), dtype=torch.float32)
            rewards = torch.as_tensor([b[3] for b in batch], dtype=torch.float32)
            dones = torch.as_tensor([b[4] for b in batch], dtype=torch.float32)
            actions = torch.as_tensor([b[1] for b in batch], dtype=torch.long)
            q_values = model.net(states).gather(1, actions.unsqueeze(1)).squeeze(1)
            with torch.no_grad():
                next_max = model.net(next_states).max(dim=1).values
            target = rewards + args.gamma * next_max * (1.0 - dones)
            loss = loss_fn(q_values, target)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            total_loss += float(loss.item())
            n_batches += 1
        if n_batches:
            avg_loss = total_loss / n_batches
            if epoch % max(1, args.train_epochs // 10) == 0:
                print(f"epoch {epoch+1}/{args.train_epochs} loss={avg_loss:.4f}")
    model.net.eval()
    model.set_meta(
        {
            "n_plates_min": min(feature_stats["n_plates"]),
            "n_plates_max": max(feature_stats["n_plates"]),
            "thickness_min": min(feature_stats["thickness_min"]),
            "thickness_max": max(feature_stats["thickness_max"]),
            "width_min": min(feature_stats["width_min"]),
            "width_max": max(feature_stats["width_max"]),
            "cut_min": min(feature_stats["cut_min"]),
            "cut_max": max(feature_stats["cut_max"]),
        }
    )
    model.save(args.output)
    print(f"DQN model saved: {args.output}")


if __name__ == "__main__":
    main()
