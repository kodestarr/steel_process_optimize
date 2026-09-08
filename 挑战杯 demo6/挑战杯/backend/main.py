"""钢板齐套感知排产模型 - FastAPI 后端"""
from __future__ import annotations

import json
import copy
import math
import os
import subprocess
import sys
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

# 允许从项目根目录导入现有模型
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── P0-4/P0-5 FIX: NaN/Inf sanitizer for JSON-safe serialization ──
def _sanitize_json(obj):
    """Recursively replace NaN/Inf with None (or 0.0 for numeric contexts) so json.dumps never crashes."""
    if isinstance(obj, dict):
        return {k: _sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_json(v) for v in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None  # None → JSON null, safe for all consumers
    return obj


def _resolve_stage_plate(part: str, part_plate_map: dict) -> str:
    """工序条目的钢板名：普通零件查映射，整板大件标签直接还原。"""
    if part in part_plate_map:
        return str(part_plate_map[part])
    suffix = "切割后的大件"
    if part.endswith(suffix):
        return part[: -len(suffix)]
    return ""


# ── P0-2/P0-3 FIX: Path traversal guard ──
def _safe_result_path(run_id: str, filename: str | None = None) -> Path:
    """Resolve a path inside RESULT_DIR, rejecting traversal attempts."""
    # Purify run_id — strip any path separators and parent-dir segments
    safe_id = os.path.basename(run_id) or "_"
    if safe_id != run_id:
        raise HTTPException(403, "非法运行ID")
    if safe_id in (".", ".."):
        raise HTTPException(403, "非法运行ID")

    if filename is not None:
        rel = Path(filename.replace("\\", "/"))
        if rel.is_absolute() or ".." in rel.parts or len(rel.parts) > 2:
            raise HTTPException(403, "非法文件名")
        resolved = (RESULT_DIR / safe_id / rel).resolve()
    else:
        resolved = (RESULT_DIR / safe_id).resolve()

    # Ensure resolved path is inside RESULT_DIR
    try:
        resolved.relative_to(RESULT_DIR.resolve())
    except ValueError:
        raise HTTPException(403, "路径越权")

    return resolved

from steel_schedule_model import (  # noqa: E402
    ModelConfig,
    ProcessParams,
    load_and_validate,
    load_process_params,
    make_greedy_schedule,
    machine_names,
    pick_machine,
    CranePool,
    _reserve_raw_material_crane,
    monte_carlo_robustness,
    plate_features,
    plot_outputs,
    simulate,
    build_joint_schedule,
    objective,
)

from improved_optimizer import run_multi_strategy_inline  # noqa: E402
from drl_optimizer import run_drl_enhanced_optimization  # noqa: E402
from pareto_optimizer import run_quadratic_optimization  # noqa: E402
from ga_lns_optimizer import run_ga_lns_optimization  # noqa: E402
from dqn_nsga2_optimizer import run_dq_nsga2_pareto_front  # noqa: E402

# ── 目录 ──────────────────────────────────────────────
UPLOAD_DIR = Path(__file__).parent / "uploads"
RESULT_DIR = Path(__file__).parent / "results"
HISTORY_FILE = Path(__file__).parent / "history.json"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
RESULT_DIR.mkdir(parents=True, exist_ok=True)

# ── 优化进度共享状态（线程安全）──
import threading
_optimization_progress: dict[str, dict] = {}
_progress_lock = threading.Lock()

# ── 跨平台文件锁（P0-1: 防止多 worker 下 history.json 并发写损坏）──
import os as _os


class _FileLock:
    """跨平台文件互斥锁，兼容 Windows (msvcrt) 和 Unix (fcntl)。

    用法:
        with _FileLock("/path/to/file.lock"):
            # 安全读写
    """

    def __init__(self, lock_path: str, timeout: float = 5.0):
        self._lock_path = lock_path
        self._timeout = timeout
        self._fd = None

    def acquire(self) -> bool:
        import time
        _os.makedirs(_os.path.dirname(self._lock_path) or '.', exist_ok=True)
        deadline = time.time() + self._timeout
        while True:
            try:
                if _os.name == 'nt':
                    # Windows: 使用 msvcrt 文件锁
                    import msvcrt
                    self._fd = _os.open(self._lock_path, _os.O_CREAT | _os.O_RDWR)
                    msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
                else:
                    # Unix: 使用 fcntl 文件锁
                    import fcntl
                    self._fd = _os.open(self._lock_path, _os.O_CREAT | _os.O_RDWR)
                    fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except (IOError, OSError):
                if self._fd is not None:
                    _os.close(self._fd)
                    self._fd = None
                if time.time() > deadline:
                    return False
                time.sleep(0.05)

    def release(self):
        if self._fd is not None:
            try:
                if _os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                _os.close(self._fd)
            except Exception:
                pass
            self._fd = None

    def __enter__(self):
        if not self.acquire():
            raise TimeoutError(f"无法在 {self._timeout}s 内获取文件锁: {self._lock_path}")
        return self

    def __exit__(self, *args):
        self.release()
        return False

# ── FastAPI 应用 ─────────────────────────────────────
app = FastAPI(title="钢板齐套感知排产模型", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _no_cache_api(request, call_next):
    """所有 /api 响应禁用缓存，避免浏览器把不同 run_id 的详情串在一起。"""
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    return response


# ═══════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════

_HISTORY_LOCK_PATH = str(HISTORY_FILE) + ".lock"


def _load_history() -> dict:
    with _FileLock(_HISTORY_LOCK_PATH):
        if not HISTORY_FILE.exists():
            return {"runs": []}
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))


def _save_history(history: dict) -> None:
    with _FileLock(_HISTORY_LOCK_PATH):
        # P0-4 FIX: sanitize NaN/Inf before json.dumps to prevent ValueError crash
        safe = _sanitize_json(history)
        HISTORY_FILE.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")


def _atomic_history_update(update_fn) -> dict:
    """P1-1 修复：在单一文件锁内完成「读取→修改→写入」全流程，消除 TOCTOU 竞态。

    update_fn 接收 history dict，返回修改后的 history dict（可原地修改）。
    返回最终的 history dict。
    """
    with _FileLock(_HISTORY_LOCK_PATH):
        if HISTORY_FILE.exists():
            history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        else:
            history = {"runs": []}
        history = update_fn(history)
        # P0-4 FIX: sanitize NaN/Inf before json.dumps to prevent ValueError crash
        safe = _sanitize_json(history)
        HISTORY_FILE.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
        return history


def _history_score(opt_metrics: dict, base_metrics: dict) -> float:
    """按固定口径计算历史综合评分，运行完成时固化，不再随后续记录变化。"""
    cmax = float(opt_metrics.get("总完工时间(h)", 0) or 0)
    kit = float(opt_metrics.get("加权平均齐套跨度(h)", 0) or 0)
    load = float(opt_metrics.get("切割负载差(h)", 0) or 0)
    b_cmax = base_metrics.get("总完工时间(h)")
    b_kit = base_metrics.get("加权平均齐套跨度(h)")
    b_load = base_metrics.get("切割负载差(h)")
    if b_cmax and b_kit and b_load is not None:
        raw = (
            0.4 * cmax / max(float(b_cmax), 1e-9)
            + 0.4 * kit / max(float(b_kit), 1e-9)
            + 0.2 * load / max(float(b_load), 0.5)
        )
    else:
        raw = 0.4 * cmax / 100 + 0.4 * kit / 60 + 0.2 * load / 2
    return round(1.0 / (1.0 + max(raw, 0.0)), 4)


def _objective_display_name(cfg) -> str:
    """返回当前评价函数展示名称。"""
    objective_type = getattr(cfg, "objective_type", "linear")
    if objective_type == "quadratic":
        return "二次非线性评价"
    if objective_type == "auto":
        return "多目标Pareto自动匹配"
    return "线性评价"


def _optimizer_display_name(cfg) -> str:
    """返回当前使用的进化算法 + 评价函数展示名称。"""
    method = getattr(cfg, "optimizer_method", "sa_tabu")
    if method == "dq_nsga2":
        return "DQN+NSGA-II（多目标自动匹配）"
    if method == "quadratic":  # 旧版兼容：quadratic 表示 SA+Tabu + 二次评价
        return "SA+Tabu + 二次非线性评价"
    if method == "ga_lns":
        return f"GA+LNS + {_objective_display_name(cfg)}"
    return f"SA+Tabu + {_objective_display_name(cfg)}"


