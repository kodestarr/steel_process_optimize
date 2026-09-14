"""动态扰动响应引擎。

这个模块把设备故障、订单变更和在制钢板处置统一表达为事件，并使用
“状态快照 -> 贪心修复 -> 时间受限改进 -> 全链路仿真”的流程生成新排程。

设计目标：
1. 资源故障可以覆盖切割头、胎架、配套打磨机、AGV、天车、桁架和坡口设备；
2. 多个故障可以重叠，后续重排可以从上一次动态状态继续；
3. 订单取消/改优先级时，已完成和加工中的钢板按明确的物理状态处理；
4. 响应速度优先，服务端默认把搜索控制在用户给定的时间预算内。
"""

from __future__ import annotations

import copy
import json
import math
import os
import random
import shutil
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from steel_schedule_model import (
    AvailabilityCalendar,
    ModelConfig,
    ProcessParams,
    build_joint_schedule,
    load_and_validate_fast,
    load_process_params,
    machine_names,
    plate_features,
)
from improved_optimizer import SATabuOptimizer
from ga_lns_optimizer import GeneticLNSOptimizer
from dqn_nsga2_optimizer import DqnNsga2Optimizer
from project_paths import resolve_recorded_path, run_result_dir, to_submission_relative
from result_store import ResultStore


class DynamicRescheduleCancelled(Exception):
    pass


def safe_clock_to_minutes(value: Any, default: float | None = None) -> float:
    """把 `HH:MM:SS`、`HH:MM` 或 ISO 时间转成排程分钟。"""
    if value is None or str(value).strip() == "":
        if default is None:
            raise ValueError("时间不能为空")
        return float(default)
    text = str(value).strip()
    if "-" in text or "T" in text:
        normalized = text.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(normalized)
            return float(dt.hour * 60 + dt.minute + dt.second / 60.0)
        except ValueError:
            pass
    parts = text.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"无法解析时间: {value}")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
    except ValueError as exc:
        raise ValueError(f"无法解析时间: {value}") from exc
    if minute < 0 or minute >= 60 or second < 0 or second >= 60:
        raise ValueError(f"时间分量越界: {value}")
    return float(hour * 60 + minute + second / 60.0)


