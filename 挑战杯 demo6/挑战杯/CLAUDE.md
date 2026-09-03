# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Challenge Cup (挑战杯) competition entry: **Kit-Aware Steel Plate Scheduling Model** (钢板齐套感知排产模型). Solves shipbuilding scheduling: given N steel plates with M parts across multiple ship segments, schedule cutting on N2/N5 machines to minimize makespan (Cmax), kit-span (time between first and last part of each segment+priority group), and machine load imbalance.

Full-stack: **FastAPI** backend + **React/TypeScript/Ant Design** frontend + **Python** optimization engine.

## Commands

```bash
# One-click start (double-click or terminal)
start.bat                        # Windows: installs deps, builds frontend, starts server at :8000

# Backend only
cd backend
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000 --reload

# Frontend dev (with hot reload, proxies /api to :8000)
cd frontend
npm install
npm run dev                      # Vite dev server

# Frontend build
npm run build                    # tsc -b && vite build → dist/

# Lint
npm run lint                     # oxlint

# CLI: run model without web UI
python steel_schedule_model.py --input "附件2：钢板零件数据.xlsx" --speed-table "附件3：工艺用时计算表.xlsx" --output outputs

# CLI: generate Word report
python build_report.py --results outputs --output 建模报告.docx
```

## Architecture

```
Browser (SPA)  ←→  FastAPI (:8000)  ←→  Optimization Engine
                      ├─ /api/upload     Excel validation
                      ├─ /api/run        Full pipeline
                      ├─ /api/reschedule Dynamic re-optimization
                      ├─ /api/history    Run history CRUD
                      └─ /*              Static files (frontend/dist/)
```

**Data flow**: Excel upload → `load_and_validate()` → `plate_features()` → `run_multi_strategy_inline()` (SA+Tabu) → `simulate()` (discrete-event) → CSVs/PNGs/JSON → frontend visualization.

**Key files**:
- `steel_schedule_model.py` (1200+ lines) — ModelConfig dataclass, data validation, feature engineering, discrete-event simulation, objective function, PNG plotting
- `improved_optimizer.py` (900+ lines) — SA+Tabu hybrid optimizer with 8 neighborhood operators, Pareto archive, 3-tier selection, multi-start wrapper
- `drl_optimizer.py` (700+ lines) — Q-Learning agent (243 states x 27 actions) for adaptive operator/weight selection during SA search
- `backend/main.py` (1200+ lines) — FastAPI app with all endpoints, file locking, progress tracking
- `pretrain_drl.py` — Batch Q-learning pretraining on 9 datasets, saves to `drl_qtable.npy`

## ModelConfig (central parameter hub)

All 40+ parameters live in `ModelConfig` dataclass at `steel_schedule_model.py:20-68`. Everything else reads from it via attribute access or `getattr(cfg, key, fallback)`.

Key defaults:
- **Objective weights**: `obj_weight_cmax=0.40`, `obj_weight_kit=0.40`, `obj_weight_load=0.20`
- **Competition targets**: `CMAX_TARGET_H=67.25`, `KITSPAN_TARGET_H=12.7`, `LOADDIFF_TARGET_H=1.0`
- **Buffer**: `finish_buffer_capacity=28` (N2=10 + N5=18, per `PER_MACHINE_CAPS`), `bevel_buffer_capacity=3`, `kit_buffer_capacity=30`
- **Machines**: `cutting_machines=2` (N2, N5; can add N8=3), `agvs=2`
- **Algorithm**: `local_search_iterations=800`, `random_seed=20260723`
- `adapt_to_data()` dynamically scales downstream resources (auto-bevel max 4, manual-bevel max 6, grinders max 8) based on dataset characteristics

## Optimization Pipeline

**6 stages**:

1. **Data validation** (`load_and_validate`): Validates Excel sheets (钢板数据 + 零件数据), checks referential integrity, normalizes part types, computes bevel lengths (I坡 excluded).

2. **Feature engineering** (`plate_features`): Per-plate cut duration (via official formula or component method), kit coverage count, kit weight (Σ 1/group_part_count), minimum priority.