def _run_model(
    plates: pd.DataFrame,
    parts: pd.DataFrame,
    cfg: ModelConfig,
    out_dir: Path,
    checks: dict,
    speed_table_path: str | None = None,
    eval_track: str = "capacity",
    progress_tag: str | None = None,
    manage_progress: bool = True,
    pareto_front_store: dict | None = None,
):
    """运行完整建模管线，将结果写入 out_dir 并返回结构化数据。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = copy.deepcopy(cfg)
    cfg.eval_track = eval_track
    # 加载附件3工艺参数（若提供）
    pp = None
    speed_table = None
    if speed_table_path:
        pp_path = Path(speed_table_path)
        if pp_path.exists():
            pp = load_process_params(pp_path)
            speed_table = pp.speed_table
    # ── 自适应资源配置：根据数据集特征动态扩展瓶颈资源 ──
    cfg = cfg.adapt_to_data(plates, parts, pp)
    features = plate_features(plates, parts, cfg, speed_table)

    # ── 进度回调 ──
    run_tag = progress_tag or out_dir.name  # run_id
    def _progress_cb(iteration: int, max_iter: int, current_obj: float, best_obj: float, temp: float):
        with _progress_lock:
            _optimization_progress[run_tag] = {
                "iteration": iteration, "max_iterations": max_iter,
                "current_obj": round(current_obj, 4), "best_obj": round(best_obj, 4),
                "temperature": round(temp, 4),
                "progress_pct": round(100.0 * iteration / max(max_iter, 1), 1),
            }

    # ── 多策略优化（高迭代数时启用DRL增强）──
    method = getattr(cfg, "optimizer_method", "sa_tabu")
    objective_type = getattr(cfg, "objective_type", "linear")
    if method == "quadratic":  # 旧版兼容
        method = "sa_tabu"
        objective_type = "quadratic"
    use_drl = (
        cfg.local_search_iterations >= 400
        and method == "sa_tabu"
        and objective_type == "linear"
        and eval_track == "capacity"
    )
    if method == "dq_nsga2":
        if pareto_front_store is not None and pareto_front_store.get("capacity") is not None:
            front_result = pareto_front_store
        else:
            front_result = run_dq_nsga2_pareto_front(
                plates, parts, features, cfg, pp, speed_table, checks,
                iterations=cfg.local_search_iterations,
                progress_callback=_progress_cb,
            )
            if pareto_front_store is not None:
                pareto_front_store.clear()
                pareto_front_store.update(front_result)
        track_result = front_result[eval_track]
        opt_result = {
            "schedule": track_result["schedule"],
            "base_schedule": front_result["base_schedule"],
            "base_metrics": front_result["base_metrics"],
            "opt_metrics": track_result["metrics"],
            "stages": track_result["stages"],
            "strategy_name": "DQN+NSGA-II",
        }
    elif method == "ga_lns":
        opt_result = run_ga_lns_optimization(
            plates, parts, features, cfg, pp, speed_table, checks,
            iterations=cfg.local_search_iterations,
            progress_callback=_progress_cb,
            objective_type=objective_type,
        )
    elif objective_type == "quadratic":
        opt_result = run_quadratic_optimization(
            plates, parts, features, cfg, pp, speed_table, checks,
            iterations=cfg.local_search_iterations,
            progress_callback=_progress_cb,
        )
    elif use_drl:
        opt_result = run_drl_enhanced_optimization(
            plates, parts, features, cfg, pp, speed_table, checks,
            iterations=cfg.local_search_iterations, training=True,
        )
    else:
        opt_result = run_multi_strategy_inline(
            plates, parts, features, cfg, pp, speed_table, checks,
            iterations=cfg.local_search_iterations,
            progress_callback=_progress_cb,
        )
    # 清理进度状态
    if manage_progress:
        with _progress_lock:
            _optimization_progress.pop(run_tag, None)
    schedule = opt_result["schedule"]
    base_schedule = opt_result["base_schedule"]
    base_metrics = opt_result["base_metrics"]
    opt_metrics = opt_result["opt_metrics"]
    stages = opt_result["stages"]

    # P1-1/P1-3: 第二次 simulate() 返回完整 metrics（含 buffer/死锁/AGV/资源利用率），
    # 用于丰富 summary.json 和保证前后端数据一致性。
    # simulate 返回顺序: (complete_df, groups_df, metrics_dict, stages_df, buffer_timeseries)
    complete, groups, full_opt_metrics, _, buf_ts = simulate(schedule, parts, cfg, pp)
    _, base_groups, full_base_metrics, _, _ = simulate(base_schedule, parts, cfg, pp)

    # 合并：以 optimizer 返回的 KPI 为准，补充 simulate 的详细诊断指标
    base_metrics = {**full_base_metrics, **base_metrics}
    opt_metrics = {**full_opt_metrics, **opt_metrics}

    # ── P1-3: 统一引用 ModelConfig.PER_MACHINE_CAPS（消除重复定义）──
    all_machines = list(schedule["切割机"].unique())
    per_machine_caps = getattr(cfg, 'PER_MACHINE_CAPS', {"N2": 10, "N5": 18})
    _caps_map: dict[str, int] = {}
    _remaining_cap = max(0, cfg.finish_buffer_capacity - sum(
        per_machine_caps.get(m, 0) for m in all_machines if m in per_machine_caps))
    _unassigned = [m for m in all_machines if m not in per_machine_caps]
    for m in all_machines:
        if m in per_machine_caps:
            _caps_map[m] = per_machine_caps[m]
        else:
            _caps_map[m] = max(1, _remaining_cap // max(1, len(_unassigned)))
    buffer_config = {
        "machine_caps": _caps_map,
        "bevel_capacity": cfg.bevel_buffer_capacity,
        "half_capacity": cfg.half_buffer_capacity,
        "kit_capacity": cfg.kit_buffer_capacity,
    }

    # 对比
    comparison_rows = [
        {"方案": "FIFO基线", **base_metrics},
        {"方案": "齐套感知优化", **opt_metrics},
    ]
    comparison = pd.DataFrame(comparison_rows)
    # "整体产能" 是越高越好，改善率方向需要反过来 (opt - base) / base
    for col in ["总完工时间(h)", "加权平均齐套跨度(h)", "最大齐套跨度(h)", "切割负载差(h)", "整体产能(张板/班)"]:
        if col not in comparison.columns:
            continue
        base_val = comparison.loc[0, col]
        opt_val = comparison.loc[1, col]
        if col == "整体产能(张板/班)":
            comparison.loc[1, col + "改善率"] = (
                (opt_val - base_val) / base_val if base_val != 0 else 0.0
            )
        else:
            comparison.loc[1, col + "改善率"] = (
                (base_val - opt_val) / base_val if base_val != 0 else 0.0
            )

    # 写 CSV
    schedule.to_csv(out_dir / "optimized_plate_schedule.csv", index=False, encoding="utf-8-sig")
    complete.to_csv(out_dir / "part_completion.csv", index=False, encoding="utf-8-sig")
    groups.to_csv(out_dir / "kit_groups.csv", index=False, encoding="utf-8-sig")
    stages.to_csv(out_dir / "process_stages.csv", index=False, encoding="utf-8-sig")
    comparison.to_csv(out_dir / "comparison.csv", index=False, encoding="utf-8-sig")

    # 甘特图 / 齐套跨度图 / 利用率图
    plot_outputs(schedule, groups, stages, out_dir)

    # 摘要 JSON
    param_source = "附件3实测厚度相关速度表" if speed_table_path else "ModelConfig默认参数"
    summary = {
        "数据校验": checks,
        "工艺参数来源": param_source,
        "算法名称": _optimizer_display_name(cfg),
        "参数": {
            "cut_speed_mm_min": cfg.cut_speed_mm_min,
            "v_cut_speed_mm_min": cfg.v_cut_speed_mm_min,
            "rapid_speed_mm_min": cfg.rapid_speed_mm_min,
            "marking_speed_mm_min": cfg.marking_speed_mm_min,
            "pierce_minutes": cfg.pierce_minutes,
            "plate_setup_minutes": cfg.plate_setup_minutes,
            "cut_remainder_minutes": cfg.cut_remainder_minutes,
            "small_sort_minutes": cfg.small_sort_minutes,
            "small_sorters": cfg.small_sorters,
            "small_grind_speed_mm_min": cfg.small_grind_speed_mm_min,
            "large_grind_speed_mm_min": cfg.large_grind_speed_mm_min,
            "auto_bevel_speed_mm_min": cfg.auto_bevel_speed_mm_min,
            "manual_bevel_speed_mm_min": cfg.manual_bevel_speed_mm_min,
            "objective_type": cfg.objective_type,
            "small_transfer_minutes": cfg.small_transfer_minutes,
            "large_transfer_minutes": cfg.large_transfer_minutes,
            "small_grinders": cfg.small_grinders,
            "large_grinders": cfg.large_grinders,
            "auto_bevel_machines": cfg.auto_bevel_machines,
            "manual_bevel_stations": cfg.manual_bevel_stations,
            "agvs": cfg.agvs,
            "finish_buffer_capacity": cfg.finish_buffer_capacity,
            "bevel_buffer_capacity": cfg.bevel_buffer_capacity,
            "half_buffer_capacity": cfg.half_buffer_capacity,
            "kit_buffer_capacity": cfg.kit_buffer_capacity,
            "kit_dwell_minutes": cfg.kit_dwell_minutes,
            "local_search_iterations": cfg.local_search_iterations,
            "random_seed": cfg.random_seed,
            "optimizer_method": cfg.optimizer_method,
            "ga_population_size": cfg.ga_population_size,
            "ga_generations": cfg.ga_generations,
            "lns_destroy_ratio": cfg.lns_destroy_ratio,
            "ga_crossover_rate": cfg.ga_crossover_rate,
            "ga_mutation_rate": cfg.ga_mutation_rate,
            "ga_tournament_size": cfg.ga_tournament_size,
            "lns_iterations": cfg.lns_iterations,
            "dqn_seed_count": cfg.dqn_seed_count,
            "nsga2_archive_size": cfg.nsga2_archive_size,
            "dqn_model_path": cfg.dqn_model_path,
            "capacity_eps": cfg.capacity_eps,
            "capacity_tolerance_pct": cfg.capacity_tolerance_pct,
            "secondary_weight_kit": cfg.secondary_weight_kit,
            "secondary_weight_load": cfg.secondary_weight_load,
            "secondary_weight_wait": cfg.secondary_weight_wait,
        },
        "FIFO基线": base_metrics,
        "齐套感知优化": opt_metrics,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # 保存缓存时序数据供历史回看
    if buf_ts:
        (out_dir / "buffer_timeseries.json").write_text(
            json.dumps(_sanitize_json(buf_ts), ensure_ascii=False), encoding="utf-8"
        )

    # ── 构建前端图表数据 ──
    # 甘特图/利用率统一使用“全流程生产总时长”，避免下游工序超出横轴或被截断
    makespan = float(stages["结束(min)"].max()) if len(stages) > 0 else float(schedule["切割完成(min)"].max())
    gantt_data = []
    for _, r in schedule.iterrows():
        gantt_data.append({
            "name": str(r["套料图名"]),
            "machine": str(r["切割机"]),
            "start": round(float(r["切割开始(min)"]) / 60, 4),
            "end": round(float(r["切割完成(min)"]) / 60, 4),
            "duration": round(float(r["切割工时(min)"]) / 60, 4),
            "section": str(r.get("分段号", "")),
            "priority": int(r.get("最低优先级", 0)),
            "table": int(r.get("工位", 0)),
            "tableEnd": round(float(r.get("工位完工(min)", r["切割完成(min)"])) / 60, 4),
            "tableStart": round(float(r.get("工位开始(min)", r["切割开始(min)"])) / 60, 4),
            "waitEnd": round(float(r.get("等待结束(min)", r["切割开始(min)"])) / 60, 4),
        })

    kit_data = []
    for _, r in groups.iterrows():
        kit_data.append({
            "label": f"{r['分段号']}-P{int(r['齐套优先级'])}",
            "partCount": int(r["零件数"]),
            "firstArrival": round(float(r["首件到齐套区"]) / 60, 4),
            "completion": round(float(r["齐套完成"]) / 60, 4),
            "span": round(float(r["齐套跨度(min)"]) / 60, 4),
        })

    total_makespan_stages = float(stages["结束(min)"].max()) if len(stages) > 0 else 1.0
    # 利用率统一包含该资源全部工序时长（天车空驶/空行、AGV空驶、桁架跨区等）
    util_raw = stages.groupby("资源")["时长(min)"].sum().sort_values(ascending=False) if len(stages) > 0 else pd.Series(dtype=float)
    util_data = []
    for name, total_time in util_raw.items():
        util_pct = round(float(total_time / total_makespan_stages * 100), 2) if total_makespan_stages > 0 else 0.0
        util_data.append({
            "name": str(name),
            "totalHours": round(float(total_time) / 60, 2),
            "utilization": util_pct,
        })
    makespan_min = float(makespan)
    for m in sorted(schedule["切割机"].unique()):
        load_min = float(schedule[schedule["切割机"] == m]["切割工时(min)"].sum())
        util_data.append({
            "name": str(m),
            "totalHours": round(load_min / 60, 2),
            "utilization": round(load_min / makespan_min * 100, 2) if makespan_min > 0 else 0.0,
        })

    # 工序明细（用于全流程回放，包含切割）
    stages_data = []
    part_plate_map = {}
    try:
        part_completion = pd.read_csv(out_dir / "part_completion.csv", encoding="utf-8-sig")
        part_plate_map = dict(zip(
            part_completion["零件名"].astype(str),
            part_completion["套料图名"].astype(str),
        ))
    except Exception:
        pass
    for _, r in schedule.iterrows():
        stages_data.append({
            "part": str(r["套料图名"]),
            "stage": "切割",
            "resource": str(r["切割机"]),
            "start": round(float(r["切割开始(min)"]) / 60, 4),
            "end": round(float(r["切割完成(min)"]) / 60, 4),
        })
    for _, r in stages.iterrows():
        stages_data.append({
            "part": str(r.iloc[0]),
            "stage": str(r.iloc[1]),
            "resource": str(r.iloc[2]),
            "start": round(float(r.iloc[3]) / 60, 4),
            "end": round(float(r.iloc[4]) / 60, 4),
            "plate": _resolve_stage_plate(str(r.iloc[0]), part_plate_map),
        })

    return {
        "metrics": {
            # P0-5 FIX: sanitize NaN/Inf in metrics so JSON response is valid
            "fifo": _sanitize_json({k: round(v, 4) if isinstance(v, float) else v for k, v in base_metrics.items()}),
            "optimized": _sanitize_json({k: round(v, 4) if isinstance(v, float) else v for k, v in opt_metrics.items()}),
            "comparison": json.loads(comparison.to_json(orient="records", force_ascii=False)),
        },
        "makespanHours": round(float(makespan) / 60, 2),
        "ganttData": gantt_data,
        "kitSpanData": kit_data,
        "utilizationData": util_data,
        "stagesData": stages_data,
        "bufferTimeseries": buf_ts,
        "bufferConfig": buffer_config,
        "algorithmName": _optimizer_display_name(cfg),
        "checks": {**checks, "工艺参数来源": param_source},
    }


# ═══════════════════════════════════════════════════════
# API 端点
# ═══════════════════════════════════════════════════════

def _safe_filename(filename: str | None) -> str:
    """安全处理上传文件名：修复 Windows GBK/Latin-1 环境下中文文件名乱码。

    乱码根因：浏览器以 UTF-8 编码发送文件名，但 multipart 解析链路中
    Python 可能以 Latin-1/CP1252 错误解释字节流，导致中文变乱码。
    策略：尝试 Latin-1 再编码 → UTF-8 解码恢复原始中文。

    P0-2 增强：使用 unicodedata + 正则扩展区完整覆盖 CJK 字符，
    避免原 `'一' <= c <= '鿿'` 范围漏检 CJK Extension B+ 汉字。
    """
    import re
    import unicodedata as _ud

    # 匹配任何 CJK 统一表意文字（含扩展 A-G）、CJK 兼容汉字、假名/韩文
    _CJK_RE = re.compile(
        r'[一-鿿㐀-䶿豈-﫿'
        r'\U00020000-\U0002A6DF\U0002A700-\U0002EBEF'
        r'\U00030000-\U000323AF]'
    )

    def _has_cjk(s: str) -> bool:
        """检测字符串是否包含中日韩统一表意文字（含扩展区）。"""
        return bool(_CJK_RE.search(s))

    if not filename:
        return "unknown.xlsx"
    # 如果文件名已经是正常的（含中文字符），直接返回
    try:
        filename.encode("utf-8")  # 验证是否为有效UTF-8
        if _has_cjk(filename):
            return filename
    except UnicodeEncodeError:
        pass  # 当前字符串含有无法编码的字符，尝试恢复
    # 尝试 Latin-1 → UTF-8 恢复（最常见的乱码路径）
    # P1-3 修复：显式 errors="replace" 防止非 Latin-1 字符触发 UnicodeEncodeError 500
    try:
        recovered = filename.encode("latin-1", errors="replace").decode("utf-8", errors="replace")
        if _has_cjk(recovered):
            return recovered
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    # 尝试 Latin-1 → GBK 恢复（部分 Windows 环境）
    # P0-6 FIX: errors="replace" on both encode AND decode prevents UnicodeError 500 crash
    try:
        recovered = filename.encode("latin-1", errors="replace").decode("gbk", errors="replace")
        if _has_cjk(recovered):
            return recovered
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    # 尝试 raw bytes 直接 UTF-8 解码（处理裸 UTF-8 字节被错误当作字符串）
    try:
        raw_bytes = filename.encode("latin-1", errors="replace")
        recovered = raw_bytes.decode("utf-8", errors="replace")
        if _has_cjk(recovered):
            return recovered
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    # 若所有恢复路径失败，返回原始文件名
    return filename


@app.post("/api/upload")
async def upload_excel(file: UploadFile = File(...), speed_file: UploadFile = File(None)):
    """上传钢板零件 Excel（附件2）和可选的工艺参数表（附件3）。"""
    if not file.filename or not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(400, "请上传 .xlsx 或 .xls 文件")

    # 编码安全处理：避免 Windows GBK 环境中文文件名乱码
    safe_name = _safe_filename(file.filename)

    # 限制最大上传50MB，防止内存溢出
    MAX_SIZE = 50 * 1024 * 1024
    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(400, f"文件过大（{len(content)/1024/1024:.1f}MB），请上传不超过50MB的文件")

    file_id = uuid.uuid4().hex[:12]
    save_path = UPLOAD_DIR / f"{file_id}_{safe_name}"
    save_path.write_bytes(content)

    # 保存附件3（若提供）
    speed_table_uploaded = False
    speed_sheet_count = 0
    if speed_file and speed_file.filename and speed_file.filename.endswith((".xlsx", ".xls")):
        safe_speed_name = _safe_filename(speed_file.filename)
        speed_path = UPLOAD_DIR / f"{file_id}_speed_{safe_speed_name}"
        speed_content = await speed_file.read()
        speed_path.write_bytes(speed_content)
        try:
            import pandas as pd
            xl = pd.ExcelFile(speed_path)
            speed_sheet_count = len(xl.sheet_names)
            speed_table_uploaded = True
        except Exception:
            speed_path.unlink(missing_ok=True)
            # 不阻断上传——附件3校验失败仅仅跳过

    try:
        plates, parts, checks = load_and_validate(save_path)
    except Exception as e:
        save_path.unlink(missing_ok=True)
        if speed_table_uploaded:
            speed_path.unlink(missing_ok=True)
        raise HTTPException(400, f"数据校验失败: {e}")

    # 转换为可 JSON 序列化的格式
    clean_checks = {}
    for k, v in checks.items():
        if isinstance(v, (int, float, str, bool, type(None))):
            clean_checks[k] = v
        else:
            clean_checks[k] = str(v)

    summary = {
        "钢板数": int(len(plates)),
        "零件数": int(len(parts)),
        "齐套组数": int(parts.groupby(["分段号", "齐套优先级"]).ngroups),
        "分段数": int(parts["分段号"].nunique()),
        "套料图数": int(plates["套料图名"].nunique()),
    }

    return {
        "file_id": file_id,
        "filename": safe_name,
        "validation": clean_checks,
        "summary": summary,
        "speed_table_uploaded": speed_table_uploaded,
        "speed_sheet_count": speed_sheet_count,
    }


@app.post("/api/run")
async def run_model(payload: dict):
    """运行排产模型。payload = {file_id, params}"""
    file_id = payload.get("file_id")
    if not file_id:
        raise HTTPException(400, "缺少 file_id")

    # 找上传文件（附件2）
    candidates = [p for p in UPLOAD_DIR.glob(f"{file_id}_*") if "_speed_" not in p.name]
    if not candidates:
        raise HTTPException(404, "未找到上传文件，请重新上传")
    input_path = candidates[0]

    # 找附件3速度表（若上传过）
    speed_candidates = list(UPLOAD_DIR.glob(f"{file_id}_speed_*"))
    speed_table_path = str(speed_candidates[0]) if speed_candidates else None

    # 构建 ModelConfig
    user_params = payload.get("params", {})
    cfg = ModelConfig()
    # P1-5 FIX: type-coerce user params to prevent silent wrong-type injection
    _FLOAT_KEYS = {
        "cut_speed_mm_min", "v_cut_speed_mm_min", "rapid_speed_mm_min", "marking_speed_mm_min",
        "pierce_minutes", "plate_setup_minutes", "cut_remainder_minutes", "small_sort_minutes",
        "small_grind_speed_mm_min", "large_grind_speed_mm_min", "auto_bevel_speed_mm_min",
        "manual_bevel_speed_mm_min", "small_transfer_minutes", "large_transfer_minutes",
        "truss_travel_minutes", "crane_overlap_minutes", "crane_return_ratio",
        "crane_bevel_transfer_minutes", "worktable_hoist_minutes",
        "obj_weight_cmax", "obj_weight_kit", "obj_weight_load",
        "platen_idle_weight",
        "crane_large_threshold", "crane_small_threshold", "crane_cut_threshold",
        "crane_large_min", "crane_small_max", "crane_trip_empty_nominal",
        "crane_first_cut_quantile",
        "ga_crossover_rate", "ga_mutation_rate", "lns_destroy_ratio",
        "small_truss_direct_palletize_minutes", "small_truss_palletize_minutes",
        "small_grind_scan_minutes", "auto_bevel_overhead_minutes", "kit_dwell_minutes",
        "n2_bevel_truss_minutes",
    }
    _INT_KEYS = {
        "small_sorters", "small_grinders", "large_grinders", "auto_bevel_machines",
        "manual_bevel_stations", "agvs", "cutting_machines", "finish_buffer_capacity",
        "bevel_buffer_capacity", "half_buffer_capacity", "kit_buffer_capacity",
        "bevel_workstation_capacity",
        "local_search_iterations", "random_seed", "use_component_formula",
        "ga_population_size", "ga_generations", "ga_tournament_size", "lns_iterations",
        "dqn_seed_count", "nsga2_archive_size",
    }
    for key, value in user_params.items():
        if hasattr(cfg, key):
            try:
                if key in _FLOAT_KEYS:
                    value = float(value)
                elif key in _INT_KEYS:
                    value = int(value)
            except (TypeError, ValueError):
                raise HTTPException(400, f"参数 {key} 类型错误，期望数值")
            setattr(cfg, key, value)
    # P0-1 FIX: Mark that user explicitly set params — prevents adapt_to_data from overriding
    if user_params:
        cfg._user_set_crane_overlap = True

    # 创建结果目录
    run_id = uuid.uuid4().hex[:12]
    out_dir = RESULT_DIR / run_id
    is_dq_nsga2 = getattr(cfg, "optimizer_method", "sa_tabu") == "dq_nsga2"

    try:
        plates, parts, checks = load_and_validate(input_path)
        # P4-3：普通算法双轨并行；DQN+NSGA-II 只跑一次 Pareto 前沿，
        # 再分别按 capacity/balanced 规则从同一前沿选两个交付解。
        _prev_env = os.environ.get("OPTIMIZER_PARALLEL")
        os.environ["OPTIMIZER_PARALLEL"] = str(max(2, (os.cpu_count() or 4) // 2))

        def _run_one(track: str, sub: str | None):
            target = out_dir if sub is None else out_dir / sub
            return _run_model(
                plates, parts, cfg, target, checks, speed_table_path,
                eval_track=track, progress_tag=run_id, manage_progress=False,
            )

        try:
            if is_dq_nsga2:
                front_store: dict = {}
                result = _run_model(
                    plates, parts, cfg, out_dir, checks, speed_table_path,
                    eval_track="capacity", progress_tag=run_id,
                    manage_progress=False, pareto_front_store=front_store,
                )
                balanced_result = _run_model(
                    plates, parts, cfg, out_dir / "balanced", checks, speed_table_path,
                    eval_track="balanced", progress_tag=run_id,
                    manage_progress=False, pareto_front_store=front_store,
                )
            else:
                from concurrent.futures import ThreadPoolExecutor

                with ThreadPoolExecutor(max_workers=2) as _dual_exec:
                    _fut_cap = _dual_exec.submit(_run_one, "capacity", None)
                    _fut_bal = _dual_exec.submit(_run_one, "balanced", "balanced")
                    result = _fut_cap.result()
                    balanced_result = _fut_bal.result()
        finally:
            with _progress_lock:
                _optimization_progress.pop(run_id, None)
            if _prev_env is None:
                os.environ.pop("OPTIMIZER_PARALLEL", None)
            else:
                os.environ["OPTIMIZER_PARALLEL"] = _prev_env
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, f"模型运行失败: {e}")

    # 记录历史（P1-1 修复：原子化读-改-写）
    _atomic_history_update(lambda h: h["runs"].insert(0, {
        "id": run_id,
        "name": f"运行 {datetime.now().strftime('%m-%d %H:%M')}",
        "filename": str(candidates[0].name),
        "file_id": file_id,
        "created_at": datetime.now().isoformat(),
        "params": {k: getattr(cfg, k) for k in user_params if hasattr(cfg, k)},
        "base_metrics": result["metrics"].get("fifo", {}),
        "score": _history_score(result["metrics"]["optimized"], result["metrics"].get("fifo", {})),
        "algorithm_name": result.get("algorithmName", _optimizer_display_name(cfg)),
        "optimized_metrics": result["metrics"]["optimized"],
    }) or h)

    result["run_id"] = run_id
    _file_map = {
        "schedule_csv": "optimized_plate_schedule.csv",
        "completion_csv": "part_completion.csv",
        "kit_csv": "kit_groups.csv",
        "stages_csv": "process_stages.csv",
        "comparison_csv": "comparison.csv",
        "gantt_png": "cutting_gantt.png",
        "kit_png": "kit_span.png",
        "util_png": "resource_utilisation.png",
    }
    result["files"] = {k: f"{run_id}/{v}" for k, v in _file_map.items()}
    balanced_result["files"] = {k: f"{run_id}/balanced/{v}" for k, v in _file_map.items()}
    result["reportMode"] = "capacity"
    result["algorithmName"] = f"{result.get('algorithmName', '优化')}（产能优先/重工时）"
    if is_dq_nsga2:
        balanced_result["algorithmName"] = f"{balanced_result.get('algorithmName', '优化')}（兼顾三指标/Pareto综合选解）"
    else:
        balanced_result["algorithmName"] = f"{balanced_result.get('algorithmName', '优化')}（兼顾三指标/原评价）"
    result["reports"] = {
        "capacity": {**result, "run_id": run_id},
        "balanced": {**balanced_result, "run_id": run_id},
    }
    # 顶层字段保持 capacity，前端切换报告时用 reports[mode] 覆盖顶层
    return result


@app.get("/api/run/progress/{run_id}")
async def get_run_progress(run_id: str):
    """查询优化进度（供前端轮询）。"""
    with _progress_lock:
        prog = _optimization_progress.get(run_id)
    if prog is None:
        # 检查是否已完成（结果目录存在）
        if (RESULT_DIR / run_id).exists():
            return {"status": "completed", "progress_pct": 100.0}
        return {"status": "not_found", "progress_pct": 0.0}
    return {"status": "running", **prog}


@app.get("/api/report/{run_id}")
async def download_report(run_id: str, mode: str = "capacity"):
    """生成并下载 Word 建模报告。"""
    # P0-3 FIX: path traversal guard
    run_dir = _safe_result_path(run_id)
    if not run_dir.exists():
        raise HTTPException(404, "运行记录不存在")
    if mode == "balanced":
        run_dir = run_dir / "balanced"
        if not run_dir.exists():
            raise HTTPException(404, "平衡版结果不存在")

    docx_path = run_dir / "report.docx"
    if not docx_path.exists():
        # 调用 build_report.py 生成
        build_script = PROJECT_ROOT / "build_report.py"
        if not build_script.exists():
            raise HTTPException(500, "build_report.py 未找到")
        try:
            subprocess.run(
                [sys.executable, str(build_script), "--results", str(run_dir), "--output", str(docx_path)],
                check=True, capture_output=True, text=True, timeout=60,
            )
        except subprocess.CalledProcessError as e:
            raise HTTPException(500, f"报告生成失败: {e.stderr}")
        except subprocess.TimeoutExpired:
            raise HTTPException(500, "报告生成超时")

    return FileResponse(
        docx_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=f"建模报告_{run_id}.docx",
    )


@app.get("/api/download/{run_id}/{filename:path}")
async def download_file(run_id: str, filename: str):
    """下载结果 CSV / PNG 文件。"""
    # P0-2 FIX: path traversal guard
    file_path = _safe_result_path(run_id, filename)
    if not file_path.exists():
        raise HTTPException(404, f"文件不存在: {filename}")
    return FileResponse(file_path)


@app.get("/api/history")
async def get_history():
    """返回历史运行记录列表。"""
    # 读取历史；只有确实存在失效记录时才清理并写回，避免每次打开都重写文件
    history = _load_history()
    runs = history.get("runs", [])
    valid = [r for r in runs if (RESULT_DIR / r["id"]).exists()]
    if len(valid) != len(runs):
        history["runs"] = valid
        _save_history(history)
    return {"runs": valid}


@app.delete("/api/history/{run_id}")
async def delete_history(run_id: str):
    """删除某次运行记录及结果文件。"""
    run_dir = RESULT_DIR / run_id
    # P1-9 FIX: try/finally ensures history.json stays consistent even if rmtree fails
    try:
        if run_dir.exists():
            import shutil
            shutil.rmtree(run_dir)
    except OSError:
        # Log but continue — the history entry must still be removed
        pass
    # P1-1 修复：原子化读-改-写
    def _delete(history):
        history["runs"] = [r for r in history["runs"] if r["id"] != run_id]
        return history
    _atomic_history_update(_delete)
    return {"ok": True}


@app.put("/api/history/{run_id}/name")
async def rename_history(run_id: str, payload: dict):
    """重命名某次运行。"""
    new_name = payload.get("name", "").strip()
    if not new_name:
        raise HTTPException(400, "名称不能为空")
    # P1-1 修复：原子化读-改-写
    def _rename(history):
        for r in history["runs"]:
            if r["id"] == run_id:
                r["name"] = new_name
                return history
        raise HTTPException(404, "运行记录不存在")
    _atomic_history_update(_rename)
    return {"ok": True}


def _history_report_payload(
    run_id: str,
    history_entry: dict,
    summary_path: Path,
    fallback_name: str,
) -> dict:
    """从某个结果目录构造历史详情 payload（供 capacity/balanced 复用）。"""
    run_dir = summary_path.parent
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    stored_opt = history_entry.get("optimized_metrics") or summary.get("齐套感知优化", {})
    stored_base = history_entry.get("base_metrics") or summary.get("FIFO基线", {})
    stored_name = (
        history_entry.get("algorithm_name")
        or summary.get("算法名称")
        or fallback_name
    )

    schedule = pd.read_csv(run_dir / "optimized_plate_schedule.csv")
    groups = pd.read_csv(run_dir / "kit_groups.csv")
    stages = pd.read_csv(run_dir / "process_stages.csv")

    stored_makespan = stored_opt.get("总完工时间(h)")
    if stored_makespan is not None and float(stored_makespan) > 0:
        makespan_hours = float(stored_makespan)
    else:
        makespan_hours = float(schedule["切割完成(min)"].max()) / 60

    gantt_data = []
    for _, r in schedule.iterrows():
        gantt_data.append({
            "name": str(r["套料图名"]),
            "machine": str(r["切割机"]),
            "start": round(float(r["切割开始(min)"]) / 60, 4),
            "end": round(float(r["切割完成(min)"]) / 60, 4),
            "duration": round(float(r["切割工时(min)"]) / 60, 4),
            "section": str(r.get("分段号", "")),
            "priority": int(r.get("最低优先级", 0)),
            "table": int(r.get("工位", 0)),
            "tableEnd": round(float(r.get("工位完工(min)", r["切割完成(min)"])) / 60, 4),
            "tableStart": round(float(r.get("工位开始(min)", r["切割开始(min)"])) / 60, 4),
            "waitEnd": round(float(r.get("等待结束(min)", r["切割开始(min)"])) / 60, 4),
        })

    kit_data = []
    for _, r in groups.iterrows():
        kit_data.append({
            "label": f"{r['分段号']}-P{int(r['齐套优先级'])}",
            "partCount": int(r["零件数"]),
            "firstArrival": round(float(r["首件到齐套区"]) / 60, 4),
            "completion": round(float(r["齐套完成"]) / 60, 4),
            "span": round(float(r["齐套跨度(min)"]) / 60, 4),
        })

    total_stages = float(stages["结束(min)"].max()) if len(stages) > 0 else 1.0
    util_raw = stages.groupby("资源")["时长(min)"].sum().sort_values(ascending=False) if len(stages) > 0 else pd.Series(dtype=float)
    util_data = []
    for name, total_time in util_raw.items():
        pct = round(float(total_time / total_stages * 100), 2) if total_stages > 0 else 0.0
        util_data.append({
            "name": str(name),
            "totalHours": round(float(total_time) / 60, 2),
            "utilization": pct,
        })
    makespan_min = float(makespan_hours) * 60
    for mach in sorted(schedule["切割机"].unique()):
        load_min = float(schedule[schedule["切割机"] == mach]["切割工时(min)"].sum())
        util_data.append({
            "name": str(mach),
            "totalHours": round(load_min / 60, 2),
            "utilization": round(load_min / makespan_min * 100, 2) if makespan_min > 0 else 0.0,
        })

    try:
        comparison_df = pd.read_csv(run_dir / "comparison.csv")
        comparison_records = json.loads(comparison_df.to_json(orient="records", force_ascii=False))
    except Exception:
        comparison_records = []

    stages_data = []
    part_plate_map = {}
    try:
        part_completion = pd.read_csv(run_dir / "part_completion.csv", encoding="utf-8-sig")
        part_plate_map = dict(zip(
            part_completion["零件名"].astype(str),
            part_completion["套料图名"].astype(str),
        ))
    except Exception:
        pass
    for _, r in schedule.iterrows():
        stages_data.append({
            "part": str(r["套料图名"]),
            "stage": "切割",
            "resource": str(r["切割机"]),
            "start": round(float(r["切割开始(min)"]) / 60, 4),
            "end": round(float(r["切割完成(min)"]) / 60, 4),
        })
    for _, r in stages.iterrows():
        stages_data.append({
            "part": str(r.iloc[0]),
            "stage": str(r.iloc[1]),
            "resource": str(r.iloc[2]),
            "start": round(float(r.iloc[3]) / 60, 4),
            "end": round(float(r.iloc[4]) / 60, 4),
            "plate": _resolve_stage_plate(str(r.iloc[0]), part_plate_map),
        })

    buf_ts = None
    buf_ts_path = run_dir / "buffer_timeseries.json"
    if buf_ts_path.exists():
        try:
            buf_ts = json.loads(buf_ts_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            buf_ts = None

    _file_names = {
        "schedule_csv": "optimized_plate_schedule.csv",
        "completion_csv": "part_completion.csv",
        "kit_csv": "kit_groups.csv",
        "stages_csv": "process_stages.csv",
        "comparison_csv": "comparison.csv",
        "gantt_png": "cutting_gantt.png",
        "kit_png": "kit_span.png",
        "util_png": "resource_utilisation.png",
    }
    if run_dir.parent.name == "results":
        files = {k: f"{run_id}/{v}" for k, v in _file_names.items()}
    else:
        files = {k: f"{run_id}/{run_dir.name}/{v}" for k, v in _file_names.items()}

    return {
        "run_id": run_id,
        "algorithmName": stored_name,
        "metrics": {
            "fifo": stored_base,
            "optimized": stored_opt,
            "comparison": comparison_records,
        },
        "checks": {**summary.get("数据校验", {}), "工艺参数来源": summary.get("工艺参数来源", "未知")},
        "makespanHours": round(float(makespan_hours), 2),
        "ganttData": gantt_data,
        "kitSpanData": kit_data,
        "utilizationData": util_data,
        "stagesData": stages_data,
        "bufferTimeseries": buf_ts,
        "files": files,
    }


@app.get("/api/run/{run_id}")
async def get_run_detail(run_id: str):
    """获取某次历史运行的完整结果（用于切换查看）。"""
    run_dir = RESULT_DIR / run_id
    if not run_dir.exists():
        raise HTTPException(404, "运行记录不存在")
    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        raise HTTPException(404, "结果数据已丢失")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    # 历史详情优先使用 history.json 中固化的快照，确保与左侧历史列表完全一致
    history = _load_history()
    history_entry = next((h for h in history.get("runs", []) if h.get("id") == run_id), {})
    stored_opt = history_entry.get("optimized_metrics") or summary.get("齐套感知优化", {})
    stored_base = history_entry.get("base_metrics") or summary.get("FIFO基线", {})
    stored_name = (
        history_entry.get("algorithm_name")
        or summary.get("算法名称")
        or "SA+Tabu（线性评价）"
    )

    capacity_payload = _history_report_payload(
        run_id, history_entry, summary_path, stored_name,
    )
    capacity_payload["algorithmName"] = f"{capacity_payload.get('algorithmName', stored_name)}（产能优先/重工时）"
    capacity_payload["reportMode"] = "capacity"
    balanced_dir = run_dir / "balanced"
    if balanced_dir.exists() and (balanced_dir / "summary.json").exists():
        balanced_payload = _history_report_payload(
            run_id, {}, balanced_dir / "summary.json", stored_name,
        )
        if "DQN" in (balanced_payload.get("algorithmName", stored_name) or stored_name):
            balanced_payload["algorithmName"] = f"{balanced_payload.get('algorithmName', stored_name)}（兼顾三指标/Pareto综合选解）"
        else:
            balanced_payload["algorithmName"] = f"{balanced_payload.get('algorithmName', stored_name)}（兼顾三指标/原评价）"
        balanced_payload["reportMode"] = "balanced"
        capacity_report = dict(capacity_payload)
        capacity_payload["reports"] = {
            "capacity": capacity_report,
            "balanced": balanced_payload,
        }
    return capacity_payload

    # 重算图表数据
    try:
        schedule = pd.read_csv(run_dir / "optimized_plate_schedule.csv")
        groups = pd.read_csv(run_dir / "kit_groups.csv")
        stages = pd.read_csv(run_dir / "process_stages.csv")
    except Exception:
        raise HTTPException(500, "结果 CSV 文件损坏")

    stored_makespan = stored_opt.get("总完工时间(h)")
    if stored_makespan is not None and float(stored_makespan) > 0:
        makespan_hours = float(stored_makespan)
    else:
        makespan_hours = float(schedule["切割完成(min)"].max()) / 60
    gantt_data = []
    for _, r in schedule.iterrows():
        gantt_data.append({
            "name": str(r["套料图名"]),
            "machine": str(r["切割机"]),
            "start": round(float(r["切割开始(min)"]) / 60, 4),
            "end": round(float(r["切割完成(min)"]) / 60, 4),
            "duration": round(float(r["切割工时(min)"]) / 60, 4),
            "section": str(r.get("分段号", "")),
            "priority": int(r.get("最低优先级", 0)),
            "table": int(r.get("工位", 0)),
            "tableEnd": round(float(r.get("工位完工(min)", r["切割完成(min)"])) / 60, 4),
            "tableStart": round(float(r.get("工位开始(min)", r["切割开始(min)"])) / 60, 4),
            "waitEnd": round(float(r.get("等待结束(min)", r["切割开始(min)"])) / 60, 4),
        })

    kit_data = []
    for _, r in groups.iterrows():
        kit_data.append({
            "label": f"{r['分段号']}-P{int(r['齐套优先级'])}",
            "partCount": int(r["零件数"]),
            "firstArrival": round(float(r["首件到齐套区"]) / 60, 4),
            "completion": round(float(r["齐套完成"]) / 60, 4),
            "span": round(float(r["齐套跨度(min)"]) / 60, 4),
        })

    total_stages = float(stages["结束(min)"].max()) if len(stages) > 0 else 1.0
    util_raw = stages.groupby("资源")["时长(min)"].sum().sort_values(ascending=False) if len(stages) > 0 else pd.Series(dtype=float)
    util_data = []
    for name, total_time in util_raw.items():
        pct = round(float(total_time / total_stages * 100), 2) if total_stages > 0 else 0.0
        util_data.append({
            "name": str(name),
            "totalHours": round(float(total_time) / 60, 2),
            "utilization": pct,
        })
    makespan_min = float(makespan_hours) * 60
    for m in sorted(schedule["切割机"].unique()):
        load_min = float(schedule[schedule["切割机"] == m]["切割工时(min)"].sum())
        util_data.append({
            "name": str(m),
            "totalHours": round(load_min / 60, 2),
            "utilization": round(load_min / makespan_min * 100, 2) if makespan_min > 0 else 0.0,
        })

    # 读取对比表
    try:
        comparison_df = pd.read_csv(run_dir / "comparison.csv")
        comparison_records = json.loads(comparison_df.to_json(orient="records", force_ascii=False))
    except Exception:
        comparison_records = []

    # 工序明细（用于全流程回放，包含切割）
    stages_data = []
    part_plate_map = {}
    try:
        part_completion = pd.read_csv(run_dir / "part_completion.csv", encoding="utf-8-sig")
        part_plate_map = dict(zip(
            part_completion["零件名"].astype(str),
            part_completion["套料图名"].astype(str),
        ))
    except Exception:
        pass
    for _, r in schedule.iterrows():
        stages_data.append({
            "part": str(r["套料图名"]),
            "stage": "切割",
            "resource": str(r["切割机"]),
            "start": round(float(r["切割开始(min)"]) / 60, 4),
            "end": round(float(r["切割完成(min)"]) / 60, 4),
        })
    for _, r in stages.iterrows():
        stages_data.append({
            "part": str(r.iloc[0]),
            "stage": str(r.iloc[1]),
            "resource": str(r.iloc[2]),
            "start": round(float(r.iloc[3]) / 60, 4),
            "end": round(float(r.iloc[4]) / 60, 4),
            "plate": _resolve_stage_plate(str(r.iloc[0]), part_plate_map),
        })

    # 读取缓存时序数据（新运行有，旧历史记录可能缺失）
    buf_ts = None
    buf_ts_path = run_dir / "buffer_timeseries.json"
    if buf_ts_path.exists():
        try:
            buf_ts = json.loads(buf_ts_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            buf_ts = None

    return {
        "run_id": run_id,
        "algorithmName": stored_name,
        "metrics": {
            "fifo": stored_base,
            "optimized": stored_opt,
            "comparison": comparison_records,
        },
        "checks": {**summary.get("数据校验", {}), "工艺参数来源": summary.get("工艺参数来源", "未知")},
        "makespanHours": round(float(makespan_hours), 2),
        "ganttData": gantt_data,
        "kitSpanData": kit_data,
        "utilizationData": util_data,
        "stagesData": stages_data,
        "bufferTimeseries": buf_ts,
        "files": {
            "schedule_csv": f"{run_id}/optimized_plate_schedule.csv",
            "completion_csv": f"{run_id}/part_completion.csv",
            "kit_csv": f"{run_id}/kit_groups.csv",
            "stages_csv": f"{run_id}/process_stages.csv",
            "comparison_csv": f"{run_id}/comparison.csv",
            "gantt_png": f"{run_id}/cutting_gantt.png",
            "kit_png": f"{run_id}/kit_span.png",
            "util_png": f"{run_id}/resource_utilisation.png",
        },
    }


@app.post("/api/reschedule")
async def reschedule(payload: dict):
    """动态重调度：处理机器故障/插单/紧急插单等场景。

    payload = {
        run_id: str,           # 原运行ID
        scenario: str,         # "machine_failure" | "rush_order" | "reoptimize"
        fault_time_h: float,   # 故障发生时间（小时，从0开始）
        fault_machine: str,    # 故障机器名（如"N5"）
        fault_duration_h: float, # 故障持续时长（小时）
        rush_plates: list,     # 插单钢板列表（可选）
        time_limit_s: float,   # 重调度时间限制（默认5s）
    }
    """
    import time

    run_id = payload.get("run_id")
    scenario = payload.get("scenario", "reoptimize")
    fault_time_h = payload.get("fault_time_h", 0)
    fault_machine = payload.get("fault_machine", "")
    fault_duration_h = payload.get("fault_duration_h", 1.0)
    rush_plates = payload.get("rush_plates", [])
    time_limit_s = payload.get("time_limit_s", 5.0)

    if not run_id:
        raise HTTPException(400, "缺少 run_id")

    run_dir = RESULT_DIR / run_id
    if not run_dir.exists():
        raise HTTPException(404, "运行记录不存在")

    try:
        schedule = pd.read_csv(run_dir / "optimized_plate_schedule.csv")
    except Exception:
        raise HTTPException(500, "排程数据丢失，请重新运行")

    # 找原始数据：先查历史记录获取file_id，再匹配上传文件
    history = _load_history()
    file_id_for_run = None
    for h in history.get("runs", []):
        if h.get("id") == run_id:
            file_id_for_run = h.get("file_id", h.get("id"))
            break

    # 用file_id（或回退到run_id）查找上传文件
    search_id = file_id_for_run or run_id
    candidates = [p for p in UPLOAD_DIR.glob(f"{search_id}_*") if "_speed_" not in p.name]

    # 回退：尝试所有历史记录中的file_id
    if not candidates:
        for h in history.get("runs", []):
            hid = h.get("file_id", h.get("id"))
            h_candidates = [p for p in UPLOAD_DIR.glob(f"{hid}_*") if "_speed_" not in p.name]
            if h_candidates:
                candidates = h_candidates
                search_id = hid
                break

    if not candidates:
        raise HTTPException(500, "原始上传文件已丢失，请重新上传数据后运行")

    input_path = candidates[0]
    speed_candidates = list(UPLOAD_DIR.glob(f"{search_id}_speed_*"))
    speed_table_path = str(speed_candidates[0]) if speed_candidates else None

    t_start = time.time()

    try:
        plates, parts, checks = load_and_validate(input_path)
        pp = None
        speed_table = None
        if speed_table_path and Path(speed_table_path).exists():
            pp = load_process_params(Path(speed_table_path))
            speed_table = pp.speed_table

        cfg = ModelConfig()
        cfg.local_search_iterations = 100  # 重调度更快
        features = plate_features(plates, parts, cfg, speed_table)

        fault_time_min = fault_time_h * 60.0
        fault_duration_min = fault_duration_h * 60.0
        machines = list(schedule["切割机"].unique())

        # ── 冻结窗口 (P0-2/P0-3 FIX) ──
        # Only freeze plates on NON-fault machines that started before fault_time.
        # Plates on the FAULT machine: apply fault_delay if affected.
        if scenario == "machine_failure" and fault_machine:
            # Non-fault machines: freeze plates that started before fault_time
            frozen_mask = (schedule["切割开始(min)"] < fault_time_min) & (schedule["切割机"] != fault_machine)
            # Fault machine: freeze plates that FINISHED before fault_time (unaffected by fault)
            fault_early_mask = (schedule["切割完成(min)"] <= fault_time_min) & (schedule["切割机"] == fault_machine)
            frozen_mask = frozen_mask | fault_early_mask
        else:
            # reoptimize / rush_order: freeze all plates that started before fault_time
            frozen_mask = schedule["切割开始(min)"] < fault_time_min

        frozen_plates = schedule.loc[frozen_mask, "套料图名"].tolist()
        remaining_plates = [p for p in features[features.columns[2]].tolist()
                          if p not in frozen_plates]

        # ── 处理故障延迟 (P0-2 FIX) ──
        # Compute delays for plates on the fault machine affected by the fault window
        fault_delay = {}
        if scenario == "machine_failure" and fault_machine:
            affected = schedule[
                (schedule["切割机"] == fault_machine) &
                (schedule["切割开始(min)"] < fault_time_min + fault_duration_min) &
                (schedule["切割完成(min)"] > fault_time_min)
            ]
            for _, row in affected.iterrows():
                delay = fault_time_min + fault_duration_min - row["切割开始(min)"]
                fault_delay[row["套料图名"]] = max(0, delay)

        # ── 重优化剩余钢板 ──
        from improved_optimizer import SATabuOptimizer

        # 构建带冻结顺序的初始解
        remaining_features = features[features.columns[2]].isin(remaining_plates)
        rem_f = features[remaining_features].copy()

        # 初始顺序：原顺序中剩余的钢板 + 插单钢板
        original_order = schedule["套料图名"].tolist()
        init_order = [p for p in original_order if p in remaining_plates]
        # 插单钢板插入最前面
        if rush_plates:
            init_order = rush_plates + init_order

        # 快速重优化
        opt = SATabuOptimizer(features, parts, cfg, pp, seed=cfg.random_seed + 1)
        best_order, best_metrics, stats = opt.optimize(
            init_order,
            max_iterations=min(60, int(time_limit_s / 0.08)),
            time_limit_seconds=max(1.0, time_limit_s - (time.time() - t_start)),
        )

        # 重建完整排程
        name_col = features.columns[2]
        seg_col = features.columns[1]
        cut_candidates = [c for c in features.columns if '工时' in c and 'min' in c]
        if not cut_candidates:
            raise HTTPException(500, f"未找到切割工时列，可用列: {list(features.columns)}")
        cut_col = cut_candidates[0]
        pri_candidates = [c for c in features.columns if '优先' in c or '最低' in c]
        if not pri_candidates:
            raise HTTPException(500, f"未找到优先级列，可用列: {list(features.columns)}")
        pri_col = pri_candidates[0]
        fidx = features.set_index(name_col)
        machines_list = machine_names(cfg)

        final_rows = []
        free = {m: 0.0 for m in machines_list}
        worktables = {m: [0.0, 0.0] for m in machines_list}
        gun_free = {m: 0.0 for m in machines_list}
        crane_pool = CranePool()

        # 先放冻结钢板（保持原分配，P0-2 FIX: 故障机器上的冻结钢板应用延迟）
        for _, r in schedule[frozen_mask].iterrows():
            m = str(r["切割机"])
            orig_start = float(r["切割开始(min)"])
            orig_end = float(r["切割完成(min)"])
            orig_dur = float(r["切割工时(min)"])
            plate_name = str(r["套料图名"])
            # Apply fault_delay if this plate is on the fault machine and was affected
            delay = fault_delay.get(plate_name, 0.0)
            if delay > 0:
                orig_start += delay
                orig_end += delay
            handling = float(r.get("工位处理时间(min)", 0.0))
            if handling == 0.0 and plate_name in fidx.index:
                handling = float(fidx.loc[plate_name].get("工位处理时间(min)", 0.0))
            raw_start, raw_empty_end, raw_loaded_end = _reserve_raw_material_crane(
                crane_pool, orig_start, cfg,
            )
            table_idx = 0 if worktables[m][0] <= orig_start else 1
            table_end = orig_end + handling
            worktables[m][table_idx] = table_end
            gun_free[m] = max(gun_free[m], orig_end)
            free[m] = max(gun_free[m], min(worktables[m]))
            final_rows.append({
                "套料图名": plate_name, "分段号": str(r["分段号"]),
                "切割机": m, "切割开始(min)": orig_start,
                "切割完成(min)": orig_end,
                "切割工时(min)": orig_dur,
                "_fixed": True,
                "原料吊运开始(min)": raw_start,
                "原料空驶完成(min)": raw_empty_end,
                "原料吊运完成(min)": raw_loaded_end,
                "原料吊运返回完成(min)": raw_loaded_end,
                "工位开始(min)": orig_start,
                "工位": table_idx,
                "工位处理时间(min)": handling,
                "工位完工(min)": table_end,
                "最低优先级": int(r["最低优先级"]),
            })

        # 故障机器延后
        if scenario == "machine_failure" and fault_machine:
            gun_free[fault_machine] = max(
                gun_free[fault_machine],
                fault_time_min + fault_duration_min,
            )
            free[fault_machine] = max(gun_free[fault_machine], min(worktables[fault_machine]))

        # 重排剩余钢板
        ordered = fidx.loc[best_order].reset_index()
        for seq_i, (_, r) in enumerate(ordered.iterrows(), len(final_rows) + 1):
            m = pick_machine(r, free)
            raw_start, raw_empty_end, raw_loaded_end = _reserve_raw_material_crane(
                crane_pool, free[m], cfg,
            )
            st = max(free[m], raw_loaded_end)
            en = st + r[cut_col]
            handling = float(r.get("工位处理时间(min)", 0.0))
            table_idx = 0 if worktables[m][0] <= st else 1
            table_end = en + handling
            worktables[m][table_idx] = table_end
            gun_free[m] = en
            free[m] = max(gun_free[m], min(worktables[m]))
            final_rows.append({
                "套料图名": str(r[name_col]), "分段号": str(r[seg_col]),
                "切割机": m, "切割序号": seq_i,
                "切割开始(min)": st, "切割完成(min)": en,
                "切割工时(min)": r[cut_col], "最低优先级": int(r[pri_col]),
                "_fixed": False,
                "原料吊运开始(min)": raw_start,
                "原料空驶完成(min)": raw_empty_end,
                "原料吊运完成(min)": raw_loaded_end,
                "原料吊运返回完成(min)": raw_loaded_end,
                "工位开始(min)": st,
                "工位": table_idx,
                "工位处理时间(min)": handling,
                "工位完工(min)": table_end,
            })

        new_schedule = pd.DataFrame(final_rows)
        new_schedule, _, new_groups, new_metrics, new_stages, new_buf_ts = build_joint_schedule(
            new_schedule, parts, cfg, pp,
        )

        # 构建响应
        elapsed = time.time() - t_start
        makespan = float(new_stages["结束(min)"].max()) if len(new_stages) > 0 else float(new_schedule["切割完成(min)"].max())

        gantt_data = []
        for _, r in new_schedule.iterrows():
            gantt_data.append({
                "name": str(r["套料图名"]),
                "machine": str(r["切割机"]),
                "start": round(float(r["切割开始(min)"]) / 60, 4),
                "end": round(float(r["切割完成(min)"]) / 60, 4),
            "duration": round(float(r["切割工时(min)"]) / 60, 4),
            "section": str(r.get("分段号", "")),
            "priority": int(r.get("最低优先级", 0)),
            "table": int(r.get("工位", 0)),
            "tableEnd": round(float(r.get("工位完工(min)", r["切割完成(min)"])) / 60, 4),
            "tableStart": round(float(r.get("工位开始(min)", r["切割开始(min)"])) / 60, 4),
            "waitEnd": round(float(r.get("等待结束(min)", r["切割开始(min)"])) / 60, 4),
        })

        # 构建 stagesData（含切割+下游工序）
        stages_data = []
        part_plate_map = {}
        try:
            part_plate_map = dict(zip(
                parts["零件名"].astype(str),
                parts["套料图名"].astype(str),
            ))
        except Exception:
            pass
        for _, r in new_schedule.iterrows():
            stages_data.append({
                "part": str(r["套料图名"]),
                "stage": "切割",
                "resource": str(r["切割机"]),
                "start": round(float(r["切割开始(min)"]) / 60, 4),
                "end": round(float(r["切割完成(min)"]) / 60, 4),
            })
        for _, r in new_stages.iterrows():
            stages_data.append({
                "part": str(r.iloc[0]),
                "stage": str(r.iloc[1]),
                "resource": str(r.iloc[2]),
                "start": round(float(r.iloc[3]) / 60, 4),
                "end": round(float(r.iloc[4]) / 60, 4),
                "plate": _resolve_stage_plate(str(r.iloc[0]), part_plate_map),
            })

        # 构建 utilizationData
        total_makespan_stages = float(new_stages["结束(min)"].max()) if len(new_stages) > 0 else 1.0
        util_raw = new_stages.groupby("资源")["时长(min)"].sum().sort_values(ascending=False) if len(new_stages) > 0 else pd.Series(dtype=float)
        util_data = []
        for name, total_time in util_raw.items():
            util_pct = round(float(total_time / total_makespan_stages * 100), 2) if total_makespan_stages > 0 else 0.0
            util_data.append({
                "name": str(name),
                "totalHours": round(float(total_time) / 60, 2),
                "utilization": util_pct,
            })
        makespan_min = float(makespan)
        for m in sorted(new_schedule["切割机"].unique()):
            load_min = float(new_schedule[new_schedule["切割机"] == m]["切割工时(min)"].sum())
            util_data.append({
                "name": str(m),
                "totalHours": round(load_min / 60, 2),
                "utilization": round(load_min / makespan_min * 100, 2) if makespan_min > 0 else 0.0,
            })

        return {
            "scenario": scenario,
            "reschedule_time_s": round(elapsed, 3),
            "frozen_plates": len(frozen_plates),
            "reoptimized_plates": len(best_order),
            "metrics": {
                "optimized": {k: round(v, 4) if isinstance(v, float) else v
                            for k, v in new_metrics.items()},
            },
            "makespanHours": round(float(makespan) / 60, 2),
            "ganttData": gantt_data,
            "stagesData": stages_data,
            "utilizationData": util_data,
            "bufferTimeseries": new_buf_ts,
            "stats": stats,
        }

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, f"重调度失败: {e}")


@app.post("/api/monte_carlo/{run_id}")
async def run_monte_carlo(run_id: str, payload: dict):
    """运行Monte Carlo鲁棒性分析。

    payload = {n_samples: int, perturbation_pct: float}
    """
    n_samples = payload.get("n_samples", 30)
    perturbation_pct = payload.get("perturbation_pct", 0.10)

    # 找上传文件
    history = _load_history()
    file_id_for_run = None
    for h in history.get("runs", []):
        if h.get("id") == run_id:
            file_id_for_run = h.get("file_id", h.get("id"))
            break

    search_id = file_id_for_run or run_id
    candidates = [p for p in UPLOAD_DIR.glob(f"{search_id}_*") if "_speed_" not in p.name]
    if not candidates:
        raise HTTPException(404, "原始上传文件已丢失")

    input_path = candidates[0]
    speed_candidates = list(UPLOAD_DIR.glob(f"{search_id}_speed_*"))
    speed_table_path = str(speed_candidates[0]) if speed_candidates else None

    try:
        plates, parts, checks = load_and_validate(input_path)
        pp = None
        if speed_table_path and Path(speed_table_path).exists():
            pp = load_process_params(Path(speed_table_path))

        cfg = ModelConfig()
        # P1-7 FIX: accept user params instead of hardcoding cutting_machines=3
        user_params = payload.get("params", {})
        for key, value in user_params.items():
            if hasattr(cfg, key):
                try:
                    setattr(cfg, key, float(value) if isinstance(value, str) and '.' in value else int(value) if isinstance(value, str) else value)
                except (TypeError, ValueError):
                    pass  # skip malformed params, use defaults
        # Apply safe defaults for Monte Carlo if not overridden by user
        if not user_params or "cutting_machines" not in user_params:
            cfg.cutting_machines = 3
        if not user_params or "use_component_formula" not in user_params:
            cfg.use_component_formula = True

        mc_result = monte_carlo_robustness(
            plates, parts, cfg, pp,
            n_samples=n_samples,
            perturbation_pct=perturbation_pct,
        )
        # Remove raw samples for API response size
        mc_result.pop("详细样本", None)
        return mc_result
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, f"Monte Carlo分析失败: {e}")


# ── 托管前端静态文件（构建后生效；必须在所有 API 路由之后） ──
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
if FRONTEND_DIST.exists():
    # ── P0-2 修复：SPA fallback 路径穿越防护 ──
    _FRONTEND_ROOT = FRONTEND_DIST.resolve()

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        # ── P0-4 修复：API 路径前缀显式拦截，避免 SPA fallback 吞掉 404 ──
        if full_path.startswith("api/"):
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=404, content={"detail": f"API endpoint not found: /{full_path}"})
        # 解析候选路径并校验安全边界，防止 ../../etc/passwd 类攻击
        candidate = (FRONTEND_DIST / full_path).resolve()
        if not str(candidate).startswith(str(_FRONTEND_ROOT) + _os.sep) and candidate != _FRONTEND_ROOT:
            raise HTTPException(404)
        if candidate.exists() and candidate.is_file():
            return FileResponse(candidate, headers={"Cache-Control": "no-cache"})
        index = FRONTEND_DIST / "index.html"
        if index.exists():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        raise HTTPException(404, "页面不存在")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
