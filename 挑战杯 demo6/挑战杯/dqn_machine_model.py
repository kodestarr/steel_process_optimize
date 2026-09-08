"""DQN machine-assignment model for DQN+NSGA-II.

The DQN consumes an operation sequence (plate order) and assigns a cutting
machine to each plate. This module keeps the model optional: when torch or a
weight file is unavailable, the optimizer falls back to heuristic machine maps
so the web application remains usable.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np


def _resolve_model_path(path: str | Path) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = Path(__file__).parent / p
    return p


def _plate_thickness(row) -> float:
    for col in ("厚度(mm)", "厚度", "板厚(mm)", "板厚"):
        if col in row:
            try:
                val = float(row[col])
                if math.isfinite(val):
                    return val
            except (TypeError, ValueError):
                continue
    return 6.0


def _plate_width(row) -> float | None:
    for col in ("钢板宽度(mm)", "宽度(mm)", "板宽(mm)"):
        if col in row:
            try:
                val = float(row[col])
                if math.isfinite(val):
                    return val
            except (TypeError, ValueError):
                continue
    return None


def _eligible_machines(features, plate_name: str, machines: list[str]) -> list[str]:
    """Return machines that can legally cut a plate (N5 has thickness/width caps)."""
    row = _row_for(features, plate_name)
    thickness = _plate_thickness(row)
    width = _plate_width(row)
    eligible = []
    for m in machines:
        if m == "N5":
            if thickness > 36.0:
                continue
            if width is not None and width > 4500.0:
                continue
        eligible.append(m)
    return eligible or (["N2"] if "N2" in machines else machines[:1])


def _row_for(features, plate_name: str):
    if plate_name in getattr(features, "index", []):
        return features.loc[plate_name]
    for col in ("套料图名", "钢板名称", "plate_name", "名称"):
        if col in features.columns:
            matches = features.loc[features[col].astype(str) == str(plate_name)]
            if len(matches):
                return matches.iloc[0]
    return features.iloc[0]


def _dataset_feature_stats(features) -> dict:
    """Physical feature ranges used to decide whether a trained model may be used."""
    cut_cols = [c for c in features.columns if "工时" in c and "min" in c]
    cut_col = cut_cols[0] if cut_cols else None
    thick_col = next(
        (c for c in ("厚度(mm)", "厚度", "板厚(mm)", "板厚") if c in features.columns),
        None,
    )
    width_col = next(
        (c for c in ("钢板宽度(mm)", "宽度(mm)", "板宽(mm)") if c in features.columns),
        None,
    )
    cuts = [float(v) for v in features[cut_col].tolist() if cut_col is not None]
    thickness = (
        [float(v) for v in features[thick_col].tolist() if thick_col is not None]
        if thick_col is not None
        else []
    )
    width = (
        [float(v) for v in features[width_col].tolist() if width_col is not None]
        if width_col is not None
        else []
    )
    return {
        "n_plates": len(features),
        "thickness_min": min(thickness) if thickness else 0.0,
        "thickness_max": max(thickness) if thickness else 0.0,
        "width_min": min(width) if width else 0.0,
        "width_max": max(width) if width else 0.0,
        "cut_min": min(cuts) if cuts else 0.0,
        "cut_max": max(cuts) if cuts else 0.0,
    }


def heuristic_machine_maps(
    features,
    orders: list[list[str]],
    machines: list[str],
    variants: int = 3,
    seed: int = 20260723,
) -> list[dict[str, str]]:
    """Generate diversified feasible machine maps without a trained DQN model."""
    import random

    rng = random.Random(seed)
    name_col = next(c for c in features.columns if "套料图名" in c or "plate" in c.lower())
    cut_col = next(c for c in features.columns if "工时" in c and "min" in c)
    maps: list[dict[str, str]] = []

    def _greedy(loads_bias: dict[str, float] | None = None) -> dict[str, str]:
        loads = {m: 0.0 for m in machines}
        loads.update(loads_bias or {})
        result: dict[str, str] = {}
        for order in orders:
            for plate in order:
                elig = _eligible_machines(features, plate, machines)
                m = min(elig, key=lambda x: loads.get(x, 0.0))
                result[plate] = m
                loads[m] += float(_row_for(features, plate)[cut_col])
        return result

    maps.append(_greedy())
    if len(machines) >= 2:
        n2_heavy = {m: 0.0 for m in machines}
        n2_heavy["N2"] = -1e-6
        maps.append(_greedy(n2_heavy))
    if len(machines) >= 2:
        n5_heavy = {m: 0.0 for m in machines}
        n5_heavy["N5"] = -1e-6
        maps.append(_greedy(n5_heavy))

    while len(maps) < variants:
        base = rng.choice(maps)
        variant = dict(base)
        plate = rng.choice(sum((list(o) for o in orders), []))
        elig = _eligible_machines(features, plate, machines)
        others = [m for m in elig if m != variant.get(plate)]
        if others:
            variant[plate] = rng.choice(others)
        maps.append(variant)
    return maps[: max(1, variants)]


def _try_import_torch():
    try:
        import torch

        return torch
    except Exception:
        return None


class DqnMachineModel:
    """Small CPU DQN used only as a seed generator for NSGA-II."""

    def __init__(
        self,
        input_dim: int = 16,
        action_dim: int = 3,
        device: str = "cpu",
    ):
        self.torch = _try_import_torch()
        self.input_dim = input_dim
        self.action_dim = action_dim
        self.device = device
        self.net = None
        self.meta: dict | None = None
        if self.torch is not None:
            import torch
            from torch import nn

            class _QNet(nn.Module):
                def __init__(self, in_dim: int, out_dim: int):
                    super().__init__()
                    self.net = nn.Sequential(
                        nn.Linear(in_dim, 128),
                        nn.ReLU(),
                        nn.Linear(128, 64),
                        nn.ReLU(),
                        nn.Linear(64, out_dim),
                    )

                def forward(self, x):
                    return self.net(x)

            self.net = _QNet(input_dim, action_dim)

    def load(self, path: str | Path) -> bool:
        resolved = _resolve_model_path(path)
        if not resolved.exists() or self.torch is None or self.net is None:
            return False
        try:
            import torch

            self.net.load_state_dict(torch.load(resolved, map_location="cpu"))
            self.net.eval()
            meta_path = Path(str(resolved) + ".meta.json")
            if meta_path.exists():
                try:
                    self.meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    self.meta = None
            return True
        except Exception:
            return False

    def save(self, path: str | Path) -> None:
        if self.net is None:
            return
        import torch

        torch.save(self.net.state_dict(), _resolve_model_path(path))
        if self.meta:
            meta_path = Path(str(_resolve_model_path(path)) + ".meta.json")
            meta_path.write_text(
                json.dumps(self.meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def set_meta(self, meta: dict) -> None:
        self.meta = dict(meta)

    def supports(self, features) -> bool:
        """Only use the model when current physical features are inside a sane envelope."""
        meta = self.meta
        if not meta:
            return False
        stats = _dataset_feature_stats(features)
        checks = {
            "n_plates": (stats["n_plates"], meta.get("n_plates_min", 0), meta.get("n_plates_max", 1)),
            "thickness_min": (stats["thickness_min"], meta.get("thickness_min", 0), meta.get("thickness_max", 100)),
            "thickness_max": (stats["thickness_max"], meta.get("thickness_min", 0), meta.get("thickness_max", 100)),
            "width_min": (stats["width_min"], meta.get("width_min", 0), meta.get("width_max", 10000)),
            "width_max": (stats["width_max"], meta.get("width_min", 0), meta.get("width_max", 10000)),
            "cut_min": (stats["cut_min"], meta.get("cut_min", 0), meta.get("cut_max", 10000)),
            "cut_max": (stats["cut_max"], meta.get("cut_min", 0), meta.get("cut_max", 10000)),
        }
        for key, (current, lo, hi) in checks.items():
            if hi <= 0:
                if key.startswith("width"):
                    if current > 1e-9:
                        return False
                    continue
                return False
            if current < lo * 0.6 - 1e-9:
                return False
            if current > hi * 1.5 + 1e-9:
                return False
        return True

    def _state_vector(
        self,
        features,
        plate_name: str,
        machine_loads: dict[str, float],
    ) -> np.ndarray:
        """Compact state: current plate + normalized machine loads + remaining aggregate."""
        row = _row_for(features, plate_name)
        thickness = _plate_thickness(row)
        width = _plate_width(row) or 0.0
        cut = 0.0
        pri = 0.0
        seg = 0.0
        for col in features.columns:
            low = col.lower()
            if "工时" in col and "min" in col:
                cut = float(row[col])
            elif "优先" in col or "最低" in col:
                pri = float(row[col])
            elif "分段号" in col or "segment" in low:
                # Do not encode exact segment labels: they do not transfer to
                # different plate datasets and Python string hashes are unstable.
                seg = 0.0
        loads = sorted(machine_loads.values())
        load_vec = np.zeros(5, dtype=float)
        for i, val in enumerate(loads[:5]):
            load_vec[i] = val / max(1.0, np.mean(loads) + 1e-9)
        total_remaining = float(sum(max(0.0, x) for x in machine_loads.values()))
        raw = np.asarray(
            [
                min(2.0, thickness / 40.0),
                min(2.0, width / 5000.0),
                min(3.0, cut / 200.0),
                min(3.0, pri / 5.0),
                seg / 1000.0,
                min(5.0, total_remaining / 1000.0),
                *load_vec,
            ],
            dtype=float,
        )
        padded = np.zeros(max(1, self.input_dim), dtype=float)
        padded[: len(raw)] = raw[: self.input_dim]
        return padded

    def infer_machine(
        self,
        features,
        plate_name: str,
        machine_loads: dict[str, float],
        eligible: list[str],
    ) -> str | None:
        if self.net is None or not eligible:
            return None
        import torch

        state = torch.as_tensor(self._state_vector(features, plate_name, machine_loads), dtype=torch.float32)
        with torch.no_grad():
            q = self.net(state.unsqueeze(0)).squeeze(0).cpu().numpy()
        # Q values are emitted in N2,N5,N8,... order; map back to machine ids.
        action_id = int(np.argmax(q[: len(eligible)]))
        return eligible[action_id]


def dqn_seed_machine_maps(
    features,
    orders: list[list[str]],
    cfg,
    machines: list[str],
    seed_count: int,
    population_size: int | None = None,
) -> list[dict[str, str]]:
    """Return `population_size` machine maps with heuristic majority and few DQN maps."""
    dqn_request = max(0, int(seed_count or 0))
    pop_size = max(4, int(population_size if population_size is not None else max(dqn_request, 8)))
    # Fixed action width decouples the weight file from the current machine count
    # and stays compatible with the offline trainer (N2/N5/N8/N9/N10).
    model = DqnMachineModel(action_dim=5)
    model_path = getattr(cfg, "dqn_model_path", "dqn_machine_model.pt")
    loaded = model.load(model_path)

    dqn_budget = 0
    if loaded and model.supports(features):
        dqn_budget = max(0, min(dqn_request, max(1, (pop_size + 4) // 5)))
    elif loaded:
        print(
            "[DQN+NSGA-II] DQN model skipped: current feature range does not match "
            "training metadata; using heuristic machine seeds."
        )
    heuristic_variants = max(1, pop_size - dqn_budget)
    maps: list[dict[str, str]] = heuristic_machine_maps(
        features,
        orders,
        machines,
        variants=heuristic_variants,
        seed=int(getattr(cfg, "random_seed", 20260723)),
    )
    if dqn_budget > 0:
        for dq_idx in range(dqn_budget):
            order = orders[dq_idx % len(orders)] if orders else []
            loads = {m: 0.0 for m in machines}
            plate_map: dict[str, str] = {}
            cut_col = next(c for c in features.columns if "工时" in c and "min" in c)
            for plate in order:
                elig = _eligible_machines(features, plate, machines)
                m = model.infer_machine(features, plate, loads, elig)
                if m is None or m not in elig:
                    m = min(elig, key=lambda x: loads.get(x, 0.0))
                plate_map[plate] = m
                loads[m] += float(_row_for(features, plate)[cut_col])
            maps.append(plate_map)
    return maps[:pop_size]