3. **Multi-start SA+Tabu** (`run_multi_strategy_inline`):
   - 5-6 initial solutions: FIFO, Completeness (greedy), SegPriShort, SegPriLong, SPT, ThinFirst
   - Each start gets `max(80, iterations/N_starts)` iterations or `max(30, 120/N_starts)` seconds
   - SA: T_start=15.0, cooling=0.992, reheat=0.45, patience=max(60, n_plates)
   - Tabu: MD5 hash of full sequence, tenure=n/10
   - 8 operators: swap, insert, block_reverse, segment_shuffle, cross_machine_rebalance, stagger_crane, kit_cluster, seg_priority_sort
   - Adaptive bias: early search explores, late search exploits (kit_cluster + segment_shuffle)
   - Evaluation cache: 5000-entry MD5→(schedule, metrics, stages)

4. **Simulation** (`simulate`): Full discrete-event simulation with:
   - Truss sorting (2 arms, cross-machine travel), auto-grinding (with vision scan), auto/manual bevel
   - AGV path network (4 zones: N2/N5/processing/kit), deadlock detection
   - Three-level buffer: machine pallet (N2=10, N5=18) → bevel buffer (3) → kit buffer (30)
   - Predictive AGV dispatch at 70% buffer occupancy
   - 30+ KPI output including 3 OEE variants

5. **Selection** (3 tiers):
   - Tier 1 (ideal): Cmax ≤ FIFO AND KitSpan ≤ 12.7h → pick best KitSpan
   - Tier 2 (competition): Cmax ≤ 67.25h AND KitSpan ≤ 12.7h → pick best Cmax
   - Tier 3 (fallback): minimize combined normalized excess

6. **Output**: 5 CSVs + 3 PNGs + summary.json + buffer_timeseries.json per run

## DRL System

Activated when `local_search_iterations >= 400`. Q-table: 243 states × 27 actions (9 operators × 3 weight profiles). State = phase(3) × temp_zone(3) × streak(3) × kit_trend(3) × load_zone(3). Reward = tanh(Δobj×5) + 0.3×tanh(Δkit×10) + 0.1×acceptance_bonus. Pretrained Q-table at `drl_qtable.npy`.

## Objective Function Details

Two objective functions exist:

1. **`objective()`** (standalone, line 1198): `0.40*Cmax/67.25 + 0.40*KitSpan/12.7 + 0.20*LoadDiff/1.0`. Used for CLI display and DRL pretraining evaluation.

2. **`kit_span_objective()`** (SA+Tabu internal): Same base + satisficing (KitSpan improvements beyond 12.7h discounted 50%) + hard Cmax penalty (5.0 per unit excess over 67.25h) + soft FIFO degradation penalty (2.0 per unit excess over FIFO baseline) + buffer health penalty.

**Weights are defined in 5 places that must stay synchronized**: ModelConfig defaults, `objective()`, `kit_span_objective()` getattr fallbacks, DRL WEIGHT_PROFILES["balanced"], and 3 frontend files (ParamConfig defaults, HistorySidebar score calc, ResultOverview/ModelArchitecture display text).

## Frontend Structure

`App.tsx` manages state: `upload`, `params` (from ParamConfig DEFAULT_PARAMS), `result`, `activeRunId`. Tab-based layout: DataImport → ParamConfig → ResultOverview → GanttChart → KitAnalysis → UtilCharts → KitDashboard → BufferMonitor → ReschedulePanel → ModelArchitecture → LoadingPipeline. HistorySidebar in left drawer. All charts use ECharts via echarts-for-react.

API client (`api/index.ts`): axios with baseURL `/api`, infinite timeout. Progress polling at `fetchProgress`.

After editing frontend source, run `npm run build` and restart backend to serve updated `dist/`.

## Key Design Decisions

- **Cmax is near physical lower bound**: Total cutting time is fixed; greedy machine assignment already achieves near-optimal load balancing. Cmax can improve at most ~8%. KitSpan has 50%+ improvement headroom. This is a problem characteristic, not an algorithm deficiency.
- **Balanced weights (0.40/0.40/0.20) outperform Cmax-heavy or KitSpan-heavy**: A small LoadDiff weight increase (0.15→0.20) helps escape local optima that trap other configurations.
- **All weights hardcoded in display strings must be updated together**: 5 backend locations + 4 frontend locations.
- **Python cache**: After editing `.py` files, delete `__pycache__/` directories to force recompilation.