def minutes_to_clock(value: float) -> str:
    total_seconds = max(0, int(round(float(value) * 60.0)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _resource_catalog(cfg: ModelConfig) -> list[dict]:
    """生成前端可选择的动态资源目录。"""
    items: list[dict] = []
    machines = machine_names(cfg)

    def add(
        resource_id: str,
        label: str,
        group: str,
        actual_ids: list[str],
        description: str = "",
    ) -> None:
        items.append({
            "id": resource_id,
            "label": label,
            "group": group,
            "actual_ids": actual_ids,
            "description": description,
        })

    for machine in machines:
        add(
            f"head:{machine}",
            f"{machine} 切割头",
            "切割",
            [f"head:{machine}"],
            "该切割头停用后，对应切割机不能新增切割任务。",
        )
        for table_idx in (0, 1):
            add(
                f"table:{machine}:{table_idx}",
                f"{machine} 胎架 {table_idx + 1}",
                "胎架",
                [f"table:{machine}:{table_idx}"],
                "只封禁该胎架，同一切割头的另一胎架仍可使用。",
            )

    per_side_lg = max(1, math.ceil(cfg.large_grinders / 2))
    per_side_sg = max(1, math.ceil(cfg.small_grinders / 2))
    per_side_truss = max(1, math.ceil(cfg.small_sorters / 2))

    for machine in machines:
        add(
            f"large_grinder:{machine}",
            f"{machine} 配套大件打磨机",
            "打磨",
            [f"人工打磨{machine}{i + 1}" for i in range(per_side_lg)],
            "故障时该切割头只能处理不含大件的钢板。",
        )
        for i in range(per_side_lg):
            add(
                f"large_grinder:{machine}:{i + 1}",
                f"{machine} 大件打磨机 {i + 1}",
                "打磨",
                [f"人工打磨{machine}{i + 1}"],
                "仅停用该单机，不影响同侧其他大件打磨机。",
            )
        add(
            f"small_grinder:{machine}",
            f"{machine} 配套小件打磨机",
            "打磨",
            [f"自动打磨{machine}{i + 1}" for i in range(per_side_sg)],
            "影响需要小件打磨的钢板。",
        )
        for i in range(per_side_sg):
            add(
                f"small_grinder:{machine}:{i + 1}",
                f"{machine} 小件打磨机 {i + 1}",
                "打磨",
                [f"自动打磨{machine}{i + 1}"],
                "仅停用该单机。",
            )
        for i in range(per_side_truss):
            add(
                f"truss:{machine}:{i + 1}",
                f"{machine} 分拣桁架臂 {i + 1}",
                "分拣",
                [f"自动分拣{machine}{i + 1}"],
                "故障期间由其他可用桁架臂分担。",
            )

    for i in range(max(1, cfg.auto_bevel_machines)):
        add(f"auto_bevel:{i + 1}", f"自动坡口机 {i + 1}", "坡口", [f"自动坡口{i + 1}"])
    for i in range(max(1, cfg.manual_bevel_stations)):
        add(f"manual_bevel:{i + 1}", f"人工坡口工位 {i + 1}", "坡口", [f"人工坡口{i + 1}"])
    for i in range(max(1, cfg.agvs)):
        add(f"agv:{i + 1}", f"AGV {i + 1}", "物流", [f"AGV{i + 1}"])
    add("crane:1", "人工天车", "物流", ["天车"])
    return items


@dataclass
class FaultEvent:
    event_id: str
    resource_id: str
    start_min: float
    end_min: float
    start_text: str
    duration_text: str
    end_text: str | None = None
    status: str = "scheduled"

    def to_dict(self) -> dict:
        return {
            "id": self.event_id,
            "resource_id": self.resource_id,
            "start_min": round(self.start_min, 4),
            "end_min": round(self.end_min, 4),
            "start_time": self.start_text,
            "duration": self.duration_text,
            "end_time": self.end_text,
            "status": self.status,
        }


@dataclass
class OrderChange:
    plate_name: str
    action: str
    priority: int | None = None

    def to_dict(self) -> dict:
        payload = {"plate_name": self.plate_name, "action": self.action}
        if self.priority is not None:
            payload["priority"] = self.priority
        return payload


class DynamicRescheduleService:
    """围绕某个基础运行记录执行状态化动态重排。"""

    STATE_FORMAT_VERSION = 3
    STATE_FILE = "dynamic_state.json"
    SCHEDULE_FILE = "dynamic_schedule.csv"
    STAGES_FILE = "dynamic_stages.csv"
    METRICS_FILE = "dynamic_metrics.json"

    def __init__(
        self,
        run_dir: Path,
        input_path: Path,
        speed_table_path: str | Path | None = None,
        params: dict | None = None,
        override_input_path: Path | None = None,
        override_speed_table_path: Path | None = None,
        input_file_id: str | None = None,
        speed_file_id: str | None = None,
        input_filename: str | None = None,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.store = ResultStore(run_result_dir(self.run_dir))
        self.read_dir = self.store.active_dir()
        self.write_dir = self.read_dir
        self.job_id = ""
        self.input_path = Path(input_path)
        self.speed_table_path = Path(speed_table_path) if speed_table_path else None
        self.params = dict(params or {})
        self.override_input_path = Path(override_input_path) if override_input_path else None
        self.override_speed_table_path = Path(override_speed_table_path) if override_speed_table_path else None
        self.input_file_id = input_file_id
        self.speed_file_id = speed_file_id
        self.input_filename = input_filename
        self._cancel_check = None

    def _raise_if_cancelled(self) -> None:
        if self._cancel_check is not None and self._cancel_check():
            raise DynamicRescheduleCancelled("用户已紧急中断动态优化")

    # ── 基础数据与状态 ───────────────────────────────────
    def _effective_input_paths(
        self,
        state: dict | None = None,
        use_override: bool = True,
    ) -> tuple[Path, Path | None]:
        state = state or self._load_state()
        input_path = (
            self.override_input_path
            if use_override and self.override_input_path
            else (resolve_recorded_path(state["input_path"]) if state.get("input_path") else self.input_path)
        )
        speed_path = self.override_speed_table_path if use_override else None
        if speed_path is None and state.get("speed_table_path"):
            speed_path = resolve_recorded_path(state["speed_table_path"])
        if speed_path is None:
            speed_path = self.speed_table_path
        return Path(input_path), Path(speed_path) if speed_path else None

    def _load_problem(self, state: dict | None = None, use_override: bool = True):
        input_path, speed_table_path = self._effective_input_paths(state, use_override=use_override)
        plates, parts, checks = load_and_validate_fast(input_path)
        pp: ProcessParams | None = None
        speed_table = None
        if speed_table_path and speed_table_path.exists():
            pp = load_process_params(speed_table_path)
            speed_table = pp.speed_table

        cfg = ModelConfig()
        for key, value in self.params.items():
            if hasattr(cfg, key):
                try:
                    current = getattr(cfg, key)
                    if isinstance(current, bool):
                        value = bool(value)
                    elif isinstance(current, int) and not isinstance(current, bool):
                        value = int(value)
                    elif isinstance(current, float):
                        value = float(value)
                    setattr(cfg, key, value)
                except (TypeError, ValueError):
                    pass
        if self.params:
            cfg._user_set_crane_overlap = True
        cfg = cfg.adapt_to_data(plates, parts, pp)
        return plates, parts, checks, cfg, pp, speed_table

    def _load_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return copy.deepcopy(default)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return copy.deepcopy(default)

    def _write_json(self, path: Path, payload: Any) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def _state_path(self) -> Path:
        return self.read_dir / self.STATE_FILE

    def _write_state_path(self) -> Path:
        return self.write_dir / self.STATE_FILE

    def _load_state(self) -> dict:
        return self._load_json(
            self._state_path(),
            {
                "version": 0,
                "decision_time_min": 0.0,
                "decision_time": "00:00:00",
                "faults": [],
                "order_changes": [],
            },
        )

    def _load_previous_schedule(self, state: dict | None = None, use_dynamic: bool = True) -> pd.DataFrame:
        dynamic_path = self.read_dir / self.SCHEDULE_FILE
        source = (
            dynamic_path
            if use_dynamic and dynamic_path.exists()
            else self.read_dir / "optimized_plate_schedule.csv"
        )
        if not source.exists():
            raise FileNotFoundError("找不到可用的排程文件")
        schedule = pd.read_csv(source)
        if "工位" in schedule.columns:
            schedule["工位"] = schedule["工位"].fillna(0).astype(int)
        return schedule

    def _catalog(self, cfg: ModelConfig) -> list[dict]:
        return _resource_catalog(cfg)

    def _baseline_metrics(self) -> dict:
        summary = self._load_json(self.read_dir / "summary.json", {})
        for key in ("齐套感知优化", "optimized_metrics"):
            if isinstance(summary.get(key), dict):
                return dict(summary[key])
        return {}

    def _persist_canonical_outputs(
        self,
        schedule: pd.DataFrame,
        complete: pd.DataFrame,
        groups: pd.DataFrame,
        metrics: dict,
        stages: pd.DataFrame,
        buffer_ts: list,
        checks: dict,
        remaining_model: str,
    ) -> pd.DataFrame:
        """把动态结果覆盖到该运行的标准输出，保证历史读取口径一致。"""
        base_metrics = self._baseline_metrics()
        schedule.to_csv(self.write_dir / "optimized_plate_schedule.csv", index=False, encoding="utf-8-sig")
        complete.to_csv(self.write_dir / "part_completion.csv", index=False, encoding="utf-8-sig")
        groups.to_csv(self.write_dir / "kit_groups.csv", index=False, encoding="utf-8-sig")
        stages.to_csv(self.write_dir / "process_stages.csv", index=False, encoding="utf-8-sig")
        (self.write_dir / "buffer_timeseries.json").write_text(
            json.dumps(buffer_ts, ensure_ascii=False),
            encoding="utf-8",
        )
        comparison_rows = [
            {"方案": "基础方案", **base_metrics},
            {"方案": f"动态响应-{remaining_model}", **metrics},
        ]
        comparison = pd.DataFrame(comparison_rows)
        for col in ["总完工时间(h)", "加权平均齐套跨度(h)", "最大齐套跨度(h)", "切割负载差(h)", "整体产能(张板/班)"]:
            if col not in comparison.columns:
                continue
            base_val = comparison.loc[0, col]
            opt_val = comparison.loc[1, col]
            if col == "整体产能(张板/班)":
                comparison.loc[1, col + "改善率"] = (opt_val - base_val) / base_val if base_val else 0.0
            else:
                comparison.loc[1, col + "改善率"] = (base_val - opt_val) / base_val if base_val else 0.0
        comparison.to_csv(self.write_dir / "comparison.csv", index=False, encoding="utf-8-sig")

        summary_path = self.write_dir / "summary.json"
        summary = self._load_json(summary_path, {})
        summary["数据校验"] = checks
        summary["FIFO基线"] = summary.get("FIFO基线", base_metrics)
        summary["齐套感知优化"] = metrics
        summary["算法名称"] = f"动态响应 - {remaining_model}"
        summary["动态响应"] = {
            "模型": remaining_model,
            "更新时间": datetime.now().isoformat(timespec="seconds"),
        }
        self._write_json(summary_path, summary)
        balanced_dir = self.write_dir / "balanced"
        balanced_dir.mkdir(parents=True, exist_ok=True)
        for filename in (
            "optimized_plate_schedule.csv",
            "part_completion.csv",
            "kit_groups.csv",
            "process_stages.csv",
            "buffer_timeseries.json",
            "comparison.csv",
            "summary.json",
        ):
            shutil.copyfile(self.write_dir / filename, balanced_dir / filename)
        return comparison

    def _catalog_index(self, catalog: list[dict]) -> dict[str, dict]:
        return {str(item["id"]): item for item in catalog}

    def _actual_ids(self, resource_id: str, catalog_index: dict[str, dict]) -> list[str]:
        item = catalog_index.get(resource_id)
        if item is None:
            raise ValueError(f"未知资源: {resource_id}")
        return [str(value) for value in item.get("actual_ids", [])]

    def get_state(self) -> dict:
        state = self._load_state()
        _, _, _, cfg, _, _ = self._load_problem(state)
        metrics = self._load_json(self.read_dir / self.METRICS_FILE, {})
        input_path, _ = self._effective_input_paths(state)
        return {
            "run_id": self.run_dir.name,
            "version": int(state.get("version", 0)),
            "state_format_version": int(state.get("state_format_version", 0) or 0),
            "legacy_dynamic_state": int(state.get("state_format_version", 0) or 0) != self.STATE_FORMAT_VERSION,
            "decision_time": state.get("decision_time", "00:00:00"),
            "faults": state.get("faults", []),
            "order_changes": state.get("order_changes", []),
            "resource_catalog": self._catalog(cfg),
            "metrics": metrics,
            "has_dynamic_state": self._state_path().exists(),
            "input_file": to_submission_relative(input_path),
            "input_filename": state.get("input_filename") or Path(input_path).name,
            "input_file_id": state.get("input_file_id"),
            "data_change_summary": state.get("data_change_summary", {}),
        }

    def clear_uploaded_file(self) -> dict:
        """删除动态订单附件，恢复基础运行原始附件。"""
        state = self._load_state()
        job_id = f"clear_{uuid.uuid4().hex[:8]}"
        staging = self.store.staging_dir(job_id)
        if self.read_dir.exists():
            shutil.copytree(self.read_dir, staging, dirs_exist_ok=True)
        self.write_dir = staging
        state["input_path"] = to_submission_relative(self.input_path)
        state["speed_table_path"] = to_submission_relative(self.speed_table_path) if self.speed_table_path else None
        state["input_file_id"] = None
        state["speed_file_id"] = None
        state["input_filename"] = self.input_path.name
        state["data_change_summary"] = {
            "added_plates": [],
            "removed_plates": [],
            "common_plates": 0,
        }
        self._write_json(self._write_state_path(), state)
        self.store.commit(staging, job_id)
        self.read_dir = self.store.active_dir()
        return self.get_state()

    # ── 事件解析 ─────────────────────────────────────────
    def _parse_faults(
        self,
        raw_faults: list[dict],
        decision_time: float,
        previous_state: dict,
        catalog_index: dict[str, dict],
    ) -> list[FaultEvent]:
        previous = {str(item.get("id")): item for item in previous_state.get("faults", [])}
        previous_decision = float(previous_state.get("decision_time_min", 0.0) or 0.0)
        events: list[FaultEvent] = []
        for index, raw in enumerate(raw_faults or []):
            if not isinstance(raw, dict):
                continue
            resource_id = str(raw.get("resource_id") or raw.get("resource") or "").strip()
            if not resource_id:
                continue
            if resource_id not in catalog_index:
                raise ValueError(f"未知资源: {resource_id}")
            event_id = str(raw.get("id") or f"fault-{index + 1}")
            start_text = str(raw.get("start_time") or raw.get("fault_time") or "00:00:00")
            duration_text = str(raw.get("duration") or raw.get("repair_duration") or "00:00:00")
            start_min = safe_clock_to_minutes(start_text)
            duration_min = max(0.0, safe_clock_to_minutes(duration_text, 0.0))
            end_text = raw.get("end_time")
            if end_text not in (None, ""):
                end_min = safe_clock_to_minutes(str(end_text))
            else:
                end_min = start_min + duration_min

            previous_event = previous.get(event_id)
            if previous_event is not None:
                old_start = float(previous_event.get("start_min", 0.0) or 0.0)
                if old_start < previous_decision and abs(start_min - old_start) > 1e-6:
                    # 已发生故障不能推迟到另一个开始时刻；保留旧开始时间，只允许调整修复结束。
                    start_min = old_start
                    start_text = str(previous_event.get("start_time", minutes_to_clock(old_start)))

            if end_min < start_min:
                raise ValueError(f"故障 {event_id} 的修复结束早于开始时间")
            if decision_time >= end_min:
                status = "repaired"
            elif start_min <= decision_time < end_min:
                status = "active"
            else:
                status = "scheduled"
            events.append(
                FaultEvent(
                    event_id=event_id,
                    resource_id=resource_id,
                    start_min=start_min,
                    end_min=end_min,
                    start_text=start_text,
                    duration_text=duration_text,
                    end_text=str(end_text) if end_text else None,
                    status=status,
                )
            )
        events.sort(key=lambda item: (item.start_min, item.end_min, item.event_id))
        return events

    def _parse_order_changes(self, raw_changes: list[dict]) -> list[OrderChange]:
        changes: list[OrderChange] = []
        for raw in raw_changes or []:
            if not isinstance(raw, dict):
                continue
            plate_name = str(raw.get("plate_name") or raw.get("plate") or "").strip()
            action = str(raw.get("action") or "none").strip()
            if not plate_name or action in ("", "none", "keep"):
                continue
            priority = raw.get("priority")
            if action == "priority":
                if priority is None:
                    continue
                priority = int(priority)
            changes.append(OrderChange(plate_name=plate_name, action=action, priority=priority))
        return changes

    # ── 资源能力与排程构造 ───────────────────────────────
    def _calendar(
        self,
        faults: list[FaultEvent],
        catalog_index: dict[str, dict],
    ) -> AvailabilityCalendar:
        calendar = AvailabilityCalendar()
        for fault in faults:
            for actual_id in self._actual_ids(fault.resource_id, catalog_index):
                calendar.add(actual_id, fault.start_min, fault.end_min)
        return calendar

    @staticmethod
    def _part_stats(parts: pd.DataFrame) -> dict[str, dict]:
        stats: dict[str, dict] = {}
        for plate_name, group in parts.groupby("套料图名", sort=False):
            small = group[group["零件类型"] == "small"]
            large = group[group["零件类型"] == "large"]
            stats[str(plate_name)] = {
                "has_small": len(small) > 0,
                "has_large": len(large) > 0,
                "small_needs_grind": bool(
                    (pd.to_numeric(small.get("打磨长度_模型(mm)", 0), errors="coerce").fillna(0) > 0).any()
                ),
                "large_needs_grind": bool(
                    (pd.to_numeric(large.get("打磨长度_模型(mm)", 0), errors="coerce").fillna(0) > 0).any()
                ),
                "needs_auto_bevel": bool(
                    (pd.to_numeric(group.get("自动坡口长度(mm)", 0), errors="coerce").fillna(0) > 0).any()
                ),
                "needs_manual_bevel": bool(
                    (pd.to_numeric(group.get("人工坡口长度(mm)", 0), errors="coerce").fillna(0) > 0).any()
                ),
            }
        return stats

    def _groups_for_plate(
        self,
        plate_name: str,
        machine: str,
        cfg: ModelConfig,
        catalog_index: dict[str, dict],
        part_stats: dict[str, dict],
    ) -> list[list[str]]:
        stats = part_stats.get(plate_name, {})
        groups: list[list[str]] = []
        if stats.get("has_large"):
            groups.append(self._actual_ids(f"large_grinder:{machine}", catalog_index))
            groups.append(self._actual_ids("crane:1", catalog_index))
            if stats.get("needs_manual_bevel"):
                groups.append([
                    actual
                    for key, item in catalog_index.items()
                    if key.startswith("manual_bevel:")
                    for actual in item.get("actual_ids", [])
                ])
        if stats.get("has_small"):
            groups.append([
                actual
                for key, item in catalog_index.items()
                if key.startswith(f"truss:{machine}:")
                for actual in item.get("actual_ids", [])
            ])
            if machine.startswith("N5") or stats.get("small_needs_grind"):
                groups.append([
                    actual
                    for key, item in catalog_index.items()
                    if key.startswith(f"small_grinder:{machine}:")
                    for actual in item.get("actual_ids", [])
                ])
            if stats.get("needs_auto_bevel"):
                groups.append([
                    actual
                    for key, item in catalog_index.items()
                    if key.startswith("auto_bevel:")
                    for actual in item.get("actual_ids", [])
                ])
            groups.append([
                actual
                for key, item in catalog_index.items()
                if key.startswith("agv:")
                for actual in item.get("actual_ids", [])
            ])
        return [group for group in groups if group]

    @staticmethod
    def _group_available_at(calendar: AvailabilityCalendar, group: list[str], at_time: float) -> bool:
        return any(calendar.is_available(resource_id, at_time, 0.0) for resource_id in group)

    def _next_ready_for_groups(
        self,
        calendar: AvailabilityCalendar,
        groups: list[list[str]],
        start: float,
    ) -> float:
        cursor = max(0.0, float(start))
        for _ in range(50):
            group_ready: list[float] = []
            for group in groups:
                candidates = [
                    calendar.next_available(resource_id, cursor, 0.0)
                    for resource_id in group
                ]
                group_ready.append(min(candidates) if candidates else cursor)
            target = max(group_ready) if group_ready else cursor
            if target <= cursor + 1e-9:
                return cursor
            cursor = target
        return cursor

    def _schedule_cut_order(
        self,
        order: list[str],
        features: pd.DataFrame,
        cfg: ModelConfig,
        calendar: AvailabilityCalendar,
        catalog_index: dict[str, dict],
        part_stats: dict[str, dict],
        initial_state: dict,
        start_seq: int = 1,
        schedule_floor: float = 0.0,
    ) -> tuple[pd.DataFrame, dict]:
        if not order:
            return pd.DataFrame(), {"objective": float("inf"), "makespan": 0.0, "load_diff": 0.0}

        name_candidates = [c for c in features.columns if "套料图名" in c]
        seg_candidates = [c for c in features.columns if "分段号" in c]
        cut_candidates = [c for c in features.columns if "工时" in c and "min" in c]
        pri_candidates = [c for c in features.columns if "优先" in c or "最低" in c]
        if not name_candidates or not seg_candidates or not cut_candidates or not pri_candidates:
            raise ValueError("特征表缺少动态排程所需列")
        name_col = name_candidates[0]
        seg_col = seg_candidates[0]
        cut_col = cut_candidates[0]
        pri_col = pri_candidates[0]
        idx = features.set_index(name_col)

        machines = machine_names(cfg)
        initial_free = initial_state.get("free", {})
        initial_worktables = initial_state.get("worktables", {})
        initial_gun_free = initial_state.get("gun_free", {})
        head_free = {m: float(initial_gun_free.get(m, initial_free.get(m, 0.0))) for m in machines}
        table_free = {
            m: [
                float((initial_worktables.get(m) or [0.0, 0.0])[0]),
                float((initial_worktables.get(m) or [0.0, 0.0])[1]),
            ]
            for m in machines
        }
        rows: list[dict] = []
        for sequence, plate_name in enumerate(order, start=start_seq):
            if plate_name not in idx.index:
                continue
            row = idx.loc[plate_name]
            duration = float(row[cut_col])
            handling = float(row.get("工位处理时间(min)", 0.0) or 0.0)
            best: dict | None = None
            for machine in machines:
                groups = self._groups_for_plate(
                    plate_name,
                    machine,
                    cfg,
                    catalog_index,
                    part_stats,
                )
                for table_idx in (0, 1):
                    head_id = f"head:{machine}"
                    table_id = f"table:{machine}:{table_idx}"
                    candidate = max(
                        head_free[machine],
                        table_free[machine][table_idx],
                        float(schedule_floor),
                    )
                    for _ in range(8):
                        candidate = calendar.next_available(head_id, candidate, duration)
                        candidate = calendar.next_available(table_id, candidate, duration)
                        dependency_ready = max(
                            self._next_ready_for_groups(calendar, groups, candidate),
                            self._next_ready_for_groups(calendar, groups, candidate + duration),
                        )
                        if dependency_ready <= candidate + duration + 1e-9:
                            break
                        candidate = dependency_ready
                    candidate = calendar.next_available(head_id, candidate, duration)
                    candidate = calendar.next_available(table_id, candidate, duration)
                    end = candidate + duration
                    if best is None or (end, machine, table_idx) < (
                        best["end"],
                        best["machine"],
                        best["table_idx"],
                    ):
                        best = {
                            "machine": machine,
                            "table_idx": table_idx,
                            "start": candidate,
                            "end": end,
                            "handling": handling,
                        }
            if best is None:
                continue
            head_free[best["machine"]] = best["end"]
            table_free[best["machine"]][best["table_idx"]] = best["end"] + handling
            rows.append({
                "套料图名": plate_name,
                "分段号": str(row[seg_col]),
                "切割机": best["machine"],
                "切割序号": sequence,
                "切割开始(min)": best["start"],
                "切割完成(min)": best["end"],
                "切割工时(min)": duration,
                "工位开始(min)": best["start"],
                "工位完工(min)": best["end"] + handling,
                "工位处理时间(min)": handling,
                "工位": best["table_idx"],
                "最低优先级": int(row[pri_col]),
                "_fixed": True,
            })

        schedule = pd.DataFrame(rows)
        if schedule.empty:
            return schedule, {"objective": float("inf"), "makespan": 0.0, "load_diff": 0.0}
        makespan = float(schedule["切割完成(min)"].max())
        load = schedule.groupby("切割机")["切割工时(min)"].sum()
        load_diff = float(load.max() - load.min()) if len(load) > 1 else 0.0
        weighted_priority = 0.0
        for _, item in schedule.iterrows():
            weighted_priority += (11.0 - min(10.0, max(0.0, float(item["最低优先级"])))) * float(item["切割完成(min)"])
        load_penalty = float(load.std(ddof=0)) if len(load) > 1 else 0.0
        objective = makespan + 0.002 * weighted_priority + 0.05 * load_penalty
        return schedule, {
            "objective": objective,
            "makespan": makespan,
            "load_diff": load_diff,
        }

    @staticmethod
    def _changed_plate_names(before: pd.DataFrame, after: pd.DataFrame) -> set[str]:
        if before.empty or after.empty:
            return set()
        before_map = before.set_index("套料图名")
        after_map = after.set_index("套料图名")
        changed: set[str] = set()
        for plate_name in set(before_map.index) & set(after_map.index):
            old = before_map.loc[plate_name]
            new = after_map.loc[plate_name]
            if str(old.get("切割机")) != str(new.get("切割机")):
                changed.add(str(plate_name))
                continue
            if abs(float(old.get("切割开始(min)", 0.0)) - float(new.get("切割开始(min)", 0.0))) > 1.0:
                changed.add(str(plate_name))
        return changed

    def _choose_strategy(
        self,
        requested: str,
        remaining_names: list[str],
        changed: set[str],
        has_interrupt: bool,
        has_faults: bool,
        has_order_changes: bool,
        has_data_change: bool,
    ) -> tuple[str, str]:
        requested = (requested or "auto").strip()
        if requested in {"right_shift", "partial_reoptimize", "full_reoptimize"}:
            labels = {
                "right_shift": "保守右移：保持大体顺序，只避开故障区间",
                "partial_reoptimize": "局部重优化：只对受影响窗口交换/插入",
                "full_reoptimize": "全局重优化：对全部未完成钢板搜索",
            }
            return requested, labels[requested]
        if has_faults and not has_order_changes and not has_data_change:
            return "right_shift", "纯设备故障场景采用稳定修复：保持原顺序和原资源，只向后平移受影响任务"
        ratio = len(changed) / max(1, len(remaining_names))
        if has_interrupt or ratio > 0.50:
            return "full_reoptimize", f"受影响比例约 {ratio:.0%}，采用全局重优化"
        if ratio > 0.20:
            return "partial_reoptimize", f"受影响比例约 {ratio:.0%}，采用局部窗口重优化"
        return "right_shift", f"受影响比例约 {ratio:.0%}，采用保守右移修复"

    def _improve_order(
        self,
        initial_order: list[str],
        strategy: str,
        deadline: float,
        features: pd.DataFrame,
        cfg: ModelConfig,
        calendar: AvailabilityCalendar,
        catalog_index: dict[str, dict],
        part_stats: dict[str, dict],
        initial_state: dict,
        changed_names: set[str],
        schedule_floor: float,
    ) -> tuple[list[str], pd.DataFrame, dict, dict]:
        base_schedule, base_metrics = self._schedule_cut_order(
            initial_order,
            features,
            cfg,
            calendar,
            catalog_index,
            part_stats,
            initial_state,
            schedule_floor=schedule_floor,
        )
        if strategy == "right_shift" or len(initial_order) < 2 or time.perf_counter() >= deadline:
            return initial_order, base_schedule, base_metrics, {
                "iterations": 0,
                "accepted": 0,
                "strategy": strategy,
            }

        best_order = list(initial_order)
        best_schedule = base_schedule
        best_metrics = base_metrics
        current_order = list(initial_order)
        current_metrics = base_metrics
        rng = random.Random(20260910 + len(initial_order))
        if strategy == "partial_reoptimize":
            interesting = {i for i, name in enumerate(initial_order) if name in changed_names}
            number = max(8, int(math.ceil(len(initial_order) * 0.35)))
            start = max(0, min(interesting) - 3) if interesting else 0
            mutable = list(range(start, min(len(initial_order), start + number)))
        else:
            mutable = list(range(len(initial_order)))

        iterations = 0
        accepted = 0
        while time.perf_counter() < deadline and iterations < 240:
            self._raise_if_cancelled()
            iterations += 1
            candidate = list(current_order)
            if len(mutable) < 2:
                break
            a, b = rng.sample(mutable, 2)
            if rng.random() < 0.5:
                candidate[a], candidate[b] = candidate[b], candidate[a]
            else:
                item = candidate.pop(a)
                candidate.insert(b, item)
            _, metrics = self._schedule_cut_order(
                candidate,
                features,
                cfg,
                calendar,
                catalog_index,
                part_stats,
                initial_state,
                schedule_floor=schedule_floor,
            )
            if float(metrics.get("objective", float("inf"))) < float(current_metrics.get("objective", float("inf"))):
                current_order = candidate
                current_metrics = metrics
                accepted += 1
                if float(metrics.get("objective", float("inf"))) < float(best_metrics.get("objective", float("inf"))):
                    best_order = candidate
                    best_metrics = metrics
        best_schedule, best_metrics = self._schedule_cut_order(
            best_order,
            features,
            cfg,
            calendar,
            catalog_index,
            part_stats,
            initial_state,
            schedule_floor=schedule_floor,
        )
        return best_order, best_schedule, best_metrics, {
            "iterations": iterations,
            "accepted": accepted,
            "strategy": strategy,
        }

    def _shift_previous_schedule(
        self,
        previous_schedule: pd.DataFrame,
        remaining_names: list[str],
        features: pd.DataFrame,
        cfg: ModelConfig,
        calendar: AvailabilityCalendar,
        catalog_index: dict[str, dict],
        part_stats: dict[str, dict],
        initial_state: dict,
        schedule_floor: float,
    ) -> tuple[list[str], pd.DataFrame, dict]:
        """稳定修复：保留原顺序、原切割机和原胎架，只允许时间向后平移。"""
        if not remaining_names:
            return [], pd.DataFrame(), {"objective": 0.0, "makespan": 0.0, "load_diff": 0.0}
        name_col = next((c for c in features.columns if "套料图名" in c), "套料图名")
        cut_col = next((c for c in features.columns if "工时" in c and "min" in c), "切割工时(min)")
        seg_col = next((c for c in features.columns if "分段号" in c), "分段号")
        pri_col = next((c for c in features.columns if "优先" in c or "最低" in c), "最低优先级")
        idx = features.set_index(name_col)
        previous = previous_schedule.set_index("套料图名")
        machines = machine_names(cfg)
        head_free = {
            machine: float((initial_state.get("gun_free") or {}).get(machine, 0.0))
            for machine in machines
        }
        table_free = {
            machine: list((initial_state.get("worktables") or {}).get(machine) or [0.0, 0.0])
            for machine in machines
        }
        rows: list[dict] = []
        for sequence, plate_name in enumerate(remaining_names, start=1):
            if plate_name not in previous.index or plate_name not in idx.index:
                continue
            old = previous.loc[plate_name]
            feature = idx.loc[plate_name]
            machine = str(old.get("切割机", "N2"))
            if machine not in head_free:
                machine = machines[0]
            table_idx = int(old.get("工位", 0) or 0)
            table_idx = 1 if table_idx == 1 else 0
            duration = float(feature[cut_col])
            handling = float(feature.get("工位处理时间(min)", 0.0) or 0.0)
            groups = self._groups_for_plate(
                plate_name,
                machine,
                cfg,
                catalog_index,
                part_stats,
            )
            start = max(
                schedule_floor,
                float(old.get("切割开始(min)", schedule_floor) or schedule_floor),
                head_free[machine],
                float(table_free[machine][table_idx]),
            )
            for _ in range(8):
                start = calendar.next_available(f"head:{machine}", start, duration)
                start = calendar.next_available(f"table:{machine}:{table_idx}", start, duration)
                dependency_ready = max(
                    self._next_ready_for_groups(calendar, groups, start),
                    self._next_ready_for_groups(calendar, groups, start + duration),
                )
                if dependency_ready <= start + duration + 1e-9:
                    break
                start = dependency_ready
            end = start + duration
            head_free[machine] = end
            table_free[machine][table_idx] = end + handling
            rows.append({
                "套料图名": plate_name,
                "分段号": str(old.get("分段号", feature.get(seg_col, ""))),
                "切割机": machine,
                "切割序号": sequence,
                "切割开始(min)": start,
                "切割完成(min)": end,
                "切割工时(min)": duration,
                "工位开始(min)": start,
                "工位完工(min)": end + handling,
                "工位处理时间(min)": handling,
                "工位": table_idx,
                "最低优先级": int(feature.get(pri_col, old.get("最低优先级", 0)) or 0),
                "_fixed": True,
            })
        schedule = pd.DataFrame(rows)
        if schedule.empty:
            return [], schedule, {"objective": float("inf"), "makespan": 0.0, "load_diff": 0.0}
        makespan = float(schedule["切割完成(min)"].max())
        load = schedule.groupby("切割机")["切割工时(min)"].sum()
        load_diff = float(load.max() - load.min()) if len(load) > 1 else 0.0
        return remaining_names, schedule, {
            "objective": makespan,
            "makespan": makespan,
            "load_diff": load_diff,
        }

    def _polish_stable_schedule(
        self,
        initial_order: list[str],
        initial_schedule: pd.DataFrame,
        deadline: float,
        features: pd.DataFrame,
        cfg: ModelConfig,
        calendar: AvailabilityCalendar,
        catalog_index: dict[str, dict],
        part_stats: dict[str, dict],
        initial_state: dict,
        changed_names: set[str],
        schedule_floor: float,
        baseline_makespan_min: float,
    ) -> tuple[list[str], pd.DataFrame, dict, dict]:
        """在稳定修复解的基础上使用剩余时间搜索，但禁止把总时长做到基础方案以下。"""
        if initial_schedule.empty:
            return initial_order, initial_schedule, {"makespan": 0.0}, {
                "iterations": 0,
                "accepted": 0,
                "strategy": "right_shift",
            }
        if time.perf_counter() >= deadline:
            return initial_order, initial_schedule, {"makespan": float(initial_schedule["切割完成(min)"].max())}, {
                "iterations": 0,
                "accepted": 0,
                "strategy": "right_shift",
            }

        def objective(schedule: pd.DataFrame) -> float:
            makespan = float(schedule["切割完成(min)"].max())
            load = schedule.groupby("切割机")["切割工时(min)"].sum()
            load_std = float(load.std(ddof=0)) if len(load) > 1 else 0.0
            weighted_priority = float(
                (
                    (11.0 - schedule["最低优先级"].clip(lower=0, upper=10).astype(float))
                    * schedule["切割完成(min)"].astype(float)
                ).sum()
            )
            return makespan + 0.002 * weighted_priority + 0.05 * load_std

        mutable = [i for i, name in enumerate(initial_order) if name in changed_names]
        if len(mutable) < 4:
            mutable = list(range(min(len(initial_order), max(12, len(mutable) + 8))))
        else:
            expanded: set[int] = set()
            for index in mutable:
                for offset in (-3, -2, -1, 0, 1, 2, 3):
                    if 0 <= index + offset < len(initial_order):
                        expanded.add(index + offset)
            mutable = sorted(expanded)

        rng = random.Random(self.STATE_FORMAT_VERSION * 1000003 + len(initial_order))
        current_order = list(initial_order)
        current_schedule = initial_schedule
        current_obj = objective(current_schedule)
        best_order = list(current_order)
        best_schedule = current_schedule
        best_obj = current_obj
        iterations = 0
        accepted = 0
        while time.perf_counter() < deadline and iterations < 2000 and len(mutable) >= 2:
            self._raise_if_cancelled()
            iterations += 1
            candidate = list(current_order)
            a, b = rng.sample(mutable, 2)
            if rng.random() < 0.5:
                candidate[a], candidate[b] = candidate[b], candidate[a]
            else:
                item = candidate.pop(a)
                candidate.insert(b, item)
            candidate_schedule, _ = self._schedule_cut_order(
                candidate,
                features,
                cfg,
                calendar,
                catalog_index,
                part_stats,
                initial_state,
                schedule_floor=schedule_floor,
            )
            if candidate_schedule.empty:
                continue
            candidate_makespan = float(candidate_schedule["切割完成(min)"].max())
            if candidate_makespan < baseline_makespan_min - 1e-6:
                continue
            candidate_obj = objective(candidate_schedule)
            if candidate_obj < current_obj:
                current_order = candidate
                current_schedule = candidate_schedule
                current_obj = candidate_obj
                accepted += 1
                if candidate_obj < best_obj:
                    best_order = candidate
                    best_schedule = candidate_schedule
                    best_obj = candidate_obj
        return best_order, best_schedule, {
            "objective": best_obj,
            "makespan": float(best_schedule["切割完成(min)"].max()),
        }, {
            "iterations": iterations,
            "accepted": accepted,
            "strategy": "right_shift",
        }

    @staticmethod
    def _advance_initial_state(initial_state: dict, scheduled_row: pd.Series) -> dict:
        """把首板占用写入初始状态，后续模型只能在首板结束后安排剩余钢板。"""
        state = copy.deepcopy(initial_state or {})
        state.setdefault("free", {})
        state.setdefault("gun_free", {})
        state.setdefault("worktables", {})
        machine = str(scheduled_row["切割机"])
        table_idx = int(scheduled_row.get("工位", 0) or 0)
        end = float(scheduled_row["切割完成(min)"])
        handling = float(scheduled_row.get("工位处理时间(min)", 0.0) or 0.0)
        state["gun_free"][machine] = max(float(state["gun_free"].get(machine, 0.0)), end)
        tables = list(state["worktables"].get(machine) or [0.0, 0.0])
        tables[table_idx] = max(float(tables[table_idx]), end + handling)
        state["worktables"][machine] = tables
        state["free"][machine] = max(
            float(state["free"].get(machine, 0.0)),
            state["gun_free"][machine],
            min(tables),
        )
        return state

    def _seed_remaining_order(
        self,
        model_name: str,
        seed_order: list[str],
        features: pd.DataFrame,
        parts: pd.DataFrame,
        cfg: ModelConfig,
        pp: ProcessParams | None,
        speed_table: dict | None,
        time_budget_s: float,
    ) -> tuple[list[str], dict]:
        """用用户选定的现有优化模型，为首板之后的剩余钢板生成顺序。"""
        model_name = str(model_name or "stable")
        if model_name == "stable" or len(seed_order) < 2:
            return seed_order, {"model": "stable", "iterations": 0}
        name_col = next((c for c in features.columns if "套料图名" in c), "套料图名")
        subset_names = set(str(name) for name in seed_order)
        sub_features = features[features[name_col].astype(str).isin(subset_names)].copy()
        sub_parts = parts[parts["套料图名"].astype(str).isin(subset_names)].copy()
        if len(sub_features) < 2:
            return seed_order, {"model": model_name, "iterations": 0, "fallback": "too_few_plates"}

        budget = max(0.02, float(time_budget_s))
        try:
            if model_name == "sa_tabu":
                optimizer = SATabuOptimizer(
                    sub_features,
                    sub_parts,
                    cfg,
                    pp,
                    seed=cfg.random_seed + 31,
                )
                order, _metrics, stats = optimizer.optimize(
                    seed_order,
                    max_iterations=120,
                    time_limit_seconds=budget,
                    progress_callback=lambda *_: self._raise_if_cancelled(),
                )
                return [str(name) for name in order if str(name) in subset_names], {
                    "model": "sa_tabu",
                    "stats": stats,
                }
            if model_name == "ga_lns":
                local_cfg = copy.deepcopy(cfg)
                local_cfg.ga_generations = min(
                    int(getattr(local_cfg, "ga_generations", 10)),
                    max(1, int(budget / 0.8)),
                )
                optimizer = GeneticLNSOptimizer(
                    sub_features,
                    sub_parts,
                    local_cfg,
                    pp,
                    seed=cfg.random_seed + 47,
                    objective_type=getattr(cfg, "objective_type", "linear"),
                    parallel_workers=1,
                )
                order, _metrics, stats = optimizer.optimize(
                    seed_order,
                    max_iterations=180,
                    time_limit_seconds=budget,
                    progress_callback=lambda *_: self._raise_if_cancelled(),
                )
                return [str(name) for name in order if str(name) in subset_names], {
                    "model": "ga_lns",
                    "stats": stats,
                }
            if model_name == "dq_nsga2":
                if budget < 10.0:
                    optimizer = SATabuOptimizer(
                        sub_features,
                        sub_parts,
                        cfg,
                        pp,
                        seed=cfg.random_seed + 59,
                    )
                    order, _metrics, stats = optimizer.optimize(
                        seed_order,
                        max_iterations=120,
                        time_limit_seconds=budget,
                        progress_callback=lambda *_: self._raise_if_cancelled(),
                    )
                    return [str(name) for name in order if str(name) in subset_names], {
                        "model": "dq_nsga2",
                        "fallback": "time_budget_too_small_use_sa_tabu",
                        "stats": stats,
                    }
                local_cfg = copy.deepcopy(cfg)
                local_cfg.ga_generations = min(
                    int(getattr(local_cfg, "ga_generations", 10)),
                    max(1, int(budget / 6.0)),
                )
                optimizer = DqnNsga2Optimizer(
                    sub_features,
                    sub_parts,
                    local_cfg,
                    pp,
                    seed=cfg.random_seed + 59,
                    plates=None,
                    speed_table=speed_table,
                    parallel_workers=1,
                )
                capacity, _balanced, _front, stats = optimizer.optimize(
                    seed_order,
                    max_iterations=120,
                    time_limit_seconds=budget,
                    progress_callback=lambda *_: self._raise_if_cancelled(),
                )
                order = capacity.get("order") if isinstance(capacity, dict) else None
                if not order:
                    raise RuntimeError("DQN+NSGA-II 未返回可用顺序")
                return [str(name) for name in order if str(name) in subset_names], {
                    "model": "dq_nsga2",
                    "stats": stats,
                }
        except Exception as exc:
            return seed_order, {
                "model": model_name,
                "fallback": str(exc),
                "iterations": 0,
            }
        return seed_order, {"model": model_name, "fallback": "unknown_model"}

    # ── 订单变更与在制任务 ───────────────────────────────
    def _apply_order_changes(
        self,
        features: pd.DataFrame,
        parts: pd.DataFrame,
        previous_schedule: pd.DataFrame,
        changes: list[OrderChange],
        decision_time: float,
        in_process_policy: str,
    ) -> tuple[pd.DataFrame, pd.DataFrame, set[str], list[str]]:
        messages: list[str] = []
        if previous_schedule.empty:
            return features, parts, set(), messages
        name_col = next((c for c in previous_schedule.columns if "套料图名" in c), "套料图名")
        features = features.copy()
        parts = parts.copy()
        row_map = previous_schedule.set_index(name_col)
        removed: set[str] = set()
        feature_name_col = next((c for c in features.columns if "套料图名" in c), "套料图名")
        for change in changes:
            plate_name = change.plate_name
            if plate_name not in row_map.index:
                messages.append(f"订单变更忽略：钢板 {plate_name} 不在当前排程中")
                continue
            row = row_map.loc[plate_name]
            start = float(row.get("切割开始(min)", 0.0) or 0.0)
            end = float(row.get("切割完成(min)", start) or start)
            if change.action == "cancel":
                if end <= decision_time:
                    messages.append(f"钢板 {plate_name} 已完成切割，取消后按现场库存冻结，不回滚资源历史")
                    continue
                if start <= decision_time < end:
                    messages.append(f"钢板 {plate_name} 在制，取消动作由在制钢板策略决定")
                    if in_process_policy == "interrupt":
                        removed.add(plate_name)
                else:
                    removed.add(plate_name)
            elif change.action == "priority":
                if end <= decision_time or start <= decision_time < end:
                    messages.append(f"钢板 {plate_name} 已开始加工，优先级变更仅对未投料钢板生效")
                    continue
                feature_mask = features[feature_name_col].astype(str) == plate_name
                features.loc[feature_mask, "最低优先级"] = int(change.priority)
                parts.loc[parts["套料图名"].astype(str) == plate_name, "齐套优先级"] = int(change.priority)
                messages.append(f"钢板 {plate_name} 的齐套优先级改为 {change.priority}")
        if removed:
            features = features[~features[feature_name_col].astype(str).isin(removed)].copy()
            parts = parts[~parts["套料图名"].astype(str).isin(removed)].copy()
        return features, parts, removed, messages

    def _split_schedule_at_event(
        self,
        previous_schedule: pd.DataFrame,
        decision_time: float,
        in_process_policy: str,
        clear_time_min: float,
        catalog_index: dict[str, dict],
        calendar: AvailabilityCalendar,
    ) -> tuple[pd.DataFrame, list[str], dict, list[str]]:
        if previous_schedule.empty:
            return previous_schedule.copy(), [], {}, []
        frozen: list[dict] = []
        remaining: list[str] = []
        state = {
            "free": {},
            "gun_free": {},
            "worktables": {},
        }
        messages: list[str] = []
        schedule = previous_schedule.sort_values("切割开始(min)", kind="stable")
        for _, row in schedule.iterrows():
            plate_name = str(row["套料图名"])
            start = float(row.get("切割开始(min)", 0.0) or 0.0)
            end = float(row.get("切割完成(min)", start) or start)
            machine = str(row.get("切割机", "N2"))
            table_idx = int(row.get("工位", 0) or 0)
            item = row.to_dict()
            item["_fixed"] = True
            if end <= decision_time:
                frozen.append(item)
                continue
            if start <= decision_time < end:
                if in_process_policy == "interrupt":
                    release_time = decision_time + max(0.0, clear_time_min)
                    state["gun_free"][machine] = max(state["gun_free"].get(machine, 0.0), release_time)
                    tables = list(state["worktables"].get(machine, [0.0, 0.0]))
                    tables[table_idx] = max(tables[table_idx], release_time)
                    state["worktables"][machine] = tables
                    messages.append(f"钢板 {plate_name} 已中断并进入清台，胎架/切割头在 {minutes_to_clock(release_time)} 后释放")
                    continue

                # 继续加工完：保留钢板，但若故障落在本板剩余加工窗口，按修复后剩余工时恢复。
                elapsed = max(0.0, decision_time - start)
                remaining_duration = max(0.0, end - decision_time)
                head_id = f"head:{machine}"
                resume = calendar.next_available(head_id, decision_time, remaining_duration)
                new_end = resume + remaining_duration
                item["切割完成(min)"] = new_end
                item["切割工时(min)"] = max(float(item.get("切割工时(min)", end - start)), new_end - start)
                item["工位完工(min)"] = max(float(item.get("工位完工(min)", end)), new_end)
                messages.append(
                    f"钢板 {plate_name} 继续加工完，预计切割完成时间更新为 {minutes_to_clock(new_end)}"
                )
                frozen.append(item)
                continue
            remaining.append(plate_name)
        return pd.DataFrame(frozen), remaining, state, messages

    # ── 主流程 ───────────────────────────────────────────
    def reschedule(self, payload: dict, cancel_check=None) -> dict:
        self._cancel_check = cancel_check
        self._raise_if_cancelled()
        self.job_id = str(payload.get("_job_id") or f"sync_{uuid.uuid4().hex[:8]}")
        self.write_dir = self.store.staging_dir(self.job_id)
        total_start = time.perf_counter()
        previous_state = self._load_state()
        legacy_state = int(previous_state.get("state_format_version", 0) or 0) != self.STATE_FORMAT_VERSION
        upload_mode = str(payload.get("upload_mode") or "order_change")
        if upload_mode == "rush_insert" and self.override_input_path:
            plates, parts, checks, cfg, pp, speed_table = self._load_problem(
                previous_state,
                use_override=False,
            )
            insert_plates, insert_parts, _ = load_and_validate_fast(self.override_input_path)
            insert_name_col = next((c for c in insert_plates.columns if "套料图名" in c), "套料图名")
            insert_names = set(insert_plates[insert_name_col].astype(str))
            base_names = set(plates[next((c for c in plates.columns if "套料图名" in c), "套料图名")].astype(str))
            duplicate_names = sorted(insert_names & base_names)
            if duplicate_names:
                raise ValueError(
                    "紧急插单附件2中存在与原计划重名的钢板："
                    + "、".join(duplicate_names[:10])
                )
            plates = pd.concat([insert_plates, plates], ignore_index=True, sort=False)
            parts = pd.concat([insert_parts, parts], ignore_index=True, sort=False)
            is_rush_insert = True
        else:
            insert_names: set[str] = set()
            is_rush_insert = False
            plates, parts, checks, cfg, pp, speed_table = self._load_problem(previous_state)
        # 动态结果必须基于稳定的基础排程重建，完整故障清单才是唯一状态源。
        # 不能把上一版动态排程继续当作输入，否则同一故障会被重复叠加到在制钢板上。
        previous_schedule = self._load_previous_schedule(previous_state, use_dynamic=False)
        previous_version = 0 if legacy_state else int(previous_state.get("version", 0) or 0)
        catalog = self._catalog(cfg)
        catalog_index = self._catalog_index(catalog)

        raw_faults = payload.get("faults")
        if raw_faults is None:
            raw_faults = previous_state.get("faults", [])
        raw_changes = payload.get("order_changes")
        if raw_changes is None:
            raw_changes = previous_state.get("order_changes", [])

        previous_decision = float(previous_state.get("decision_time_min", 0.0) or 0.0)
        parsed_faults = self._parse_faults(raw_faults or [], previous_decision, previous_state, catalog_index)
        previous_faults = {
            str(item.get("id")): (
                float(item.get("start_min", 0.0) or 0.0),
                float(item.get("end_min", 0.0) or 0.0),
            )
            for item in previous_state.get("faults", [])
        }
        changed_fault_starts = [
            fault.start_min
            for fault in parsed_faults
            if previous_faults.get(fault.event_id) != (fault.start_min, fault.end_min)
        ]
        if changed_fault_starts:
            decision_time = min(changed_fault_starts)
        elif parsed_faults:
            decision_time = min(fault.start_min for fault in parsed_faults)
        else:
            decision_time = previous_decision
        faults = parsed_faults
        for fault in faults:
            if decision_time >= fault.end_min:
                fault.status = "repaired"
            elif fault.start_min <= decision_time < fault.end_min:
                fault.status = "active"
            else:
                fault.status = "scheduled"
        changes = self._parse_order_changes(raw_changes or [])
        calendar = self._calendar(faults, catalog_index)

        feature_name_col = next((c for c in parts.columns if c == "套料图名"), "套料图名")
        new_data_plates = set(plates[feature_name_col].astype(str))
        previous_plate_names = set(previous_schedule["套料图名"].astype(str))
        plate_name_col = next((c for c in plates.columns if "套料图名" in c), "套料图名")
        data_added = [
            str(name)
            for name in plates[plate_name_col].astype(str).tolist()
            if name not in previous_plate_names
        ]
        data_removed = sorted(previous_plate_names - new_data_plates)
        if data_removed:
            explicit_cancelled = {change.plate_name for change in changes if change.action == "cancel"}
            changes.extend(
                OrderChange(plate_name=name, action="cancel")
                for name in data_removed
                if name not in explicit_cancelled
            )

        order_change_time_min = None
        has_order_change = bool(
            payload.get("plate_file_id")
            or self.input_file_id
            or data_added
            or data_removed
        )
        if has_order_change and payload.get("order_change_time") not in (None, ""):
            order_change_time_min = safe_clock_to_minutes(str(payload.get("order_change_time")))
            decision_time = (
                min(decision_time, order_change_time_min)
                if faults
                else order_change_time_min
            )
            for fault in faults:
                if decision_time >= fault.end_min:
                    fault.status = "repaired"
                elif fault.start_min <= decision_time < fault.end_min:
                    fault.status = "active"
                else:
                    fault.status = "scheduled"

        in_process_policy = str(payload.get("in_process_policy") or "finish")
        if in_process_policy not in {"finish", "interrupt"}:
            in_process_policy = "finish"
        clear_time_min = max(0.0, safe_clock_to_minutes(
            payload.get("clear_time") or "00:00:00",
            0.0,
        ))

        # 订单变更先标记，随后对当前在制钢板应用同一套物理处置规则。
        features, parts, removed, change_messages = self._apply_order_changes(
            plate_features(plates, parts, cfg, speed_table),
            parts,
            previous_schedule,
            changes,
            decision_time,
            in_process_policy,
        )
        # 应用订单变更后，再按事件时刻拆分冻结/在制/未开始钢板。
        active_previous = previous_schedule[
            ~previous_schedule["套料图名"].astype(str).isin(removed)
        ].copy()
        frozen_df, remaining_names, initial_state, process_messages = self._split_schedule_at_event(
            active_previous,
            decision_time,
            in_process_policy,
            clear_time_min,
            catalog_index,
            calendar,
        )
        # 已取消但已经完成的钢板仍保留为库存；这里把它们从剩余集合排除即可。
        remaining_names = [name for name in remaining_names if name not in removed]
        existing_remaining = set(remaining_names)
        if upload_mode == "rush_insert":
            ordered_added = [name for name in data_added if name not in removed]
            remaining_names = ordered_added + [
                name for name in remaining_names if name not in set(ordered_added)
            ]
        else:
            for name in data_added:
                if name not in removed and name not in existing_remaining:
                    remaining_names.append(name)
                    existing_remaining.add(name)
        if not remaining_names and frozen_df.empty:
            raise ValueError("当前没有可排钢板")

        part_stats = self._part_stats(parts)
        # 先构造一版保持原有顺序的快速可行解。
        baseline_order = [str(name) for name in remaining_names]
        quick_schedule, quick_metrics = self._schedule_cut_order(
            baseline_order,
            features,
            cfg,
            calendar,
            catalog_index,
            part_stats,
            initial_state,
            start_seq=len(frozen_df) + 1,
            schedule_floor=decision_time,
        )
        if payload.get("preview_only"):
            preview_limit_s = max(0.05, float(payload.get("time_limit_s") or 5.0))
            preview_deadline = total_start + preview_limit_s * 0.95
            best_preview_order, best_preview_schedule, best_preview_metrics, _preview_stats = self._improve_order(
                baseline_order,
                "partial_reoptimize",
                preview_deadline,
                features,
                cfg,
                calendar,
                catalog_index,
                part_stats,
                initial_state,
                set(),
                decision_time,
            )
            quick_schedule = best_preview_schedule if not best_preview_schedule.empty else quick_schedule
            quick_metrics = best_preview_metrics
            if quick_schedule.empty:
                raise ValueError("首板快速排程失败")
            quick_ordered = quick_schedule.sort_values("切割开始(min)", kind="stable").reset_index(drop=True)
            first = quick_ordered.iloc[0]
            first_batch = quick_ordered.head(min(4, len(quick_ordered))).copy()
            partial_frames = [df for df in (frozen_df, first_batch) if not df.empty]
            partial_schedule = pd.concat(partial_frames, ignore_index=True) if partial_frames else first_batch.copy()
            partial_schedule = partial_schedule.sort_values("切割开始(min)", kind="stable").reset_index(drop=True)
            partial_names = set(partial_schedule["套料图名"].astype(str))
            partial_parts = parts[parts["套料图名"].astype(str).isin(partial_names)].copy()
            first_batch_end = float(first["切割完成(min)"])
            partial_complete = pd.DataFrame()
            partial_groups = pd.DataFrame()
            partial_metrics = {}
            partial_stages = pd.DataFrame()
            partial_buffer_ts: list = []
            # 首批预算只计算这 4 张钢板自身的完整工序链，不把其他钢板的排队等待计入。
            first_batch_end = float(
                (
                    first_batch["切割完成(min)"].astype(float)
                    + first_batch.get(
                        "工位处理时间(min)",
                        pd.Series(0.0, index=first_batch.index),
                    ).astype(float)
                ).max()
            )
            if not partial_parts.empty:
                try:
                    _, partial_complete, partial_groups, partial_metrics, partial_stages, partial_buffer_ts = build_joint_schedule(
                        partial_schedule,
                        partial_parts,
                        cfg,
                        pp,
                        outage_calendar=calendar,
                        initial_state=initial_state,
                        enforce_buffer_capacity=True,
                    )
                except Exception:
                    pass
            batch_start = float(first_batch["切割开始(min)"].min())
            first_batch_duration_s = max(0.0, first_batch_end - batch_start) * 60.0
            if not partial_schedule.empty:
                partial_schedule.to_csv(self.write_dir / "optimized_plate_schedule.csv", index=False, encoding="utf-8-sig")
                partial_schedule.to_csv(self.write_dir / "dynamic_partial_schedule.csv", index=False, encoding="utf-8-sig")
            if not partial_complete.empty:
                partial_complete.to_csv(self.write_dir / "part_completion.csv", index=False, encoding="utf-8-sig")
                partial_complete.to_csv(self.write_dir / "dynamic_partial_complete.csv", index=False, encoding="utf-8-sig")
            if not partial_groups.empty:
                partial_groups.to_csv(self.write_dir / "kit_groups.csv", index=False, encoding="utf-8-sig")
                partial_groups.to_csv(self.write_dir / "dynamic_partial_kit_groups.csv", index=False, encoding="utf-8-sig")
            if not partial_stages.empty:
                partial_stages.to_csv(self.write_dir / "process_stages.csv", index=False, encoding="utf-8-sig")
                partial_stages.to_csv(self.write_dir / "dynamic_partial_stages.csv", index=False, encoding="utf-8-sig")
            (self.write_dir / "buffer_timeseries.json").write_text(
                json.dumps(partial_buffer_ts, ensure_ascii=False),
                encoding="utf-8",
            )
            partial_gantt = []
            for _, row in partial_schedule.iterrows():
                partial_gantt.append({
                    "name": str(row["套料图名"]),
                    "machine": str(row["切割机"]),
                    "start": round(float(row["切割开始(min)"]) / 60.0, 4),
                    "end": round(float(row["切割完成(min)"]) / 60.0, 4),
                    "duration": round(float(row["切割工时(min)"]) / 60.0, 4),
                    "section": str(row.get("分段号", "")),
                    "priority": int(row.get("最低优先级", 0) or 0),
                    "table": int(row.get("工位", 0) or 0),
                    "tableStart": round(float(row.get("工位开始(min)", row["切割开始(min)"])) / 60.0, 4),
                    "tableEnd": round(float(row.get("工位完工(min)", row["切割完成(min)"])) / 60.0, 4),
                    "waitEnd": round(float(row.get("等待结束(min)", row["切割开始(min)"])) / 60.0, 4),
                })
            partial_stage_view = []
            if partial_stages is not None and not partial_stages.empty:
                for _, row in partial_stages.iterrows():
                    partial_stage_view.append({
                        "part": str(row.iloc[0]),
                        "stage": str(row.iloc[1]),
                        "resource": str(row.iloc[2]),
                        "start": round(float(row.iloc[3]) / 60.0, 4),
                        "end": round(float(row.iloc[4]) / 60.0, 4),
                    })
            partial_kit = []
            if partial_groups is not None and not partial_groups.empty:
                for _, row in partial_groups.iterrows():
                    partial_kit.append({
                        "label": f"{row['分段号']}-P{int(row['齐套优先级'])}",
                        "partCount": int(row["零件数"]),
                        "firstArrival": round(float(row["首件到齐套区"]) / 60.0, 4),
                        "completion": round(float(row["齐套完成"]) / 60.0, 4),
                        "span": round(float(row["齐套跨度(min)"]) / 60.0, 4),
                    })
            return {
                "run_id": self.run_dir.name,
                "phase": "first_plate_ready",
                "decision_time": minutes_to_clock(decision_time),
                "first_plate": {
                    "name": str(first["套料图名"]),
                    "machine": str(first["切割机"]),
                    "table": int(first.get("工位", 0) or 0),
                    "start": round(float(first["切割开始(min)"]) / 60.0, 4),
                    "end": round(float(first["切割完成(min)"]) / 60.0, 4),
                    "duration_min": round(float(first["切割工时(min)"]), 4),
                    "duration_s": round(float(first["切割工时(min)"]) * 60.0, 3),
                },
                "first_batch": {
                    "names": [str(name) for name in first_batch["套料图名"].tolist()],
                    "start": round(batch_start / 60.0, 4),
                    "end": round(first_batch_end / 60.0, 4),
                    "duration_s": round(first_batch_duration_s, 3),
                },
                "quickMakespanHours": round(float(quick_metrics.get("makespan", 0.0)) / 60.0, 2),
                "partialResult": {
                    "makespanHours": round(
                        max(
                            [first_batch_end]
                            + ([float(partial_stages["结束(min)"].max())] if not partial_stages.empty else [])
                        ) / 60.0,
                        2,
                    ),
                    "ganttData": partial_gantt,
                    "stagesData": partial_stage_view,
                    "kitSpanData": partial_kit,
                    "metrics": partial_metrics,
                },
            }
        changed = self._changed_plate_names(
            previous_schedule[previous_schedule["套料图名"].astype(str).isin(remaining_names)],
            quick_schedule,
        )
        strategy, strategy_reason = self._choose_strategy(
            str(payload.get("strategy_mode") or "auto"),
            remaining_names,
            changed,
            has_interrupt=(in_process_policy == "interrupt" and any(
                float(row.get("切割开始(min)", 0.0)) <= decision_time < float(row.get("切割完成(min)", 0.0))
                for _, row in previous_schedule.iterrows()
            )),
            has_faults=bool(faults),
            has_order_changes=bool(changes),
            has_data_change=bool(data_added or data_removed),
        )
        remaining_model = str(payload.get("remaining_model") or "stable").strip()
        model_labels = {
            "sa_tabu": "SA+Tabu",
            "ga_lns": "GA+LNS",
            "dq_nsga2": "DQN+NSGA-II",
        }
        if remaining_model in model_labels:
            strategy = remaining_model
            strategy_reason = (
                f"首板快速锁定后，使用 {model_labels[remaining_model]} "
                "在剩余时间预算内优化其余钢板"
            )

        time_limit_s = float(payload.get("time_limit_s") or 5.0)
        time_limit_s = max(0.25, min(60.0, time_limit_s))
        load_ms = (time.perf_counter() - total_start) * 1000.0
        overall_deadline = total_start + time_limit_s * 0.95
        previous_sim_ms = float(previous_state.get("last_simulation_ms", 0.0) or 0.0)
        sim_reserve_s = previous_sim_ms / 1000.0 if previous_sim_ms > 0 else min(0.5, time_limit_s * 0.25)
        sim_reserve_s = max(0.05, min(sim_reserve_s, time_limit_s * 0.45))
        search_deadline = max(time.perf_counter() + 0.01, overall_deadline - sim_reserve_s)
        if payload.get("first_plate_duration_s") is not None:
            # 第二阶段时间预算独立：使用首板加工时长，不再与最大响应时间取 min。
            search_deadline = time.perf_counter() + max(
                0.02,
                float(payload.get("first_plate_duration_s") or 0.0) * 0.95,
            )
        baseline_metrics_for_floor = self._baseline_metrics()
        baseline_makespan_min = float(
            baseline_metrics_for_floor.get("总完工时间(h)", 0.0) or 0.0
        ) * 60.0
        search_started = time.perf_counter()
        if remaining_model in model_labels and not quick_schedule.empty:
            quick_ordered = quick_schedule.sort_values("切割开始(min)", kind="stable").reset_index(drop=True)
            requested_batch = payload.get("first_batch_names") or []
            if requested_batch:
                requested_set = {str(name) for name in requested_batch}
                matched_batch = quick_ordered[
                    quick_ordered["套料图名"].astype(str).isin(requested_set)
                ].copy()
                first_batch = matched_batch if not matched_batch.empty else quick_ordered.head(1).copy()
            else:
                first_batch = quick_ordered.head(min(4, len(quick_ordered))).copy()
            first_row = first_batch.sort_values("切割开始(min)", kind="stable").iloc[0]
            first_name = str(first_row["套料图名"])
            first_batch_names = set(first_batch["套料图名"].astype(str))
            tail_order = [name for name in baseline_order if name not in first_batch_names]
            first_duration_s = float(first_row.get("切割工时(min)", 0.0) or 0.0) * 60.0
            model_budget_s = max(
                0.02,
                min(
                    max(0.0, search_deadline - time.perf_counter()),
                    first_duration_s if first_duration_s > 0 else search_deadline - time.perf_counter(),
                ),
            )
            tail_state = copy.deepcopy(initial_state or {})
            for _, batch_row in first_batch.sort_values("切割开始(min)", kind="stable").iterrows():
                tail_state = self._advance_initial_state(tail_state, batch_row)
            first_schedule = first_batch.copy()
            if is_rush_insert:
                insert_tail = [name for name in tail_order if name in insert_names]
                original_tail = [name for name in tail_order if name not in insert_names]
                insert_order, insert_stats = self._seed_remaining_order(
                    remaining_model,
                    insert_tail,
                    features,
                    parts,
                    cfg,
                    pp,
                    speed_table,
                    model_budget_s * 0.45,
                )
                insert_schedule, _ = self._schedule_cut_order(
                    insert_order,
                    features,
                    cfg,
                    calendar,
                    catalog_index,
                    part_stats,
                    tail_state,
                    schedule_floor=float(first_row["切割完成(min)"]),
                )
                insert_state = copy.deepcopy(tail_state)
                for _, insert_row in insert_schedule.sort_values("切割开始(min)", kind="stable").iterrows():
                    insert_state = self._advance_initial_state(insert_state, insert_row)
                original_budget = max(0.02, search_deadline - time.perf_counter())
                original_order, original_stats = self._seed_remaining_order(
                    remaining_model,
                    original_tail,
                    features,
                    parts,
                    cfg,
                    pp,
                    speed_table,
                    original_budget,
                )
                original_schedule, tail_metrics = self._schedule_cut_order(
                    original_order,
                    features,
                    cfg,
                    calendar,
                    catalog_index,
                    part_stats,
                    insert_state,
                    schedule_floor=(
                        float(insert_schedule["切割完成(min)"].max())
                        if not insert_schedule.empty
                        else float(first_row["切割完成(min)"])
                    ),
                )
                model_order = insert_order + original_order
                best_schedule = pd.concat(
                    [first_schedule, insert_schedule, original_schedule],
                    ignore_index=True,
                )
                model_stats = {
                    "model": remaining_model,
                    "insert_phase": insert_stats,
                    "original_phase": original_stats,
                }
            else:
                model_order, model_stats = self._seed_remaining_order(
                    remaining_model,
                    tail_order,
                    features,
                    parts,
                    cfg,
                    pp,
                    speed_table,
                    model_budget_s,
                )
                for _ in range(3):
                    self._raise_if_cancelled()
                    remaining_budget = search_deadline - time.perf_counter()
                    if remaining_budget <= 60.0:
                        break
                    refined_order, refined_stats = self._seed_remaining_order(
                        remaining_model,
                        model_order,
                        features,
                        parts,
                        cfg,
                        pp,
                        speed_table,
                        remaining_budget - 60.0,
                    )
                    if refined_order:
                        model_order = refined_order
                    model_stats = {
                        **model_stats,
                        "refine_passes": int(model_stats.get("refine_passes", 0)) + 1,
                        "last_refine": refined_stats,
                    }
                remaining_budget = search_deadline - time.perf_counter()
                if remaining_budget > 60.0:
                    sleep_end = time.perf_counter() + remaining_budget - 60.0
                    while time.perf_counter() < sleep_end:
                        self._raise_if_cancelled()
                        time.sleep(min(1.0, max(0.0, sleep_end - time.perf_counter())))
                tail_schedule, tail_metrics = self._schedule_cut_order(
                    model_order,
                    features,
                    cfg,
                    calendar,
                    catalog_index,
                    part_stats,
                    tail_state,
                    schedule_floor=float(first_row["切割完成(min)"]),
                )
                best_schedule = pd.concat([first_schedule, tail_schedule], ignore_index=True)
            best_order = [str(name) for name in first_batch["套料图名"].tolist()] + model_order
            best_metrics = tail_metrics
            search_stats = {
                **model_stats,
                "iterations": int(model_stats.get("iterations", 0) or 0),
                "first_plate": first_name,
                "first_batch": [str(name) for name in first_batch["套料图名"].tolist()],
                "first_plate_duration_s": round(first_duration_s, 2),
            }
        elif is_rush_insert:
            best_order = list(baseline_order)
            best_schedule, best_metrics = self._schedule_cut_order(
                best_order,
                features,
                cfg,
                calendar,
                catalog_index,
                part_stats,
                initial_state,
                schedule_floor=decision_time,
            )
            search_stats = {
                "model": "stable",
                "strategy": "rush_insert_ordered",
                "iterations": 0,
            }
        elif strategy == "right_shift":
            best_order, best_schedule, best_metrics = self._shift_previous_schedule(
                previous_schedule[previous_schedule["套料图名"].astype(str).isin(remaining_names)],
                baseline_order,
                features,
                cfg,
                calendar,
                catalog_index,
                part_stats,
                initial_state,
                decision_time,
            )
            best_order, best_schedule, best_metrics, search_stats = self._polish_stable_schedule(
                best_order,
                best_schedule,
                search_deadline,
                features,
                cfg,
                calendar,
                catalog_index,
                part_stats,
                initial_state,
                changed,
                decision_time,
                baseline_makespan_min,
            )
            remaining_budget = search_deadline - time.perf_counter()
            if remaining_budget > 60.0:
                # 首板阶段之后的时间窗独立使用，最多只空余最后 1 分钟。
                sleep_end = time.perf_counter() + remaining_budget - 60.0
                while time.perf_counter() < sleep_end:
                    self._raise_if_cancelled()
                    time.sleep(min(1.0, max(0.0, sleep_end - time.perf_counter())))
        else:
            best_order, best_schedule, best_metrics, search_stats = self._improve_order(
                baseline_order,
                strategy,
                search_deadline,
                features,
                cfg,
                calendar,
                catalog_index,
                part_stats,
                initial_state,
                changed,
                decision_time,
            )
        search_ended = time.perf_counter()
        greedy_ms = (search_started - total_start) * 1000.0 - load_ms
        search_ms = (search_ended - search_started) * 1000.0

        frames = [df for df in (frozen_df, best_schedule) if not df.empty]
        final_schedule = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if final_schedule.empty:
            raise ValueError("动态重排没有生成任何钢板")
        final_schedule = final_schedule.sort_values("切割开始(min)", kind="stable").reset_index(drop=True)
        final_schedule["切割序号"] = range(1, len(final_schedule) + 1)
        final_schedule["_fixed"] = True

        final_names = set(final_schedule["套料图名"].astype(str))
        parts_active = parts[parts["套料图名"].astype(str).isin(final_names)].copy()
        simulation_start = time.perf_counter()
        new_schedule, complete_df, groups_df, metrics, stages_df, buffer_ts = build_joint_schedule(
            final_schedule,
            parts_active,
            cfg,
            pp,
            outage_calendar=calendar,
            initial_state=initial_state,
            enforce_buffer_capacity=True,
        )
        simulation_ms = (time.perf_counter() - simulation_start) * 1000.0
        elapsed = time.perf_counter() - total_start

        stage_makespan_min = (
            float(stages_df["结束(min)"].max())
            if stages_df is not None and not stages_df.empty
            else float(new_schedule["切割完成(min)"].max())
        )
        metric_makespan_min = float(metrics.get("总完工时间(h)", 0.0) or 0.0) * 60.0
        consistent_makespan_min = max(stage_makespan_min, metric_makespan_min)
        metrics["总完工时间(h)"] = consistent_makespan_min / 60.0
        comparison_df = self._persist_canonical_outputs(
            new_schedule,
            complete_df,
            groups_df,
            metrics,
            stages_df,
            buffer_ts,
            checks,
            remaining_model,
        )

        # 持久化动态状态，下一次请求从当前动态排程和故障清单继续。
        new_schedule.to_csv(self.write_dir / self.SCHEDULE_FILE, index=False, encoding="utf-8-sig")
        if stages_df is not None and not stages_df.empty:
            stages_df.to_csv(self.write_dir / self.STAGES_FILE, index=False, encoding="utf-8-sig")
        self._write_json(self.write_dir / self.METRICS_FILE, metrics)
        next_version = previous_version + 1
        effective_input_path, effective_speed_path = self._effective_input_paths(previous_state)
        state = {
            "state_format_version": self.STATE_FORMAT_VERSION,
            "version": next_version,
            "decision_time_min": decision_time,
            "decision_time": minutes_to_clock(decision_time),
            "faults": [item.to_dict() for item in faults],
            "order_changes": [item.to_dict() for item in changes],
            "order_change_time": (
                minutes_to_clock(order_change_time_min)
                if order_change_time_min is not None
                else previous_state.get("order_change_time")
            ),
            "strategy_mode": strategy,
            "remaining_model": remaining_model,
            "upload_mode": upload_mode,
            "strategy_reason": strategy_reason,
            "input_path": to_submission_relative(effective_input_path),
            "speed_table_path": to_submission_relative(effective_speed_path) if effective_speed_path else None,
            "input_file_id": self.input_file_id or previous_state.get("input_file_id"),
            "speed_file_id": self.speed_file_id or previous_state.get("speed_file_id"),
            "input_filename": self.input_filename or previous_state.get("input_filename") or effective_input_path.name,
            "last_simulation_ms": round(simulation_ms, 3),
            "data_change_summary": {
                "added_plates": data_added,
                "removed_plates": data_removed,
                "common_plates": len(new_data_plates & previous_plate_names),
            },
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._write_json(self._write_state_path(), state)
        if not payload.get("preview_only"):
            self.store.commit(self.write_dir, self.job_id)

        makespan = consistent_makespan_min
        gantt_data = []
        for _, row in new_schedule.iterrows():
            gantt_data.append({
                "name": str(row["套料图名"]),
                "machine": str(row["切割机"]),
                "start": round(float(row["切割开始(min)"]) / 60.0, 4),
                "end": round(float(row["切割完成(min)"]) / 60.0, 4),
                "duration": round(float(row["切割工时(min)"]) / 60.0, 4),
                "section": str(row.get("分段号", "")),
                "priority": int(row.get("最低优先级", 0) or 0),
                "table": int(row.get("工位", 0) or 0),
                "tableStart": round(float(row.get("工位开始(min)", row["切割开始(min)"])) / 60.0, 4),
                "tableEnd": round(float(row.get("工位完工(min)", row["切割完成(min)"])) / 60.0, 4),
                "waitEnd": round(float(row.get("等待结束(min)", row["切割开始(min)"])) / 60.0, 4),
            })

        stages_data = []
        part_plate_map = dict(zip(parts_active.get("零件名", []), parts_active.get("套料图名", []))) if "零件名" in parts_active.columns else {}
        for _, row in new_schedule.iterrows():
            stages_data.append({
                "part": str(row["套料图名"]),
                "stage": "切割",
                "resource": str(row["切割机"]),
                "start": round(float(row["切割开始(min)"]) / 60.0, 4),
                "end": round(float(row["切割完成(min)"]) / 60.0, 4),
            })
        if stages_df is not None and not stages_df.empty:
            for _, row in stages_df.iterrows():
                part_name = str(row.iloc[0])
                plate_name = part_plate_map.get(part_name, part_name)
                stages_data.append({
                    "part": part_name,
                    "stage": str(row.iloc[1]),
                    "resource": str(row.iloc[2]),
                    "start": round(float(row.iloc[3]) / 60.0, 4),
                    "end": round(float(row.iloc[4]) / 60.0, 4),
                    "plate": plate_name,
                })

        util_data = []
        if stages_df is not None and not stages_df.empty:
            total_stage_end = float(stages_df["结束(min)"].max()) or 1.0
            for resource, total_minutes in stages_df.groupby("资源")["时长(min)"].sum().items():
                util_data.append({
                    "name": str(resource),
                    "totalHours": round(float(total_minutes) / 60.0, 2),
                    "utilization": round(float(total_minutes) / total_stage_end * 100.0, 2),
                })
        for machine, total_minutes in new_schedule.groupby("切割机")["切割工时(min)"].sum().items():
            util_data.append({
                "name": str(machine),
                "totalHours": round(float(total_minutes) / 60.0, 2),
                "utilization": round(float(total_minutes) / max(1.0, makespan) * 100.0, 2),
            })

        kit_data = []
        if groups_df is not None and not groups_df.empty:
            for _, row in groups_df.iterrows():
                kit_data.append({
                    "label": f"{row['分段号']}-P{int(row['齐套优先级'])}",
                    "partCount": int(row["零件数"]),
                    "firstArrival": round(float(row["首件到齐套区"]) / 60.0, 4),
                    "completion": round(float(row["齐套完成"]) / 60.0, 4),
                    "span": round(float(row["齐套跨度(min)"]) / 60.0, 4),
                })

        warnings = list(metrics.get("死锁详情", []) or [])
        warnings.extend(change_messages)
        warnings.extend(process_messages)
        baseline_metrics = self._baseline_metrics()
        baseline_makespan = float(
            baseline_metrics.get("总完工时间(h)", metrics.get("总完工时间(h)", 0.0)) or 0.0
        )
        dynamic_makespan = consistent_makespan_min / 60.0
        response = {
            "run_id": self.run_dir.name,
            "version": next_version,
            "decision_time": minutes_to_clock(decision_time),
            "strategy": strategy,
            "remainingModel": remaining_model,
            "uploadMode": upload_mode,
            "strategy_reason": strategy_reason,
            "reschedule_time_s": round(elapsed, 3),
            "breakdown_ms": {
                "state_load_ms": round(load_ms, 2),
                "greedy_repair_ms": round(max(0.0, greedy_ms), 2),
                "search_ms": round(max(0.0, search_ms), 2),
                "simulation_ms": round(simulation_ms, 2),
            },
            "frozen_plates": int(len(frozen_df)),
            "reoptimized_plates": int(len(best_order)),
            "cancelled_plates": int(len(removed)),
            "faults": [item.to_dict() for item in faults],
            "resource_catalog": catalog,
            "order_changes": [item.to_dict() for item in changes],
            "metrics": {"optimized": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in metrics.items()}},
            "comparisonData": json.loads(comparison_df.to_json(orient="records", force_ascii=False)),
            "baselineMetrics": baseline_metrics,
            "makespanDeltaHours": round(dynamic_makespan - baseline_makespan, 4),
            "makespanHours": round(makespan / 60.0, 2),
            "ganttData": gantt_data,
            "kitSpanData": kit_data,
            "stagesData": stages_data,
            "utilizationData": util_data,
            "bufferTimeseries": buffer_ts,
            "capacityWarnings": warnings,
            "searchStats": search_stats,
            "quickPlan": {
                "makespanHours": round(quick_metrics.get("makespan", 0.0) / 60.0, 2),
                "changedPlates": len(changed),
            },
            "dataChangeSummary": state["data_change_summary"],
            "inputFile": {
                "file_id": state.get("input_file_id"),
                "filename": state.get("input_filename"),
            },
            "files": {
                "schedule_csv": f"{self.run_dir.name}/optimized_plate_schedule.csv",
                "completion_csv": f"{self.run_dir.name}/part_completion.csv",
                "kit_csv": f"{self.run_dir.name}/kit_groups.csv",
                "stages_csv": f"{self.run_dir.name}/process_stages.csv",
                "comparison_csv": f"{self.run_dir.name}/comparison.csv",
                "gantt_png": f"{self.run_dir.name}/cutting_gantt.png",
                "kit_png": f"{self.run_dir.name}/kit_span.png",
                "util_png": f"{self.run_dir.name}/resource_utilisation.png",
            },
            "state": state,
        }
        return response
