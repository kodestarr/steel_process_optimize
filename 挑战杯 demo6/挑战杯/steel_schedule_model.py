"""齐套感知钢板排产：数据校验、离散事件仿真、启发式优化和结果导出。

所有未在当前附件中提供的工艺参数都收敛在 ModelConfig，便于用附件3或企业实测值替换。
"""
from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont


@dataclass
class ModelConfig:
    # 统一单位：分钟、毫米。以下为待附件3/现场数据替换的显式默认假设。
    cut_speed_mm_min: float = 1700.0
    v_cut_speed_mm_min: float = 800.0
    rapid_speed_mm_min: float = 24000.0   # 附件3/图2：24m/min
    marking_speed_mm_min: float = 24000.0  # 附件3/图2：24m/min
    pierce_minutes: float = 0.0  # 穿孔时间已折算进切割系数，不再单独计入
    plate_setup_minutes: float = 3.0     # 附件3：上料3min/张
    small_sort_minutes: float = 1.58     # 附件3：桁架切割→打磨台 95s（路径A）
    small_grind_speed_mm_min: float = 2520.0   # 附件3：42mm/s
    large_grind_speed_mm_min: float = 1950.0   # 附件3：1.95m/min
    auto_bevel_speed_mm_min: float = 1000.0
    manual_bevel_speed_mm_min: float = 250.0   # 附件3：250mm/min
    small_transfer_minutes: float = 5.0   # AGV 单次运输 min
    large_transfer_minutes: float = 5.0   # 附件3：下料 5min/张
    small_truss_direct_palletize_minutes: float = 1.0    # 附件3：桁架切割→码垛区 60s（路径B）
    small_truss_palletize_minutes: float = 1.17           # 附件3：桁架打磨台→码垛区 70s（路径C）
    small_grind_scan_minutes: float = 1.0                # 附件3：视觉扫描 1min/件
    auto_bevel_overhead_minutes: float = 3.5              # 附件3：扫描2min+预热0.5min+翻面1min
    n2_bevel_truss_minutes: float = round(70.0 / 60.0, 4)  # N2打磨后桁架到坡口工作站：70s/件
    small_sorters: int = 2      # 分拣双臂桁架（题目：2套）
    small_grinders: int = 2     # 小件自由边打磨设备：N2/N5各1台
    large_grinders: int = 2
    auto_bevel_machines: int = 1
    manual_bevel_stations: int = 1  # 人工坡口工位（题目：1个）
    agvs: int = 2                      # AGV数量（题目产线场景中有坡口件与非坡口件分别转运需求）
    cutting_machines: int = 2          # 切割机数量（题目规定：N2/N5双工位，备机N8可选）
    finish_buffer_capacity: int = 28   # N2/N5码垛区总容量（N2=10 + N5=18，题目规定）
    bevel_buffer_capacity: int = 3     # 坡口缓存区独立容量（题目规定：3）
    half_buffer_capacity: int = 14     # 缓存区容量：非坡口料框AGV到达后的暂存区
    kit_buffer_capacity: int = 16      # 小部材放置区容量：最终齐套料框存放区
    kit_dwell_minutes: float = 60.0    # 已齐套料框在齐套缓存区停留时间（下游取走间隔）
    bevel_workstation_capacity: int = 7  # 坡口工作站容量：坡口完成后的待合盘/待转运框数
    truss_travel_minutes: float = 0.5  # 桁架N2↔N5跨区移动时间
    cut_remainder_minutes: float = 0.0  # 双工位：残材/换料处理与另一工位切割并行，不再串行计入
    worktable_hoist_minutes: float = 25.0  # 桁架手动模式电动葫芦：单纯占用桁架和切割架
    crane_return_ratio: float = 1.0  # 空车返回时间 = 载运时间 × 1.0（与正向相同）
    crane_bevel_transfer_minutes: float = 3.0  # 切割胎架 -> 人工坡口加工区，载运3min
    crane_overlap_minutes: float = 3.0   # Loop 3: 天车与残材断料可重叠时间（减少纯空闲）
    use_component_formula: bool = True  # True=分项明细公式：直切+V坡+空行程+划线（穿孔已折算，不重复计入）
    local_search_iterations: int = 800
    random_seed: int = 20260723
    optimizer_method: str = "sa_tabu"  # sa_tabu=SA+Tabu, ga_lns=GA+LNS
    objective_type: str = "linear"     # linear=线性评价, quadratic=二次非线性评价
    eval_track: str = "capacity"       # capacity=产能优先(当前), balanced=兼顾三指标(原评价)
    ga_population_size: int = 16
    ga_generations: int = 10
    ga_crossover_rate: float = 0.85
    ga_mutation_rate: float = 0.20
    ga_tournament_size: int = 3
    lns_destroy_ratio: float = 0.25
    lns_iterations: int = 4
    # ── 产能优先目标参数（P4-1：产能作为第一优先级，辅助指标只做1%内微调）──
    capacity_eps: float = 0.009          # 产能差1%时，产能优的解必须赢
    capacity_tolerance_pct: float = 0.01 # 最终选解时产能差在此范围内才比较辅助指标
    secondary_weight_kit: float = 0.40   # 辅助指标：齐套跨度
    secondary_weight_load: float = 0.25  # 辅助指标：切割负载差
    secondary_weight_wait: float = 0.35  # 辅助指标：钢板等待时间
    theoretical_lb_h: float = 0.0        # 理论下界，由仿真/前置计算写入，0表示未计算

    # ── 目标函数权重（用户可调）──
    obj_weight_cmax: float = 0.40
    obj_weight_kit: float = 0.40
    obj_weight_load: float = 0.20
    # 胎架累计空闲时间软指标：只作为额外惩罚项，不改变原有三个指标的权重
    platen_idle_weight: float = 0.01

    # ── 天车需求/缓冲能力双轴分类阈值（方案1）──
    crane_large_threshold: float = 20.0
    crane_small_threshold: float = 40.0
    crane_cut_threshold: float = 90.0
    crane_large_min: float = 15.0
    crane_small_max: float = 40.0
    crane_trip_empty_nominal: float = 3.0  # 大件转运空驶当量 min/件
    crane_first_cut_quantile: float = 0.75  # 首板不得为长切割：切割工时超过该分位视为“长”

    # ── P3-3: 竞赛目标常量（全局统一引用，消除三处重复定义）──
    CMAX_TARGET_H: float = 67.25      # 竞赛目标：总完工时间 (h)
    KITSPAN_TARGET_H: float = 12.7    # 竞赛目标：齐套跨度 (h)
    LOADDIFF_TARGET_H: float = 1.0    # 竞赛目标：切割负载差 (h)

    # ── P1-3: 码垛区差异化容量（题目规定，统一单一定义）──
    PER_MACHINE_CAPS: dict = field(default_factory=lambda: {"N2": 10, "N5": 18})

    def adapt_to_data(self, plates: pd.DataFrame, parts: pd.DataFrame, pp=None) -> "ModelConfig":
        """根据数据集特征自适应调整资源配置，避免下游资源成为瓶颈。

        分析零件类型分布、坡口/打磨需求，自动扩展瓶颈资源数量。
        目标：确保切割机保持高利用率，不被下游阻塞。

        P1-3 改进：目标时间按数据集规模缩放，避免小数据集过度分配资源；
        增加资源分配上限，避免超大数据集分配不合理数量的工位。
        """
        import copy
        cfg = copy.deepcopy(self)
        # 题目实际只有1台自动坡口机，不允许自适应增加到多台
        cfg.auto_bevel_machines = 1

        # 统计零件特征
        n_small = int((parts["零件类型"] == "small").sum())
        n_large = int((parts["零件类型"] == "large").sum())
        total_bevel = float(parts["Y坡长度(mm)"].fillna(0).sum() +
                          parts["X坡长度(mm)"].fillna(0).sum() +
                          parts["K坡长度(mm)"].fillna(0).sum())
        total_grind = float(parts["打磨长度(mm)"].fillna(0).sum())
        n_plates = len(plates)

        # P1-3: 目标时间按数据集规模自适应缩放（data_0=109板为基准）
        # 小数据集用更短目标时间避免过度分配，大数据集适度放宽
        data_scale_factor = max(0.35, min(2.5, n_plates / 109.0))
        target_minutes = 67.25 * 60 * 0.75 * data_scale_factor

        # 人工坡口：大件坡口时间
        # P1-3: 上限6个工位，仅需求超过目标40%时额外增加
        if n_large > 0 and total_bevel > 0:
            large_bevel_mm = total_bevel * (n_large / max(n_small + n_large, 1))
            manual_bevel_min = large_bevel_mm / (pp.manual_bevel_speed_mm_min if pp else self.manual_bevel_speed_mm_min)
            need = int(manual_bevel_min / max(target_minutes, 1))
            cfg.manual_bevel_stations = min(6, max(1, need + (1 if manual_bevel_min > target_minutes * 0.4 else 0)))

        # 打磨资源：大件打磨时间
        # P1-3: 上限8台，保持默认2台起点不变，仅需求超过目标50%时增加
        if n_large > 0 and total_grind > 0:
            large_grind_mm = total_grind * (n_large / max(n_small + n_large, 1))
            lg_speed = pp.large_grind_speed_mm_min if pp else self.large_grind_speed_mm_min
            grind_min = large_grind_mm / lg_speed
            need = int(grind_min / max(target_minutes, 1))
            cfg.large_grinders = min(8, max(2, need + (1 if grind_min > target_minutes * 0.5 else 0)))

        # 天车重叠：若用户在前端显式设置了 crane_overlap_minutes，完全尊重用户选择
        # P0-1 FIX: _user_set_crane_overlap 由 backend/main.py 在应用用户参数后设置为 True
        if getattr(cfg, '_user_set_crane_overlap', False):
            pass  # 用户已在前端显式设置，尊重用户选择，不做任何覆盖

        return cfg


@dataclass
class ProcessParams:
    """附件3 工艺用时计算表提取的参数，替代 ModelConfig 中的硬编码速度。"""
    speed_table: dict  # {厚度(mm): {"straight": 直线速度, "vy_bevel": V&Y坡速度, "xk_bevel": X&K坡速度}}
    # 小件打磨：42mm/s = 2520 mm/min, 含机器扫描1min
    small_grind_speed_mm_min: float = 2520.0
    small_grind_scan_minutes: float = 1.0
    # 大件打磨：1.95m/min = 1950 mm/min
    large_grind_speed_mm_min: float = 1950.0
    # 大件人工坡口：250mm/min
    manual_bevel_speed_mm_min: float = 250.0
    # 小件自动坡口额外时间：扫描2min + 预热0.5min + 切割1min = 3.5min
    auto_bevel_overhead_minutes: float = 3.5
    # 自动坡口平均速度（从速度表取V&Y和X&K的平均，约2800mm/min）
    auto_bevel_speed_mm_min: float = 2800.0
    # 切割余料时间
    cut_remainder_minutes: float = 0.0
    # 转运：小件桁架切割→打磨95s + 打磨→码垛70s ≈ 2.75min
    small_transfer_minutes: float = 2.75
    large_transfer_minutes: float = 5.0  # 大件人工行车下料 5min


def load_process_params(path) -> ProcessParams:
    """从附件3 Excel 读取厚度相关速度表并提取工艺参数。"""
    df_speed = pd.read_excel(path, sheet_name=0)
    cols = list(df_speed.columns)

    # 使用 Unicode 转义匹配列名，避免平台编码问题
    def _find_col(keywords: list) -> str:
        for c in cols:
            for kw in keywords:
                if kw in c:
                    return c
        raise KeyError(f"找不到匹配列：{keywords}\n可用列：{cols}")

    col_thick = _find_col(["厚度"])               # 厚度
    col_straight = _find_col(["直切速度", "直线速度"])  # 直切速度/直线速度
    col_vy = _find_col(["V&Y", "V&Y坡切速度", "V&Y坡速度"])  # V&Y坡切速度
    col_xk = _find_col(["X&K", "X&K坡速度"])  # X&K坡速度
    speed_table = {}
    for _, r in df_speed.iterrows():
        t = float(r[col_thick])
        speed_table[t] = {
            "straight": float(r[col_straight]),
            "vy_bevel": float(r[col_vy]),
            "xk_bevel": float(r[col_xk]),
        }
    return ProcessParams(speed_table=speed_table)


def _get_speeds_for_thickness(thickness: float, speed_table: dict) -> dict:
    """根据钢板厚度查找对应的切割速度，支持精确匹配和最近邻插值。"""
    if not speed_table:
        return {"straight": 1700.0, "vy_bevel": 800.0, "xk_bevel": 400.0}
    if thickness in speed_table:
        return speed_table[thickness]
    # 最近邻查找
    keys = sorted(speed_table.keys())
    if thickness < keys[0]:
        return speed_table[keys[0]]
    if thickness > keys[-1]:
        return speed_table[keys[-1]]
    for i in range(len(keys) - 1):
        if keys[i] <= thickness <= keys[i + 1]:
            # 线性插值
            frac = (thickness - keys[i]) / (keys[i + 1] - keys[i])
            lo = speed_table[keys[i]]
            hi = speed_table[keys[i + 1]]
            return {
                "straight": lo["straight"] + (hi["straight"] - lo["straight"]) * frac,
                "vy_bevel": lo["vy_bevel"] + (hi["vy_bevel"] - lo["vy_bevel"]) * frac,
                "xk_bevel": lo["xk_bevel"] + (hi["xk_bevel"] - lo["xk_bevel"]) * frac,
            }
    return speed_table[keys[-1]]


class AGVPathNetwork:
    """AGV路径网络——建模AGV间的路径冲突与死锁检测。

    产线采用区域（Zone）模型，将车间划分为若干互斥区域：
      - Zone 0: N2切割区+码垛区
      - Zone 1: N5切割区+码垛区
      - Zone 2: 小件加工区（打磨+自动坡口）
      - Zone 3: 齐套缓存区

    每个Zone同时最多容纳1台AGV。AGV在Zone间移动需 reservation_time 分钟。
    当两个AGV试图进入同一Zone时，后到达的需等待。

    与简单的 ResourcePool 不同，PathNetwork 能够：
      1. 检测路径交叉死锁（AGV-A在Zone1等Zone2，AGV-B在Zone2等Zone1）
      2. 对冲突事件计数（用于KPI报告）
      3. 引入真实的AGV避让等待时间
    """

    def __init__(self, n_agvs: int = 2, zone_travel_time: float = 0.5, has_crane: bool = True):
        self.n_agvs = n_agvs
        self.zone_travel_time = zone_travel_time
        self.has_crane = has_crane
        self.start_zone = "切割胎架"

        # 每个AGV的状态: {agv_id: (free_time, current_zone)}
        self.agv_state: dict[int, tuple[float, str]] = {
            i: (0.0, self.start_zone) for i in range(n_agvs)
        }

        # 天车（1台）状态
        self.crane_free: float = 0.0
        self.crane_busy: float = 0.0  # 累计工作时间
        self.crane_current_zone: str = self.start_zone

        # 每个Zone的释放时间（同一时刻一个Zone只容一台AGV/天车）
        self.zone_free: dict[str, float] = {
            "N2区": 0.0, "N5区": 0.0, "加工区": 0.0, "齐套区": 0.0, "切割胎架": 0.0,
        }

        # 冲突统计
        self.conflict_count: int = 0
        self.total_wait_minutes: float = 0.0
        self.deadlock_events: list[str] = []
        self.crane_agv_conflicts: int = 0  # 天车与AGV的冲突次数

        # AGV累计工作时间
        self.agv_busy: dict[str, float] = {f"AGV{i+1}": 0.0 for i in range(n_agvs)}

    def _zone_for_location(self, machine: str) -> str:
        """根据切割机/工位确定所在Zone。"""
        if machine.startswith("N2"):
            return "N2区"
        elif machine.startswith("N5"):
            return "N5区"
        elif machine.startswith("N8"):
            return "N2区"  # N8备机靠近N2区
        else:
            return "加工区"  # 打磨/坡口/分拣

    def _need_cross_zone(self, from_zone: str, to_zone: str) -> bool:
        """判断是否需要跨Zone移动。"""
        return from_zone != to_zone

    def reserve(
        self,
        ready: float,
        duration: float,
        origin_machine: str,
        destination: str = "齐套区",
    ) -> tuple[str, float, float, dict]:
        """为一次AGV运输任务预留路径资源。

        Args:
            ready: 任务就绪时间
            duration: 运输持续时间
            origin_machine: 起点（切割机名/工位名）
            destination: 终点zone名

        Returns:
            (agv_label, start_time, end_time, conflict_info)
            conflict_info: {"waited": bool, "wait_minutes": float, "deadlock_risk": bool}
        """
        origin_zone = self._zone_for_location(origin_machine)
        dest_zone = destination if destination in self.zone_free else "齐套区"
        conflict_info = {"waited": False, "wait_minutes": 0.0, "deadlock_risk": False}

        # ── 选最佳AGV（负载均衡：最早空闲优先，但2min窗口内优先选累计工时少的）──
        candidates: list[tuple[float, int]] = []  # (start_time, agv_id)
        for agv_id, (free_time, current_zone) in self.agv_state.items():
            travel_penalty = self.zone_travel_time if self._need_cross_zone(current_zone, origin_zone) else 0.0
            departure = max(ready - travel_penalty, free_time)
            zone_free_time = self.zone_free.get(origin_zone, 0.0)
            departure = max(departure, zone_free_time)
            candidates.append((departure, agv_id))

        # 按就绪时间排序
        candidates.sort(key=lambda x: x[0])
        best_start = candidates[0][0]
        best_agv = candidates[0][1]

        # 负载均衡：在 best_start + 2min 窗口内，选累计工时最少的AGV
        fairness_window = 2.0
        for departure, agv_id in candidates[1:]:
            if departure <= best_start + fairness_window:
                agv_label_check = f"AGV{agv_id + 1}"
                best_label = f"AGV{best_agv + 1}"
                if self.agv_busy.get(agv_label_check, 0.0) < self.agv_busy.get(best_label, 0.0):
                    best_agv = agv_id
                    best_start = departure
            else:
                break

        # ── 死锁检测（仅当有 ≥2 台AGV时）──
        if self.n_agvs >= 2:
            # 检查是否有其他AGV占用目标Zone而导致潜在死锁
            for other_id, (other_free, other_zone) in self.agv_state.items():
                if other_id == best_agv:
                    continue
                if (other_zone == dest_zone and other_free > best_start
                        and self.zone_free.get(origin_zone, 0.0) > other_free):
                    conflict_info["deadlock_risk"] = True
                    self.deadlock_events.append(
                        f"t={best_start/60:.1f}h: AGV{best_agv+1}({origin_zone}->{dest_zone}) vs "
                        f"AGV{other_id+1}({other_zone}) — circular wait"
                    )
                    if len(self.deadlock_events) > 100:
                        self.deadlock_events = self.deadlock_events[-50:]
                    break

        # ── 路径冲突等待 ──
        # 先计算到达取料点的时刻，再判断目标Zone是否即将被占用
        _orig_zone = self.agv_state[best_agv][1]
        travel_penalty = self.zone_travel_time if self._need_cross_zone(_orig_zone, origin_zone) else 0.0
        loaded_start = max(best_start + travel_penalty, ready)
        dest_free = self.zone_free.get(dest_zone, 0.0)
        if dest_free > loaded_start + duration * 0.5:
            # 目标Zone在运输过程中会被占用，需延迟
            wait_time = dest_free - loaded_start
            if wait_time > 0.1:
                conflict_info["waited"] = True
                conflict_info["wait_minutes"] = wait_time
                loaded_start = dest_free
                self.conflict_count += 1
                self.total_wait_minutes += wait_time

        # ── 更新状态 ──
        end_time = loaded_start + duration
        agv_label = f"AGV{best_agv + 1}"

        # 保存原始Zone（更新agv_state前），用于计算跨区移动惩罚

        # 更新AGV状态
        self.agv_state[best_agv] = (end_time, dest_zone)
        self.agv_busy[agv_label] = self.agv_busy.get(agv_label, 0.0) + duration

        # 更新Zone占用（运输期间起点和终点Zone都被占用）
        self.zone_free[origin_zone] = max(self.zone_free[origin_zone], loaded_start + duration * 0.5)
        self.zone_free[dest_zone] = max(self.zone_free[dest_zone], end_time)

        return agv_label, best_start, end_time, conflict_info

    def reserve_crane(
        self, ready: float, duration: float, zone: str,
    ) -> tuple[float, float]:
        """预留天车资源（大件运输/上下料）。

        天车在指定Zone内工作，与AGV共享Zone容量。
        天车优先级高于AGV（AGV需等待天车释放Zone）。

        Returns:
            (start_time, end_time)
        """
        # 天车自身可用性
        travel_penalty = (
            self.zone_travel_time
            if self.crane_current_zone and self.crane_current_zone != safe_zone
            else 0.0
        )
        start = max(ready, self.crane_free + travel_penalty)

        # Zone可用性（与AGV竞争），未知Zone回退到加工区
        safe_zone = zone if zone in self.zone_free else "加工区"
        start = max(start, self.zone_free.get(safe_zone, 0.0))

        # 检测天车-AGV冲突
        for agv_id, (agv_free, agv_zone) in self.agv_state.items():
            if agv_zone == safe_zone and agv_free > start:
                self.crane_agv_conflicts += 1
                start = max(start, agv_free)

        end = start + duration
        self.crane_free = end
        self.crane_busy += duration
        self.crane_current_zone = safe_zone
        self.zone_free[safe_zone] = max(self.zone_free[safe_zone], end)

        return start, end

    def get_stats(self) -> dict:
        """返回AGV路径网络统计信息。"""
        stats = {
            "AGV路径冲突次数": self.conflict_count,
            "AGV冲突等待总时间(min)": round(self.total_wait_minutes, 2),
            "AGV死锁风险事件数": len(self.deadlock_events),
            "AGV死锁详情": self.deadlock_events[:10],
            **{f"{k}运输时间(min)": round(v, 2) for k, v in self.agv_busy.items()},
        }
        if self.has_crane:
            stats["天车工作时间(min)"] = round(self.crane_busy, 2)
            stats["天车AGV冲突次数"] = self.crane_agv_conflicts
        return stats


class TrussPool:
    """双臂桁架资源池——建模N2/N5共享桁架的分时冲突。

    每个桁架臂在N2和N5区域之间移动需要 travel_time 分钟。
    当臂上一次服务N2、本次要服务N5（或反之）时，准备时间增加 travel_time。
    """

    def __init__(self, n_arms: int = 2, travel_time: float = 0.5, name_prefix: str = "自动分拣"):
        self.n_arms = n_arms
        self.travel_time = travel_time
        self.name_prefix = name_prefix
        # (free_time, last_machine) for each arm
        self.arms: list[tuple[float, str]] = [(0.0, "") for _ in range(n_arms)]
        self.busy: dict[str, float] = {}  # arm_label → total busy time

    def reserve(self, ready: float, duration: float, machine: str) -> tuple[str, float, float]:
        """Reserve a truss arm, accounting for cross-machine travel time."""
        best_arm = 0
        best_start = float("inf")

        for i, (free_time, last_machine) in enumerate(self.arms):
            # Travel penalty if switching machines
            travel_penalty = self.travel_time if (last_machine and last_machine != machine) else 0.0
            effective_ready = max(ready - travel_penalty, free_time)
            if effective_ready < best_start:
                best_start = effective_ready
                best_arm = i

        arm_label = f"{self.name_prefix}{best_arm + 1}"
        travel_penalty = (
            self.travel_time
            if self.arms[best_arm][1] and self.arms[best_arm][1] != machine
            else 0.0
        )
        loaded_start = max(best_start + travel_penalty, ready)
        end = loaded_start + max(0.0, duration)
        self.arms[best_arm] = (end, machine)
        self.busy[arm_label] = self.busy.get(arm_label, 0.0) + max(0.0, duration)
        return arm_label, best_start, end


class ResourcePool:
    def __init__(self, names: Iterable[str]):
        self.free = {name: 0.0 for name in names}
        self.busy = {name: 0.0 for name in names}

    def reserve(self, ready: float, duration: float) -> Tuple[str, float, float]:
        name = min(self.free, key=self.free.get)
        start = max(ready, self.free[name])
        end = start + max(0.0, duration)
        self.free[name] = end
        self.busy[name] += max(0.0, duration)
        return name, start, end


class CranePool:
    """单台人工天车的占用排程器。

    天车同一时间只能执行一个任务；任务按 (start, end) 区间占用。
    reserve() 会从 ready 时刻开始找最早可插入的空档。
    """

    def __init__(self) -> None:
        self.intervals: list[tuple[float, float]] = []
        self.current_location: str = "胎架"
        self.last_empty_origin: str = ""
        self.last_empty_dest: str = ""
        self.last_loaded_origin: str = ""
        self.last_loaded_dest: str = ""

    @staticmethod
    def _loaded_travel_time(from_loc: str, to_loc: str) -> float:
        if from_loc == to_loc:
            return 0.0
        direct = {
            ("原料区", "胎架"): 3.0,
            ("胎架", "原料区"): 3.0,
            ("胎架", "坡口区"): 3.0,
            ("坡口区", "胎架"): 3.0,
            ("胎架", "成品区"): 5.0,
            ("成品区", "胎架"): 5.0,
            ("坡口区", "成品区"): 5.0,
            ("成品区", "坡口区"): 5.0,
        }
        if (from_loc, to_loc) in direct:
            return direct[(from_loc, to_loc)]
        # 胎架连接所有其他点；未定义点对按经胎架中转
        via_table = direct.get((from_loc, "胎架"), 0.0) + direct.get(("胎架", to_loc), 0.0)
        return via_table if via_table > 0 else 1.0

    def _empty_travel_time(self, from_loc: str, to_loc: str, cfg) -> float:
        ratio = max(0.0, float(getattr(cfg, "crane_return_ratio", 0.5)))
        return self._loaded_travel_time(from_loc, to_loc) * ratio

    def reserve_move(
        self,
        ready: float,
        origin: str,
        destination: str,
        loaded_time: float,
        cfg,
    ) -> tuple[float, float, float]:
        """载运任务：先空驶到 origin，再载运到 destination。

        Returns:
            (task_start, loaded_start, loaded_end)
        """
        empty_time = self._empty_travel_time(self.current_location, origin, cfg)
        total_duration = empty_time + max(0.0, loaded_time)
        start, end = self.reserve(max(0.0, ready - empty_time), total_duration)
        loaded_start = start + empty_time
        loaded_end = loaded_start + max(0.0, loaded_time)
        self.last_empty_origin = self.current_location
        self.last_empty_dest = origin
        self.last_loaded_origin = origin
        self.last_loaded_dest = destination
        self.current_location = destination
        return start, loaded_start, loaded_end

    def reserve(self, ready: float, duration: float) -> tuple[float, float]:
        if duration <= 0:
            return max(0.0, ready), max(0.0, ready)
        start = max(0.0, ready)
        for s, e in sorted(self.intervals):
            if start >= e:
                continue
            if start + duration <= s:
                break
            start = max(start, e)
        end = start + duration
        self.intervals.append((start, end))
        self.intervals.sort()
        return start, end

    def add_fixed(self, start: float, duration: float) -> tuple[float, float]:
        end = start + duration
        self.intervals.append((start, end))
        self.intervals.sort()
        return start, end

    @property
    def total_busy(self) -> float:
        return sum(e - s for s, e in self.intervals)


def machine_names(cfg: ModelConfig) -> list[str]:
    """Return cutting machine names: N2, N5, N8, ..."""
    if cfg.cutting_machines <= 2:
        return ["N2", "N5"][:cfg.cutting_machines]
    return ["N2", "N5"] + [f"N{i}" for i in range(8, 8 + cfg.cutting_machines - 2)]


def _crane_round_trip_duration(loaded_minutes: float, cfg: ModelConfig) -> float:
    ratio = max(0.0, float(getattr(cfg, "crane_return_ratio", 0.5)))
    return loaded_minutes * (1.0 + ratio)


def _reserve_raw_material_crane(
    crane_pool: CranePool,
    machine_ready: float,
    cfg: ModelConfig,
) -> tuple[float, float, float]:
    """原材料 -> 切割胎架：天车先按当前位置空驶到原料区，再载运到胎架。"""
    loaded = cfg.plate_setup_minutes
    start, loaded_start, loaded_end = crane_pool.reserve_move(
        max(0.0, machine_ready - loaded),
        "原料区",
        "胎架",
        loaded,
        cfg,
    )
    return start, loaded_start, loaded_end


# ── N5 切割机板厚物理约束（题目图2：N5 最大板厚 36mm）────────────────
N5_MAX_THICKNESS_MM = 36.0
_THICKNESS_COLS = ("厚度(mm)", "厚度", "板厚(mm)", "板厚")
_WIDTH_COLS = ("钢板宽度(mm)", "宽度(mm)", "板宽(mm)")
N5_MAX_WIDTH_MM = 4500.0


def _plate_thickness(plate, fallback: float = 6.0) -> float:
    """安全读取钢板厚度(mm)，兼容多种列名；NaN/缺失/非法一律回退 fallback，绝不抛异常。"""
    for col in _THICKNESS_COLS:
        val = None
        if hasattr(plate, "get"):
            val = plate.get(col)
        elif isinstance(plate, dict):
            val = plate.get(col)
        if val is None:
            continue
        try:
            v = float(val)
            if math.isfinite(v):
                return v
        except (TypeError, ValueError):
            continue
    return fallback


def _plate_width(plate):
    """安全读取钢板宽度(mm)，兼容多种列名；缺失/非法返回 None。"""
    for col in _WIDTH_COLS:
        val = None
        if hasattr(plate, "get"):
            val = plate.get(col)
        elif isinstance(plate, dict):
            val = plate.get(col)
        if val is None:
            continue
        try:
            v = float(val)
            if math.isfinite(v):
                return v
        except (TypeError, ValueError):
            continue
    return None


def pick_machine(
    plate,
    free: dict,
    n5_max_thickness: float = N5_MAX_THICKNESS_MM,
    n5_max_width_mm: float = N5_MAX_WIDTH_MM,
) -> str:
    """按 N5 板厚和板宽资格约束，在最早空闲机器中选机。

    - 厚度 > n5_max_thickness 的钢板剔除 N5（仅 N2 或其它大厚度备机）。
    - 宽度 > n5_max_width_mm 的钢板同样剔除 N5。
    - 候选为空时（理论防御，N2 恒存在）回退 N2，避免 min() 空序列 ValueError。
    """
    width = _plate_width(plate)
    eligible = [
        m for m in free
        if m != "N5"
        or (
            _plate_thickness(plate) <= n5_max_thickness
            and (width is None or width <= n5_max_width_mm)
        )
    ]
    if not eligible:
        return "N2" if "N2" in free else min(free, key=free.get)
    return min(eligible, key=free.get)

# Alias for backward compat
_machine_names = machine_names


def _first_existing(columns: Iterable[str], candidates: Iterable[str]) -> str:
    for c in candidates:
        if c in columns:
            return c
    raise KeyError(f"找不到必要字段：{list(candidates)}")


def load_and_validate(path: Path) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, object]]:
    # 容错处理：允许两种sheet名称格式
    try:
        plates = pd.read_excel(path, sheet_name="钢板数据")
    except ValueError:
        xl = pd.ExcelFile(path)
        plate_sheet = next((s for s in xl.sheet_names if '钢板' in s or 'plate' in s.lower()), xl.sheet_names[0])
        plates = pd.read_excel(path, sheet_name=plate_sheet)
    try:
        parts = pd.read_excel(path, sheet_name="零件数据")
    except ValueError:
        xl = pd.ExcelFile(path)
        part_sheet = next((s for s in xl.sheet_names if '零件' in s or 'part' in s.lower()), xl.sheet_names[-1])
        parts = pd.read_excel(path, sheet_name=part_sheet)

    required_plate = ["分段号", "套料图名", "零件总数", "穿孔数", "切割长度(mm)", "空行程长度(mm)", "划线长度(mm)", "V坡长度(mm)", "小件数量", "大件数量"]
    required_part = ["零件名", "套料图名", "零件类型", "切割长度(mm)", "V坡长度(mm)", "Y坡长度(mm)", "K坡长度(mm)", "I坡长度(mm)", "X坡长度(mm)", "齐套优先级", "打磨长度(mm)"]
    missing_cols = [c for c in required_plate if c not in plates.columns] + [c for c in required_part if c not in parts.columns]
    if missing_cols:
        raise ValueError(f"附件缺少字段：{missing_cols}")
    if plates["套料图名"].duplicated().any() or parts["零件名"].duplicated().any():
        raise ValueError("套料图名或零件名不应重复，请先处理主键。")
    unlinked = sorted(set(parts["套料图名"]) - set(plates["套料图名"]))
    if unlinked:
        raise ValueError(f"零件无法关联钢板：{unlinked[:5]}")

    plates = plates.copy()
    parts = parts.copy()
    parts["零件类型"] = parts["零件类型"].astype(str).str.strip().str.lower()
    type_map = {"小件": "small", "大件": "large", "small": "small", "large": "large"}
    parts["零件类型"] = parts["零件类型"].map(type_map).fillna(parts["零件类型"])
    bad_type = sorted(set(parts["零件类型"]) - {"small", "large"})
    if bad_type:
        raise ValueError(f"未知零件类型：{bad_type}")
    # 将钢板厚度合并到零件，用于后续自动坡口速度查表
    plate_cols = ["套料图名", "分段号"]
    if "厚度(mm)" in plates.columns:
        plate_cols.append("厚度(mm)")
    elif "板厚(mm)" in plates.columns:
        plate_cols.append("板厚(mm)")
    # P1-3 FIX: If parts already has "分段号", drop it to avoid merge _x/_y suffix collision
    if "分段号" in parts.columns:
        parts = parts.drop(columns=["分段号"])
    parts = parts.merge(plates[plate_cols], on="套料图名", how="left", validate="many_to_one")
    # 统一厚度列名
    if "板厚(mm)" in parts.columns and "厚度(mm)" not in parts.columns:
        parts["厚度(mm)"] = parts["板厚(mm)"]
    elif "厚度(mm)" not in parts.columns:
        parts["厚度(mm)"] = 6.0  # fallback
    # 人工/自动坡口统一按 Y+X+K 计算；V坡由切割机完成，I坡不计入坡口工序
    bevel_yxk = (
        parts["Y坡长度(mm)"].fillna(0)
        + parts["X坡长度(mm)"].fillna(0)
        + parts["K坡长度(mm)"].fillna(0)
    )
    parts["人工坡口长度(mm)"] = bevel_yxk
    parts["自动坡口长度(mm)"] = bevel_yxk
    # P3-4: 空打磨长度以 0 填充用于仿真，记录缺失零件清单供前端警告
    grind_missing_mask = parts["打磨长度(mm)"].isna()
    grind_missing_parts = parts.loc[grind_missing_mask, "零件名"].tolist() if grind_missing_mask.any() else []
    parts["打磨长度_模型(mm)"] = parts["打磨长度(mm)"].fillna(0)

    agg = parts.groupby("套料图名").agg(
        明细零件数=("零件名", "size"),
        明细小件数=("零件类型", lambda s: (s == "small").sum()),
        明细大件数=("零件类型", lambda s: (s == "large").sum()),
        明细V坡长度=("V坡长度(mm)", "sum"),
    ).reset_index()
    check = plates.merge(agg, on="套料图名", how="left")
    checks = {
        "钢板数": int(len(plates)), "零件数": int(len(parts)),
        "未匹配零件数": int(parts["分段号"].isna().sum()),
        "零件数不一致钢板数": int((check["零件总数"] != check["明细零件数"]).sum()),
        "小件数不一致钢板数": int((check["小件数量"] != check["明细小件数"]).sum()),
        "大件数不一致钢板数": int((check["大件数量"] != check["明细大件数"]).sum()),
        "V坡长度不一致钢板数": int((check["V坡长度(mm)"] - check["明细V坡长度"]).abs().gt(0.01).sum()),
        "打磨长度缺失零件数": int(parts["打磨长度(mm)"].isna().sum()),
        "打磨长度缺失零件清单": grind_missing_parts[:20],  # P3-4: 前20个缺失零件供前端Toast提示
        "齐套组数(分段+优先级)": int(parts.groupby(["分段号", "齐套优先级"]).ngroups),
    }
    return plates, parts, checks


def cut_duration(plate: pd.Series, cfg: ModelConfig, speed_table: dict | None = None) -> float:
    """计算单张钢板切割工时。

    分项明细公式（仅计算切割枪占用时间）：
    T_head = (L_cut/v_cut + L_V/v_V) / 0.8 / 0.6
             + L_empty/v_rapid + L_mark/v_mark

    其中 v_rapid = v_mark = 24000 mm/min（空行程和划线均为 24m/min）。
    双工位切割机下，残材断料/换料与另一工位切割并行，因此默认不再串行加 25min。
    穿孔时间已折算进切割速度系数，不再单独计入。
    """
    import warnings as _warnings

    thickness = float(plate.get("厚度(mm)", plate.get("板厚(mm)", 6.0)))
    sp = _get_speeds_for_thickness(thickness, speed_table) if speed_table else None
    straight_speed = sp["straight"] if sp else cfg.cut_speed_mm_min
    vy_speed = sp["vy_bevel"] if sp else cfg.v_cut_speed_mm_min

    # ── P0-1: 零速除零保护 — 所有分母非零守卫 ──
    _EPS_SPEED = 1e-6
    if straight_speed <= 0:
        _warnings.warn(f"直切速度={straight_speed}<=0, 已自动替换为安全下限 {_EPS_SPEED}")
        straight_speed = _EPS_SPEED
    if vy_speed <= 0:
        _warnings.warn(f"V坡速度={vy_speed}<=0, 已自动替换为安全下限 {_EPS_SPEED}")
        vy_speed = _EPS_SPEED
    if cfg.rapid_speed_mm_min <= 0:
        _warnings.warn(f"空行程速度={cfg.rapid_speed_mm_min}<=0, 已自动替换为安全下限 {_EPS_SPEED}")
        cfg.rapid_speed_mm_min = float(_EPS_SPEED)
    if cfg.marking_speed_mm_min <= 0:
        _warnings.warn(f"划线速度={cfg.marking_speed_mm_min}<=0, 已自动替换为安全下限 {_EPS_SPEED}")
        cfg.marking_speed_mm_min = float(_EPS_SPEED)

    cut_len = float(plate["切割长度(mm)"])
    v_len = float(plate["V坡长度(mm)"])

    # ── P0-2: NaN 检测与安全填充 ──
    _nan_fields = []
    if np.isnan(cut_len):
        _nan_fields.append("切割长度(mm)")
        cut_len = 0.0
    if np.isnan(v_len):
        _nan_fields.append("V坡长度(mm)")
        v_len = 0.0
    if _nan_fields:
        _warnings.warn(f"钢板 {plate.get('套料图名', '?')} 存在 NaN 字段: {_nan_fields}, 已按0处理")

    if getattr(cfg, 'use_component_formula', True):
        # 分项明细公式：空行程和划线均按 24m/min 显式计算
        # P0-1 FIX: NaN is truthy, so "NaN or 0" short-circuits to NaN. Must guard BEFORE or-pattern.
        _raw_empty = plate.get("空行程长度(mm)", 0)
        _raw_mark = plate.get("划线长度(mm)", 0)

        empty_len = float(_raw_empty) if pd.notna(_raw_empty) and _raw_empty else 0.0
        mark_len = float(_raw_mark) if pd.notna(_raw_mark) and _raw_mark else 0.0

        t_cut = cut_len / straight_speed if cut_len > 0 else 0.0
        t_v = v_len / vy_speed if v_len > 0 else 0.0
        t_empty = empty_len / cfg.rapid_speed_mm_min if empty_len > 0 else 0.0
        t_mark = mark_len / cfg.marking_speed_mm_min if mark_len > 0 else 0.0

        return (t_cut + t_v) / 0.8 / 0.6 + t_empty + t_mark + cfg.cut_remainder_minutes

    # 附件3官方公式：空行程/穿孔/易损件打包为 0.8、0.6 系数
    main = cut_len / straight_speed + (v_len / vy_speed if v_len > 0 else 0.0)
    return main / 0.8 / 0.6 + cfg.cut_remainder_minutes


def plate_features(plates: pd.DataFrame, parts: pd.DataFrame, cfg: ModelConfig, speed_table: dict | None = None) -> pd.DataFrame:
    p = plates.copy()
    p["切割工时(min)"] = p.apply(lambda r: cut_duration(r, cfg, speed_table), axis=1)
    group_totals = parts.groupby(["分段号", "齐套优先级"]).size().rename("组零件数").reset_index()
    membership = parts.merge(group_totals, on=["分段号", "齐套优先级"], how="left")
    plate_group = membership.groupby("套料图名").agg(
        覆盖齐套组数=("齐套优先级", "nunique"),
        齐套权重=("组零件数", lambda s: float((1 / s).sum())),
        最低优先级=("齐套优先级", "min"),
    ).reset_index()
    p = p.merge(plate_group, on="套料图名", how="left")

    # 双工位：切割完成后，工位还要处理钢板吊运、小件桁架第一次搬运或大件自由边打磨、桁架电动葫芦。
    small = parts[parts["零件类型"] == "small"].copy()
    small["_first_truss"] = np.where(
        small["打磨长度_模型(mm)"].fillna(0) > 0,
        cfg.small_sort_minutes,
        cfg.small_truss_direct_palletize_minutes,
    )
    small_truss = small.groupby("套料图名")["_first_truss"].sum().rename("小件桁架搬运时间(min)")

    large = parts[parts["零件类型"] == "large"].copy()
    large["_grind"] = large["打磨长度_模型(mm)"].fillna(0) / cfg.large_grind_speed_mm_min
    large_grind = large.groupby("套料图名")["_grind"].sum().rename("大件自由边打磨时间(min)")

    p = p.merge(small_truss, on="套料图名", how="left")
    p = p.merge(large_grind, on="套料图名", how="left")
    p["小件桁架搬运时间(min)"] = p["小件桁架搬运时间(min)"].fillna(0.0)
    p["大件自由边打磨时间(min)"] = p["大件自由边打磨时间(min)"].fillna(0.0)
    p["大件转运时间(min)"] = np.where(
        p.get("大件数量", 0).fillna(0) > 0,
        cfg.large_transfer_minutes,
        0.0,
    )
    p["工位处理时间(min)"] = (
        p[["小件桁架搬运时间(min)", "大件自由边打磨时间(min)"]].max(axis=1)
        + p["大件转运时间(min)"]
    )

    # ── 天车需求/缓冲能力双轴分类（方案1）──
    small_ops: Dict[str, float] = {}
    sg_speed = cfg.small_grind_speed_mm_min
    scan_min = cfg.small_grind_scan_minutes
    palletize_min = cfg.small_truss_palletize_minutes
    direct_min = cfg.small_truss_direct_palletize_minutes
    bevel_truss_min = cfg.n2_bevel_truss_minutes
    for _, pr in small.iterrows():
        plate_key = str(pr["套料图名"])
        need_grind = float(pr.get("打磨长度_模型(mm)", 0) or 0) > 0
        val = cfg.small_sort_minutes if need_grind else direct_min
        if need_grind:
            val += float(pr.get("打磨长度_模型(mm)", 0) or 0) * 2.0 / sg_speed + scan_min
        if float(pr.get("自动坡口长度(mm)", 0) or 0) > 0:
            val += bevel_truss_min
        else:
            val += palletize_min
        small_ops[plate_key] = small_ops.get(plate_key, 0.0) + val
    large_cnt = {str(k): int(v) for k, v in large.groupby("套料图名").size().items()}
    p["大件数量_分类"] = p["套料图名"].astype(str).map(large_cnt).fillna(0)
    p["小件处理时间(min)"] = p["套料图名"].astype(str).map(small_ops).fillna(0.0)
    trip_nominal = float(cfg.large_transfer_minutes) + float(cfg.crane_trip_empty_nominal)
    p["大件转运当量(min)"] = p["大件数量_分类"] * trip_nominal
    p["天车需求分(min)"] = p["大件自由边打磨时间(min)"] + p["大件转运当量(min)"]
    p["缓冲能力分(min)"] = p["切割工时(min)"] + p["小件处理时间(min)"] - 0.3 * p["天车需求分(min)"]

    def _classify_plate(r) -> str:
        L = float(r["天车需求分(min)"])
        S = float(r["小件处理时间(min)"])
        C = float(r["切割工时(min)"])
        if L >= cfg.crane_large_threshold and L > S:
            return "大件主导"
        if S >= cfg.crane_small_threshold and S > L:
            return "小件主导"
        if C >= cfg.crane_cut_threshold and L < cfg.crane_large_min and S < cfg.crane_small_max:
            return "切割主导"
        return "均衡"

    p["钢板分类"] = p.apply(_classify_plate, axis=1)
    return p


def build_crane_aware_orders(features: pd.DataFrame, cfg: ModelConfig | None = None) -> Dict[str, list[str]]:
    """基于天车需求/缓冲能力双轴分类生成候选调度池顺序。"""
    if cfg is None:
        cfg = ModelConfig()
    name_col = next((c for c in features.columns if "套料图名" in c or "plate" in c.lower()), None)
    seg_col = next((c for c in features.columns if "分段号" in c or "segment" in c.lower()), None)
    if name_col is None or not {"天车需求分(min)", "缓冲能力分(min)", "钢板分类"}.issubset(features.columns):
        return {}

    df = features.copy()
    heavy = df[df["钢板分类"] == "大件主导"].sort_values("天车需求分(min)", ascending=False)[name_col].tolist()
    buffer = df[df["钢板分类"].isin(["小件主导", "切割主导"])].sort_values("缓冲能力分(min)", ascending=False)[name_col].tolist()
    balanced = df[df["钢板分类"] == "均衡"].sort_values("缓冲能力分(min)", ascending=False)[name_col].tolist()
    orders: Dict[str, list[str]] = {}

    # CraneAlt：大件主导板与缓冲板全局交替
    alt: list[str] = []
    hi = bi = 0
    while hi < len(heavy) or bi < len(buffer):
        if hi < len(heavy):
            alt.append(heavy[hi])
            hi += 1
        if bi < len(buffer):
            alt.append(buffer[bi])
            bi += 1
    alt.extend(balanced)
    orders["CraneAlt"] = alt

    # CraneBufferBeforeHeavy：每块大件主导板前放一块缓冲板
    buf_before: list[str] = []
    used_b = 0
    for h in heavy:
        if used_b < len(buffer):
            buf_before.append(buffer[used_b])
            used_b += 1
        buf_before.append(h)
    buf_before.extend(buffer[used_b:])
    buf_before.extend(balanced)
    orders["CraneBufferBeforeHeavy"] = buf_before

    # CraneAltSeg：分段内交替，保持齐套分组
    if seg_col is not None:
        pri_col = next((c for c in features.columns if "最低优先级" in c or "优先" in c), None)
        df2 = df.copy()
        if pri_col is not None:
            seg_pri = df2.groupby(seg_col)[pri_col].min().to_dict()
            segs = sorted(df2[seg_col].astype(str).unique(), key=lambda s: (seg_pri.get(s, 999), s))
        else:
            segs = sorted(df2[seg_col].astype(str).unique())
        seg_order: list[str] = []
        for seg in segs:
            g = df2[df2[seg_col].astype(str) == seg]
            hg = g[g["钢板分类"] == "大件主导"].sort_values("天车需求分(min)", ascending=False)[name_col].tolist()
            bg = g[g["钢板分类"].isin(["小件主导", "切割主导"])].sort_values("缓冲能力分(min)", ascending=False)[name_col].tolist()
            balg = g[g["钢板分类"] == "均衡"].sort_values("缓冲能力分(min)", ascending=False)[name_col].tolist()
            hi = bi = 0
            while hi < len(hg) or bi < len(bg):
                if hi < len(hg):
                    seg_order.append(hg[hi])
                    hi += 1
                if bi < len(bg):
                    seg_order.append(bg[bi])
                    bi += 1
            seg_order.extend(balg)
        orders["CraneAltSeg"] = seg_order

    for _k in list(orders):
        orders[_k] = enforce_first_plate_not_long(orders[_k], features, cfg)
    return orders


def enforce_first_plate_not_long(
    order: list[str],
    features: pd.DataFrame,
    cfg: ModelConfig | None = None,
) -> list[str]:
    """修复候选顺序首板：第一件不能是长切割钢板。

    避免天车完成最初的几张上料后，因为首块长切割钢板仍在切而长时间停歇。
    """
    if not order:
        return order
    if cfg is None:
        cfg = ModelConfig()
    name_col = next((c for c in features.columns if "套料图名" in c or "plate" in c.lower()), None)
    if name_col is None or "切割工时(min)" not in features.columns:
        return order
    cut_map = dict(zip(features[name_col].astype(str), features["切割工时(min)"].astype(float)))
    limit = float(features["切割工时(min)"].quantile(getattr(cfg, "crane_first_cut_quantile", 0.75)))
    if float(cut_map.get(str(order[0]), 0.0)) <= limit:
        return order
    order = list(order)
    for i in range(1, len(order)):
        if float(cut_map.get(str(order[i]), 0.0)) <= limit:
            order[0], order[i] = order[i], order[0]
            break
    return order


def make_greedy_schedule(
    features: pd.DataFrame,
    strategy: str,
    seed: int,
    cfg: ModelConfig | None = None,
    parts: pd.DataFrame | None = None,
    pp: ProcessParams | None = None,
) -> pd.DataFrame:
    if cfg is None:
        cfg = ModelConfig()
    rng = random.Random(seed)
    p = features.copy()
    if strategy == "fifo":
        ordered = p.sort_values("序号", kind="stable")
    elif strategy == "completeness":
        # 优先释放稀缺齐套组，同时用短加工时间避免切割机出现明显空闲。
        ordered = p.assign(_noise=[rng.random() * 1e-6 for _ in range(len(p))]).sort_values(
            ["最低优先级", "齐套权重", "覆盖齐套组数", "切割工时(min)", "_noise"],
            ascending=[True, False, False, True, True], kind="stable")
    else:
        raise ValueError(strategy)
    _name_candidates = [c for c in p.columns if "套料图名" in c or "plate" in c.lower()]
    if _name_candidates:
        _nc = _name_candidates[0]
        _fixed_names = enforce_first_plate_not_long(ordered[_nc].tolist(), p, cfg)
        ordered = ordered.set_index(_nc).loc[_fixed_names].reset_index()
    machines = machine_names(cfg)
    free = {m: 0.0 for m in machines}
    worktables = {m: [0.0, 0.0] for m in machines}
    gun_free = {m: 0.0 for m in machines}
    last_table = {m: 1 for m in machines}
    crane_pool = CranePool()
    rows = []
    for sequence, (_, r) in enumerate(ordered.iterrows(), start=1):
        machine = pick_machine(r, free)
        raw_start, raw_empty_end, raw_loaded_end = _reserve_raw_material_crane(
            crane_pool, min(worktables[machine]), cfg,
        )
        # 双工位切割机：切割枪只占切割时间，工位需等吊运/桁架/打磨处理完成
        if worktables[machine][0] <= raw_loaded_end and worktables[machine][1] <= raw_loaded_end:
            table_idx = 1 - last_table[machine]
        elif worktables[machine][0] <= raw_loaded_end:
            table_idx = 0
        elif worktables[machine][1] <= raw_loaded_end:
            table_idx = 1
        else:
            table_idx = 0 if worktables[machine][0] <= worktables[machine][1] else 1
        last_table[machine] = table_idx
        table_occ_start = max(raw_loaded_end, worktables[machine][table_idx])
        start = max(free[machine], table_occ_start)
        cut_end = start + r["切割工时(min)"]
        handling = float(r.get("工位处理时间(min)", 0.0))
        table_end = cut_end + handling
        worktables[machine][table_idx] = table_end
        gun_free[machine] = cut_end
        free[machine] = max(gun_free[machine], min(worktables[machine]))
        rows.append({
            "套料图名": r["套料图名"],
            "分段号": r["分段号"],
            "切割机": machine,
            "切割序号": sequence,
            "切割开始(min)": start,
            "切割完成(min)": cut_end,
            "切割工时(min)": r["切割工时(min)"],
            "原料吊运开始(min)": raw_start,
            "原料空驶完成(min)": raw_empty_end,
            "原料吊运完成(min)": raw_loaded_end,
            "原料吊运返回完成(min)": raw_loaded_end,
            "工位开始(min)": table_occ_start,
            "等待结束(min)": start,
            "工位处理时间(min)": handling,
            "工位完工(min)": table_end,
            "工位": table_idx,
            "最低优先级": r["最低优先级"],
        })
    sched = pd.DataFrame(rows)
    if parts is not None and len(parts) > 0:
        sched, _, _, _, _, _ = build_joint_schedule(sched, parts, cfg, pp)
    return sched


def build_joint_schedule(
    schedule: pd.DataFrame,
    parts: pd.DataFrame,
    cfg: ModelConfig,
    pp: ProcessParams | None = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Dict[str, float], pd.DataFrame, list]:
    """切割与下游联合排程。

    按钢板顺序逐块处理：先分配胎架和切割头，钢板切割完成后立即用真实资源
    安排小件分拣/桁架转运/自动打磨，以及大件自由边打磨/天车转运；取这些
    实际动作的完成时间作为胎架释放时间，下一块钢板只有胎架真正释放后才能
    开始切割。返回 (排程, 零件完成表, 齐套分组, 指标, 工序表, 缓存时序)。
    """
    if "切割序号" in schedule.columns and schedule["切割序号"].notna().all():
        ordered = schedule.sort_values("切割序号", kind="stable").reset_index(drop=True)
    elif "切割开始(min)" in schedule.columns:
        ordered = schedule.sort_values("切割开始(min)", kind="stable").reset_index(drop=True)
    else:
        ordered = schedule.reset_index(drop=True)

    plate_parts_map = {
        str(name): group.sort_values(["齐套优先级", "零件名"], kind="stable")
        for name, group in parts.groupby("套料图名", sort=False)
    }

    # Truss pool for sorting (with cross-machine travel penalty)
    per_side_truss = max(1, math.ceil(cfg.small_sorters / 2))
    truss_pools = {
        "N2": TrussPool(n_arms=per_side_truss, travel_time=cfg.truss_travel_minutes, name_prefix="自动分拣N2"),
        "N5": TrussPool(n_arms=per_side_truss, travel_time=cfg.truss_travel_minutes, name_prefix="自动分拣N5"),
    }

    def _truss_for(cut_machine: str) -> TrussPool:
        """N2/N8 只能用 N2 侧桁架，N5 只能用 N5 侧桁架，不能跨机使用。"""
        if cut_machine.startswith("N2") or cut_machine.startswith("N8"):
            return truss_pools["N2"]
        return truss_pools["N5"]

    per_side_grind = max(1, math.ceil(cfg.small_grinders / 2))
    sg_pools = {
        "N2": ResourcePool([f"自动打磨N2{i+1}" for i in range(per_side_grind)]),
        "N5": ResourcePool([f"自动打磨N5{i+1}" for i in range(per_side_grind)]),
    }

    def _grind_for(cut_machine: str) -> ResourcePool:
        """N2/N8 只能用 N2 侧自动打磨机，N5 只能用 N5 侧自动打磨机，不能混用。"""
        if cut_machine.startswith("N2") or cut_machine.startswith("N8"):
            return sg_pools["N2"]
        return sg_pools["N5"]

    per_side_lg = max(1, math.ceil(cfg.large_grinders / 2))
    lg_pools = {
        "N2": ResourcePool([f"人工打磨N2{i+1}" for i in range(per_side_lg)]),
        "N5": ResourcePool([f"人工打磨N5{i+1}" for i in range(per_side_lg)]),
    }

    def _large_grind_for(cut_machine: str) -> ResourcePool:
        """大件人工打磨同样按切割机侧隔离，N2/N5不能混用。"""
        if cut_machine.startswith("N2") or cut_machine.startswith("N8"):
            return lg_pools["N2"]
        return lg_pools["N5"]

    ab_pool = ResourcePool([f"自动坡口{i+1}" for i in range(cfg.auto_bevel_machines)])
    mb_pool = ResourcePool([f"人工坡口{i+1}" for i in range(cfg.manual_bevel_stations)])
    stages: List[Dict[str, object]] = []
    # 使用附件3工艺参数（若有），否则回退到 ModelConfig
    sg_speed = pp.small_grind_speed_mm_min if pp else cfg.small_grind_speed_mm_min
    lg_speed = pp.large_grind_speed_mm_min if pp else cfg.large_grind_speed_mm_min
    mb_speed = pp.manual_bevel_speed_mm_min if pp else cfg.manual_bevel_speed_mm_min
    small_trans = pp.small_transfer_minutes if pp else cfg.small_transfer_minutes
    large_trans = pp.large_transfer_minutes if pp else cfg.large_transfer_minutes

    def add_stage(part_name: str, stage: str, pool: ResourcePool, ready: float, duration: float) -> float:
        resource, start, end = pool.reserve(ready, duration)
        stages.append({"零件名": part_name, "工序": stage, "资源": resource, "开始(min)": start, "结束(min)": end, "时长(min)": end - start})
        return end

    # ── 初始化AGV路径网络（Phase 1 天车运输和 Phase 2 AGV运输共用）──
    agv_network = AGVPathNetwork(n_agvs=cfg.agvs, zone_travel_time=cfg.small_transfer_minutes)
    crane_pool = CranePool()

    # ── 联合排程：切割 + 下游处理按钢板顺序同步推进 ──
    part_proc_done: Dict[str, float] = {}   # 零件加工完成（可码垛）时刻
    part_arrival: Dict[str, float] = {}     # 零件到达半框缓存/成品区时刻（AGV或行车后）
    part_has_bevel: Dict[str, bool] = {}
    part_seg: Dict[str, str] = {}
    part_pri: Dict[str, int] = {}
    part_type: Dict[str, str] = {}
    part_release: Dict[str, float] = {}  # 胎架释放时刻
    part_plate: Dict[str, str] = {}
    part_bevel_dur: Dict[str, float] = {}

    machines = machine_names(cfg)
    free = {m: 0.0 for m in machines}
    worktables = {m: [0.0, 0.0] for m in machines}
    gun_free = {m: 0.0 for m in machines}
    last_table = {m: 1 for m in machines}
    rows: list[dict] = []
    machine_map: Dict[str, str] = {}
    prebooked_raw: Dict[str, dict] = {}

    def _append_raw_stages(plate_name: str, raw_start: float, raw_empty_end: float, raw_loaded_end: float) -> None:
        if raw_empty_end > raw_start + 1e-9:
            stages.append({
                "零件名": plate_name,
                "工序": "天车空驶（胎架到原料区）",
                "资源": "天车",
                "开始(min)": raw_start, "结束(min)": raw_empty_end,
                "时长(min)": raw_empty_end - raw_start,
            })
        stages.append({
            "零件名": plate_name,
            "工序": "天车吊运（原料区到胎架）",
            "资源": "天车",
            "开始(min)": raw_empty_end, "结束(min)": raw_loaded_end,
            "时长(min)": raw_loaded_end - raw_empty_end,
        })

    def _current_crane_time() -> float:
        if not crane_pool.intervals:
            return 0.0
        return max(e for _, e in crane_pool.intervals)

    def _try_prebook_next_raw(seq: int, cur_machine: str, cur_table: int, ready_limit: float) -> bool:
        """大件转运前，若下一块钢板已有一个空闲胎架，先安排它的原料上料。

        只有这次上料能在大件转运就绪前完成时才提前安排，避免反过来拖慢
        当前钢板的大件转运和胎架释放。
        """
        if seq >= len(ordered):
            return False
        nxt = ordered.iloc[seq]
        nxt_name = str(nxt["套料图名"])
        if nxt_name in prebooked_raw or bool(nxt.get("_fixed", False)):
            return False
        m = pick_machine(nxt, free)
        cand: list[int] = []
        for ti in (0, 1):
            if m == cur_machine and ti == cur_table:
                continue
            if worktables[m][ti] <= ready_limit + 1e-9:
                cand.append(ti)
        if not cand:
            return False
        table_idx = min(cand, key=lambda ti: worktables[m][ti])
        target = worktables[m][table_idx]

        snap_intervals = list(crane_pool.intervals)
        snap_location = crane_pool.current_location
        snap_stage_len = len(stages)
        raw_start, raw_empty_end, raw_loaded_end = _reserve_raw_material_crane(
            crane_pool, target, cfg,
        )
        if raw_loaded_end > ready_limit + 1e-9:
            # 会拖慢当前大件转运，放弃这次提前上料
            crane_pool.intervals = snap_intervals
            crane_pool.current_location = snap_location
            del stages[snap_stage_len:]
            return False

        _append_raw_stages(nxt_name, raw_start, raw_empty_end, raw_loaded_end)
        prebooked_raw[nxt_name] = {
            "machine": m,
            "table_idx": table_idx,
            "raw_start": raw_start,
            "raw_empty_end": raw_empty_end,
            "raw_loaded_end": raw_loaded_end,
        }
        return True

    for seq, (_, r) in enumerate(ordered.iterrows(), start=1):
        plate_name = str(r["套料图名"])
        is_fixed = bool(r.get("_fixed", False))
        pre = prebooked_raw.get(plate_name)
        machine = pre["machine"] if pre is not None else (
            str(r["切割机"]) if pd.notna(r.get("切割机")) else pick_machine(r, free)
        )
        machine_map[plate_name] = machine

        if pre is not None:
            raw_start, raw_empty_end, raw_loaded_end = (
                pre["raw_start"], pre["raw_empty_end"], pre["raw_loaded_end"],
            )
        else:
            # 原料 -> 切割胎架：天车从当前位置出发，材料就绪前移动
            raw_ready = float(r["切割开始(min)"]) if is_fixed else min(worktables[machine])
            raw_start, raw_empty_end, raw_loaded_end = _reserve_raw_material_crane(
                crane_pool, raw_ready, cfg,
            )
            _append_raw_stages(plate_name, raw_start, raw_empty_end, raw_loaded_end)

        if is_fixed:
            # 动态重排中的冻结钢板：保持原切割时刻，只按真实下游动作修正胎架释放
            st = float(r["切割开始(min)"])
            en = float(r["切割完成(min)"])
            cut_dur = max(0.0, en - st)
            table_occ_start = float(r.get("工位开始(min)", st))
            input_table_end = float(r.get("工位完工(min)", en))
            table_idx = int(r.get("工位", 0)) if pd.notna(r.get("工位")) else (
                0 if worktables[machine][0] <= st else 1
            )
        elif pre is not None:
            table_idx = pre["table_idx"]
            last_table[machine] = table_idx
            table_occ_start = max(raw_loaded_end, worktables[machine][table_idx])
            st = max(free[machine], table_occ_start)
            cut_dur = float(r.get("切割工时(min)", 0.0))
            en = st + cut_dur
            input_table_end = en
        else:
            # 双工位切割机：切割枪只占切割时间，工位需等吊运/桁架/打磨处理完成
            if worktables[machine][0] <= raw_loaded_end and worktables[machine][1] <= raw_loaded_end:
                table_idx = 1 - last_table[machine]
            elif worktables[machine][0] <= raw_loaded_end:
                table_idx = 0
            elif worktables[machine][1] <= raw_loaded_end:
                table_idx = 1
            else:
                table_idx = 0 if worktables[machine][0] <= worktables[machine][1] else 1
            last_table[machine] = table_idx
            table_occ_start = max(raw_loaded_end, worktables[machine][table_idx])
            st = max(free[machine], table_occ_start)
            cut_dur = float(r.get("切割工时(min)", 0.0))
            en = st + cut_dur
            input_table_end = en

        plate_small_end = en
        plate_large_release = en
        plate_part_names: list[str] = []
        large_part_rows: list[dict] = []
        plate_prebooked = False
        plate_parts = plate_parts_map.get(plate_name, pd.DataFrame())
        for _, pr in plate_parts.iterrows():
            pname = str(pr["零件名"])
            plate_part_names.append(pname)
            if pr["零件类型"] == "small":
                # Truss sorting with machine info for conflict modeling
                need_grind = pr.get("打磨长度_模型(mm)", 0) > 0
                is_n5 = machine.startswith("N5")
                if is_n5:
                    # N5 小件必须先到自由边打磨工作台，再进入 N5 旁框
                    sort_dur = cfg.small_sort_minutes
                else:
                    sort_dur = (
                        cfg.small_sort_minutes
                        if need_grind
                        else cfg.small_truss_direct_palletize_minutes
                    )
                grind_ready = en
                goes_to_grind = is_n5 or need_grind
                truss_name, t_start, t_end = _truss_for(machine).reserve(grind_ready, sort_dur, machine)
                stages.append({"零件名": pname, "工序": "分拣", "资源": truss_name,
                                "开始(min)": t_start, "结束(min)": t_end, "时长(min)": t_end - t_start})
                t = t_end
                has_bv = pr.get("自动坡口长度(mm)", 0) > 0
                if has_bv:
                    thickness = float(pr.get("厚度(mm)", 6.0))
                    bv_table = pp.speed_table if pp else None
                    bv_sp = _get_speeds_for_thickness(thickness, bv_table) if bv_table else None
                    vy_bv = bv_sp["vy_bevel"] if bv_sp else (pp.auto_bevel_speed_mm_min if pp else cfg.auto_bevel_speed_mm_min)
                    xk_bv = bv_sp["xk_bevel"] if bv_sp else (pp.auto_bevel_speed_mm_min if pp else cfg.auto_bevel_speed_mm_min) / 2
                    y_len = float(pr.get("Y坡长度(mm)", 0) or 0)
                    x_len = float(pr.get("X坡长度(mm)", 0) or 0)
                    k_len = float(pr.get("K坡长度(mm)", 0) or 0)
                    part_bevel_dur[pname] = (
                        y_len / vy_bv
                        + (x_len + k_len) / xk_bv
                        + (pp.auto_bevel_overhead_minutes if pp else cfg.auto_bevel_overhead_minutes)
                    )
                if is_n5 or need_grind:
                    grind_dur = pr.get("打磨长度_模型(mm)", 0) * 2 / sg_speed
                    if pp:
                        grind_dur += pp.small_grind_scan_minutes if pp else cfg.small_grind_scan_minutes
                    t = add_stage(pname, "自动打磨", _grind_for(machine), t, grind_dur)

                if is_n5:
                    # N5：打磨后全部进入 N5 旁框，坡口/非坡口分开装
                    truss_name2, pt_start, pt_end = _truss_for(machine).reserve(
                        t, cfg.small_truss_palletize_minutes, machine,
                    )
                    stages.append({"零件名": pname, "工序": "桁架码垛", "资源": truss_name2,
                                    "开始(min)": pt_start, "结束(min)": pt_end,
                                    "时长(min)": pt_end - pt_start})
                    t = pt_end
                elif has_bv:
                    # N2：打磨后由桁架直接到坡口工作站
                    truss_name2, pt_start, pt_end = _truss_for(machine).reserve(
                        t, getattr(cfg, "n2_bevel_truss_minutes", 1.0), machine,
                    )
                    stages.append({"零件名": pname, "工序": "桁架至坡口工作站", "资源": truss_name2,
                                    "开始(min)": pt_start, "结束(min)": pt_end,
                                    "时长(min)": pt_end - pt_start})
                    t = pt_end
                elif need_grind:
                    # N2：打磨后桁架码垛到 N2 旁框
                    truss_name2, pt_start, pt_end = _truss_for(machine).reserve(
                        t, cfg.small_truss_palletize_minutes, machine,
                    )
                    stages.append({"零件名": pname, "工序": "桁架码垛", "资源": truss_name2,
                                    "开始(min)": pt_start, "结束(min)": pt_end,
                                    "时长(min)": pt_end - pt_start})
                    t = pt_end
                # N2 无需打磨且无需坡口：分拣结束即已到 N2 旁框
                part_proc_done[pname] = t
                part_arrival[pname] = t  # 小件加工完成即可进入码垛/坡口工作站
                plate_small_end = max(plate_small_end, t)
            else:
                # 大件：先只在胎架上安排自由边打磨；不需打磨的等待需要打磨的完成。
                # 整板大件之后由人工天车按目的地成组一次转运（新工艺说明）。
                grind_dur = float(pr.get("打磨长度_模型(mm)", 0) or 0) / lg_speed
                has_bv = float(pr.get("人工坡口长度(mm)", 0) or 0) > 0
                if grind_dur > 1e-9:
                    t_grind = add_stage(
                        pname,
                        "自由边打磨",
                        _large_grind_for(machine),
                        en,
                        grind_dur,
                    )
                else:
                    t_grind = en
                if not plate_prebooked:
                    _try_prebook_next_raw(seq, machine, table_idx, t_grind)
                    plate_prebooked = True
                large_part_rows.append({
                    "pname": pname,
                    "has_bv": has_bv,
                    "grind_end": t_grind,
                    "bevel_dur": (
                        float(pr.get("人工坡口长度(mm)", 0) or 0) / mb_speed
                        if has_bv else 0.0
                    ),
                })
            part_has_bevel[pname] = has_bv
            part_seg[pname] = str(pr["分段号"])
            part_pri[pname] = int(pr["齐套优先级"])
            part_type[pname] = pr["零件类型"]
            part_plate[pname] = plate_name

        # ── 整板大件成组转运：人工天车一次可转运多个大件 ──
        for has_bv_group in (False, True):
            group = [x for x in large_part_rows if bool(x["has_bv"]) == has_bv_group]
            if not group:
                continue
            group_ready = max(x["grind_end"] for x in group)
            batch_label = f"{plate_name}切割后的大件"
            if has_bv_group:
                # 需要人工坡口的大件整批先到坡口区，坡口完成后再整批到成品区
                c_start, empty_end, loaded_end = crane_pool.reserve_move(
                    group_ready, "胎架", "坡口区",
                    getattr(cfg, "crane_bevel_transfer_minutes", 3.0), cfg,
                )
                if empty_end > c_start + 1e-9:
                    stages.append({"零件名": batch_label,
                                    "工序": f"天车空驶（{crane_pool.last_empty_origin}到{crane_pool.last_empty_dest}）",
                                    "资源": "天车",
                                    "开始(min)": c_start, "结束(min)": empty_end,
                                    "时长(min)": empty_end - c_start})
                stages.append({"零件名": batch_label,
                                "工序": f"天车吊运（{crane_pool.last_loaded_origin}到{crane_pool.last_loaded_dest}）",
                                "资源": "天车",
                                "开始(min)": empty_end, "结束(min)": loaded_end,
                                "时长(min)": loaded_end - empty_end})
                group_release = loaded_end
                t_bevel = loaded_end
                for x in sorted(group, key=lambda z: z["pname"]):
                    t_bevel = add_stage(x["pname"], "人工坡口", mb_pool, t_bevel, x["bevel_dur"])
                c2_start, empty2_end, loaded2_end = crane_pool.reserve_move(
                    t_bevel, "坡口区", "成品区", large_trans, cfg,
                )
                if empty2_end > c2_start + 1e-9:
                    stages.append({"零件名": batch_label,
                                    "工序": f"天车空驶（{crane_pool.last_empty_origin}到{crane_pool.last_empty_dest}）",
                                    "资源": "天车",
                                    "开始(min)": c2_start, "结束(min)": empty2_end,
                                    "时长(min)": empty2_end - c2_start})
                stages.append({"零件名": batch_label,
                                "工序": f"天车吊运（{crane_pool.last_loaded_origin}到{crane_pool.last_loaded_dest}）",
                                "资源": "天车",
                                "开始(min)": empty2_end, "结束(min)": loaded2_end,
                                "时长(min)": loaded2_end - empty2_end})
                final_end = loaded2_end
            else:
                # 不需人工坡口的大件整批直接到成品区
                c_start, empty_end, loaded_end = crane_pool.reserve_move(
                    group_ready, "胎架", "成品区", large_trans, cfg,
                )
                if empty_end > c_start + 1e-9:
                    stages.append({"零件名": batch_label,
                                    "工序": f"天车空驶（{crane_pool.last_empty_origin}到{crane_pool.last_empty_dest}）",
                                    "资源": "天车",
                                    "开始(min)": c_start, "结束(min)": empty_end,
                                    "时长(min)": empty_end - c_start})
                stages.append({"零件名": batch_label,
                                "工序": f"天车吊运（{crane_pool.last_loaded_origin}到{crane_pool.last_loaded_dest}）",
                                "资源": "天车",
                                "开始(min)": empty_end, "结束(min)": loaded_end,
                                "时长(min)": loaded_end - empty_end})
                group_release = loaded_end
                final_end = loaded_end
            for x in group:
                part_proc_done[x["pname"]] = final_end
                part_arrival[x["pname"]] = final_end  # 大件完成即到成品区
            plate_large_release = max(plate_large_release, group_release)

        # 胎架释放 = 该钢板所有下游真实动作完成时刻
        actual_table_end = max(input_table_end, en, plate_small_end, plate_large_release)
        for pname in plate_part_names:
            part_release[pname] = actual_table_end
        worktables[machine][table_idx] = actual_table_end
        gun_free[machine] = en
        free[machine] = max(gun_free[machine], min(worktables[machine]))

        _pri_val = r.get("最低优先级", 0)
        rows.append({
            "套料图名": plate_name,
            "分段号": str(r.get("分段号", "")),
            "切割机": machine,
            "切割序号": seq,
            "切割开始(min)": st,
            "切割完成(min)": en,
            "切割工时(min)": cut_dur,
            "原料吊运开始(min)": raw_start,
            "原料空驶完成(min)": raw_empty_end,
            "原料吊运完成(min)": raw_loaded_end,
            "原料吊运返回完成(min)": raw_loaded_end,
            "工位开始(min)": table_occ_start,
            "等待结束(min)": st,
            "工位处理时间(min)": actual_table_end - en,
            "工位完工(min)": actual_table_end,
            "工位": table_idx,
            "最低优先级": int(_pri_val) if pd.notna(_pri_val) else 0,
        })

    new_schedule = pd.DataFrame(rows)
    if len(new_schedule) == 0:
        new_schedule = pd.DataFrame(columns=[
            "套料图名", "分段号", "切割机", "切割序号", "切割开始(min)", "切割完成(min)",
            "切割工时(min)", "原料吊运开始(min)", "原料空驶完成(min)", "原料吊运完成(min)",
            "原料吊运返回完成(min)", "工位开始(min)", "等待结束(min)", "工位处理时间(min)",
            "工位完工(min)", "工位", "最低优先级",
        ])
    schedule = new_schedule
    machine_map = dict(zip(schedule["套料图名"], schedule["切割机"]))

    # ── Phase 2: 料框批处理 + 三级缓存区容量约束（增强版）──
    # 三级缓存区：切割机码垛区 → 坡口缓存区 → 齐套缓存区
    # 增强功能：零件级占用追踪、FIFO消费、死锁检测、时间序列数据
    from collections import defaultdict

    # 构建料框分组（小件/大件分属不同料框，避免混装）
    bin_parts: Dict[tuple, list] = defaultdict(list)
    part_machine: Dict[str, str] = {}
    for pname in part_proc_done:
        mach = str(machine_map.get(part_plate[pname], "N2"))
        part_machine[pname] = mach
        key = (part_seg[pname], part_pri[pname], part_has_bevel[pname], part_type[pname], mach)
        bin_parts[key].append(pname)

    all_machines = list(schedule["切割机"].unique())
    n_machines = len(all_machines)
    # ── P1-3: 统一引用 ModelConfig.PER_MACHINE_CAPS（消除重复定义）──
    per_machine_caps = getattr(cfg, 'PER_MACHINE_CAPS', {"N2": 10, "N5": 18})
    buffer_cap_per_machine_map: dict[str, int] = {}
    remaining_cap = max(0, cfg.finish_buffer_capacity - sum(
        per_machine_caps.get(m, 0) for m in all_machines if m in per_machine_caps))
    unassigned = [m for m in all_machines if m not in per_machine_caps]
    for m in all_machines:
        if m in per_machine_caps:
            buffer_cap_per_machine_map[m] = per_machine_caps[m]
        else:
            buffer_cap_per_machine_map[m] = max(1, remaining_cap // max(1, len(unassigned)))
    bevel_buffer_cap = max(1, cfg.bevel_buffer_capacity)
    half_buffer_cap = max(1, cfg.half_buffer_capacity)
    kit_buffer_cap = max(1, cfg.kit_buffer_capacity)
    bevel_workstation_cap = max(1, getattr(cfg, "bevel_workstation_capacity", 7))
    kit_dwell = max(0.0, getattr(cfg, 'kit_dwell_minutes', 60.0))

    # ── 零件级缓存追踪 ──
    # 每个缓冲区记录: [(entry_time, exit_time, part_name), ...]
    buffer_parts: dict[str, list[tuple[float, float, str]]] = {f"{m}码垛": [] for m in all_machines}
    buffer_parts["坡口缓存"] = []
    buffer_parts["半框缓存"] = []
    buffer_parts["坡口工作站"] = []
    buffer_parts["齐套缓存"] = []

    # 缓存区占用时间序列（用于前端可视化）
    buffer_timeseries: list[dict] = []

    buffer_peaks: dict[str, int] = {f"{m}码垛": 0 for m in all_machines}
    buffer_peaks["坡口缓存"] = 0
    buffer_peaks["半框缓存"] = 0
    buffer_peaks["坡口工作站"] = 0
    buffer_peaks["齐套缓存"] = 0

    # 死锁/溢出告警收集
    deadlock_warnings: list[str] = []

    # 辅助函数：返回缓冲区当前占用数（不清理，保证峰值/时序统计正确；内存规模 O(零件数) 可忽略）
    def _clean_and_count(buf_name: str, at_time: float) -> int:
        parts_list = buffer_parts.get(buf_name, [])
        if not parts_list:
            return 0
        return sum(1 for et, xt, _pn in parts_list if et <= at_time < xt)

    def _add_buffer_event(buf_name: str, entry_time: float, exit_time: float, part_label: str):
        """记录零件进入和离开缓冲区的事件。"""
        buffer_parts.setdefault(buf_name, []).append((entry_time, exit_time, part_label))

    # ── 齐套门控所需的零件/料框级时刻追踪 ──
    import heapq
    half_entry: Dict[str, float] = {}        # 零件进入缓存区/坡口工作站的时刻
    bin_half_entry: Dict[tuple, float] = {}  # 料框到达缓存区/坡口工作站的时刻

    # 按“分段”齐套：每个分段的所有优先级都齐了才允许进最终小部材放置区
    segment_pri: Dict[str, set] = defaultdict(set)
    group_has_bevel: set = set()
    group_has_nobevel: set = set()
    small_bin_count_nb: Dict[tuple, int] = defaultdict(int)
    small_bin_count_bv: Dict[tuple, int] = defaultdict(int)
    small_bin_arrived_nb: Dict[tuple, int] = defaultdict(int)
    small_bin_arrived_bv: Dict[tuple, int] = defaultdict(int)
    group_cache_ready: Dict[tuple, float] = {}
    group_ws_ready: Dict[tuple, float] = {}
    group_ready: Dict[tuple, float] = {}
    large_ready_by_segment: Dict[str, float] = defaultdict(float)
    segment_final_scheduled: set = set()

    for pname in part_proc_done:
        gk = (part_seg[pname], int(part_pri[pname]))
        segment_pri[str(part_seg[pname])].add(int(part_pri[pname]))
        if part_type[pname] == "large":
            large_ready_by_segment[str(part_seg[pname])] = max(
                large_ready_by_segment[str(part_seg[pname])],
                part_proc_done[pname],
            )
            half_entry[pname] = part_proc_done[pname]

    for bin_key, pnames in bin_parts.items():
        if part_type[pnames[0]] == "small":
            gk = (str(bin_key[0]), int(bin_key[1]))
            if bin_key[2]:
                group_has_bevel.add(gk)
                machine_for_bin = str(bin_key[4]) if len(bin_key) > 4 else "N2"
                if machine_for_bin.startswith("N2"):
                    small_bin_count_bv[gk] += len(pnames)
                else:
                    small_bin_count_bv[gk] += 1
            else:
                group_has_nobevel.add(gk)
                small_bin_count_nb[gk] += 1

    for seg_name, pri_set in segment_pri.items():
        for p in pri_set:
            gk = (seg_name, int(p))
            if gk not in group_has_bevel and gk not in group_has_nobevel:
                group_ready[gk] = large_ready_by_segment.get(seg_name, 0.0)

    group_parts: Dict[tuple, list] = defaultdict(list)
    for bin_key, pnames in bin_parts.items():
        if part_type[pnames[0]] == "small":
            group_parts[(str(bin_key[0]), int(bin_key[1]))].extend(pnames)
    cache_active: set = set()
    ws_active: set = set()
    bin_entry_time: Dict[tuple, float] = {}
    cache_exit_time: Dict[tuple, float] = {}
    ws_exit_time: Dict[tuple, float] = {}

    def _try_final_segment(seg_name: str) -> None:
        nonlocal _seq
        if seg_name in segment_final_scheduled:
            return
        pri_set = segment_pri.get(seg_name, set())
        if not pri_set:
            return
        if any((seg_name, int(p)) not in group_ready for p in pri_set):
            return
        ready_t = max(
            [group_ready[(seg_name, int(p))] for p in pri_set]
            + [large_ready_by_segment.get(seg_name, 0.0)]
        )
        segment_final_scheduled.add(seg_name)
        for p in sorted(pri_set, key=int):
            heapq.heappush(events, (ready_t, _seq, 4, (seg_name, int(p)), None))
            _seq += 1

    # 事件队列（时间优先）：(time, seq, kind, bin_key, pnames)；kind 0=码垛→半框，kind 1=半框→齐套
    events: list = []
    _seq = 0
    for bin_key, pnames in sorted(bin_parts.items()):
        if part_type[pnames[0]] != "small":
            continue
        bin_ready = max(part_proc_done[p] for p in pnames)
        machine_for_bin = str(bin_key[4]) if len(bin_key) > 4 else "N2"
        kind = 2 if (bin_key[2] and machine_for_bin.startswith("N2")) else 0
        if kind == 2:
            # N2 自动坡口按零件级事件：一件一件到坡口工作站
            for p in pnames:
                heapq.heappush(events, (part_proc_done[p], _seq, 2, bin_key, [p]))
                _seq += 1
        else:
            heapq.heappush(events, (bin_ready, _seq, kind, bin_key, pnames))
            _seq += 1

    while events:
        t, _, kind, bin_key, pnames = heapq.heappop(events)
        seg_name = str(bin_key[0])
        pri_val = int(bin_key[1])
        is_bevel_bin = len(bin_key) > 2 and bool(bin_key[2])
        machine_for_bin = str(bin_key[4]) if len(bin_key) > 4 else "N2"
        bin_label = f"料框_{machine_for_bin}_{seg_name}_P{pri_val}{'_坡口' if is_bevel_bin else ''}"

        if kind == 0:
            # ── Phase A: 机旁码垛区 →(坡口工位)→ 半框缓存区 ──
            buf_name = f"{machine_for_bin}码垛"
            buffer_cap = buffer_cap_per_machine_map.get(machine_for_bin, max(1, cfg.finish_buffer_capacity // max(1, n_machines)))
            current_occ = _clean_and_count(buf_name, t)
            buffer_peaks[buf_name] = max(buffer_peaks[buf_name], current_occ)
            if current_occ >= buffer_cap:
                # 码垛区满：延迟该料框进入（重调度到最早离开的料框之后），避免物理超配
                active_exits = [xt for et, xt, _ in buffer_parts[buf_name] if et <= t < xt]
                if active_exits:
                    heapq.heappush(events, (min(active_exits) + 0.1, _seq, 0, bin_key, pnames))
                    _seq += 1
                    continue
            effective_ready = t

            agv_label, agv_start, agv_end, _ = agv_network.reserve(
                effective_ready, small_trans, machine_for_bin, destination="加工区",
            )
            stages.append({
                "零件名": bin_label, "工序": "AGV转运" + ("(至坡口)" if is_bevel_bin else "(至半框)"),
                "资源": agv_label, "开始(min)": agv_start, "结束(min)": agv_end,
                "时长(min)": agv_end - agv_start,
            })

            gk = (seg_name, pri_val)
            if is_bevel_bin:
                # N5：机旁框 -> 坡口缓存区 -> 坡口工作站
                bevel_occ = _clean_and_count("坡口缓存", agv_end)
                buffer_peaks["坡口缓存"] = max(buffer_peaks["坡口缓存"], bevel_occ)
                bevel_ready = agv_end
                if bevel_occ >= bevel_buffer_cap:
                    bp = sorted(buffer_parts["坡口缓存"], key=lambda x: x[1])
                    bevel_ready = max(
                        agv_end,
                        bp[-(bevel_buffer_cap - 1)][1] + 0.1
                        if bevel_buffer_cap > 1
                        else bp[0][1] + 0.1,
                    )
                bevel_end = bevel_ready
                _add_buffer_event("坡口缓存", agv_end, max(bevel_end, agv_end + 0.1), bin_label)
                _add_buffer_event(buf_name, t, agv_end, bin_label)
                bin_entry_time[bin_key] = agv_end
                heapq.heappush(events, (max(bevel_end, agv_end + 0.1), _seq, 1, bin_key, pnames))
                _seq += 1
                continue

            # 非坡口：机旁框 -> 缓存区（容量14，当前实现记录峰值但不阻塞）
            half_arrival = agv_end
            _add_buffer_event(buf_name, t, agv_end, bin_label)
            bin_half_entry[bin_key] = half_arrival
            for p in pnames:
                half_entry[p] = half_arrival
            cache_active.add(bin_key)
            bin_entry_time[bin_key] = half_arrival
            small_bin_arrived_nb[gk] += 1
            group_cache_ready[gk] = max(group_cache_ready.get(gk, 0.0), half_arrival)
            if small_bin_arrived_nb[gk] >= small_bin_count_nb[gk]:
                if gk in group_has_bevel:
                    if gk in group_ws_ready and gk not in group_ready:
                        heapq.heappush(
                            events,
                            (max(group_cache_ready[gk], group_ws_ready[gk]), _seq, 3, (seg_name, pri_val), None),
                        )
                        _seq += 1
                else:
                    group_ready[gk] = group_cache_ready[gk]
                    _try_final_segment(seg_name)

        elif kind == 1:
            # N5：坡口缓存区 -> 坡口工作站
            agv_label2, agv_start2, agv_end2, _ = agv_network.reserve(
                t, small_trans, "加工区", destination="加工区",
            )
            stages.append({
                "零件名": bin_label, "工序": "AGV至坡口工作站", "资源": agv_label2,
                "开始(min)": agv_start2, "结束(min)": agv_end2,
                "时长(min)": agv_end2 - agv_start2,
            })
            arrival = agv_end2
            bevel_total_dur = sum(part_bevel_dur.get(p, 0.0) for p in pnames)
            ab_name, ab_start, ab_end = ab_pool.reserve(arrival, max(bevel_total_dur, 0.1))
            stages.append({
                "零件名": bin_label, "工序": "自动坡口", "资源": ab_name,
                "开始(min)": ab_start, "结束(min)": ab_end,
                "时长(min)": ab_end - ab_start,
            })
            ws_end = ab_end
            _add_buffer_event("坡口工作站", arrival, ws_end, bin_label)
            ws_active.add(bin_key)
            bin_entry_time[bin_key] = arrival
            bin_half_entry[bin_key] = arrival
            for p in pnames:
                half_entry[p] = arrival
            gk = (seg_name, pri_val)
            small_bin_arrived_bv[gk] += 1
            group_ws_ready[gk] = max(group_ws_ready.get(gk, 0.0), ws_end)
            if small_bin_arrived_bv[gk] >= small_bin_count_bv[gk]:
                if gk in group_has_nobevel:
                    if gk in group_cache_ready and gk not in group_ready:
                        heapq.heappush(
                            events,
                            (max(group_cache_ready[gk], group_ws_ready[gk]), _seq, 3, (seg_name, pri_val), None),
                        )
                        _seq += 1
                else:
                    group_ready[gk] = group_ws_ready[gk]
                    _try_final_segment(seg_name)

        elif kind == 2:
            # N2：打磨后桁架直接到坡口工作站
            arrival = t
            bevel_total_dur = sum(part_bevel_dur.get(p, 0.0) for p in pnames)
            ab_name, ab_start, ab_end = ab_pool.reserve(arrival, max(bevel_total_dur, 0.1))
            stages.append({
                "零件名": bin_label, "工序": "自动坡口", "资源": ab_name,
                "开始(min)": ab_start, "结束(min)": ab_end,
                "时长(min)": ab_end - ab_start,
            })
            ws_end = ab_end
            _add_buffer_event("坡口工作站", arrival, ws_end, bin_label)
            ws_active.add(bin_key)
            bin_entry_time[bin_key] = arrival
            bin_half_entry[bin_key] = arrival
            for p in pnames:
                half_entry[p] = arrival
            gk = (seg_name, pri_val)
            small_bin_arrived_bv[gk] += 1
            group_ws_ready[gk] = max(group_ws_ready.get(gk, 0.0), ws_end)
            if small_bin_arrived_bv[gk] >= small_bin_count_bv[gk]:
                if gk in group_has_nobevel:
                    if gk in group_cache_ready and gk not in group_ready:
                        heapq.heappush(
                            events,
                            (max(group_cache_ready[gk], group_ws_ready[gk]), _seq, 3, (seg_name, pri_val), None),
                        )
                        _seq += 1
                else:
                    group_ready[gk] = group_ws_ready[gk]
                    _try_final_segment(seg_name)

        elif kind == 3:
            # 缓存区非坡口料框 -> 坡口工作站，自动合盘
            agv_label3, agv_start3, agv_end3, _ = agv_network.reserve(
                t, small_trans, "加工区", destination="加工区",
            )
            gk = (str(bin_key[0]), int(bin_key[1]))
            label = f"料框_{bin_key[0]}_P{bin_key[1]}_合盘"
            stages.append({
                "零件名": label, "工序": "AGV合盘至坡口工作站", "资源": agv_label3,
                "开始(min)": agv_start3, "结束(min)": agv_end3,
                "时长(min)": agv_end3 - agv_start3,
            })
            for k in list(cache_active):
                if (str(k[0]), int(k[1])) == gk and not k[2]:
                    _add_buffer_event("半框缓存", bin_entry_time[k], agv_start3, f"料框_{k[0]}_P{k[1]}")
                    cache_active.remove(k)
            group_ready[gk] = agv_end3
            _try_final_segment(str(bin_key[0]))

        else:
            # 最终：齐套分段 -> 小部材放置区
            seg_name = str(bin_key[0])
            pri_val = int(bin_key[1])
            gk = (seg_name, pri_val)
            kit_occ = _clean_and_count("齐套缓存", t)
            if kit_occ >= kit_buffer_cap:
                heapq.heappush(events, (t + 0.1, _seq, 4, bin_key, None))
                _seq += 1
                continue
            agv_label4, agv_start4, agv_end4, _ = agv_network.reserve(
                t, small_trans, "加工区", destination="齐套区",
            )
            label = f"料框_{seg_name}_P{pri_val}"
            stages.append({
                "零件名": label, "工序": "AGV至小部材放置区", "资源": agv_label4,
                "开始(min)": agv_start4, "结束(min)": agv_end4,
                "时长(min)": agv_end4 - agv_start4,
            })
            for k in list(cache_active):
                if (str(k[0]), int(k[1])) == gk and not k[2]:
                    _add_buffer_event("半框缓存", bin_entry_time[k], agv_start4, f"料框_{k[0]}_P{k[1]}")
                    cache_active.remove(k)
            for k in list(ws_active):
                if (str(k[0]), int(k[1])) == gk and k[2]:
                    _add_buffer_event(
                        "坡口工作站",
                        bin_entry_time[k],
                        agv_start4,
                        f"料框_{k[4] if len(k) > 4 else ''}_{k[0]}_P{k[1]}_坡口",
                    )
                    ws_active.remove(k)
            _add_buffer_event("齐套缓存", agv_end4, agv_end4 + kit_dwell, label)
            for p in group_parts.get(gk, []):
                part_arrival[p] = agv_end4

    # ── 构建零件完成时间与分组（齐套跨度由半框缓存驱动）──
    result_rows = []
    for pname in part_proc_done:
        result_rows.append({
            "零件名": pname,
            "套料图名": part_plate[pname],
            "分段号": part_seg[pname],
            "齐套优先级": part_pri[pname],
            "零件类型": part_type[pname],
            "释放(min)": part_release[pname],
            "到半框缓存(min)": half_entry[pname],
            "到齐套区(min)": part_arrival[pname],
        })

    complete = pd.DataFrame(result_rows)
    # 齐套跨度 = 组内首件到达半框缓存 → 末件到达半框缓存（齐套达成，对标 12.7h）
    group = complete.groupby(["分段号", "齐套优先级"]).agg(
        零件数=("零件名", "size"),
        首件到齐套区=("到半框缓存(min)", "min"),
        齐套完成=("到半框缓存(min)", "max"),
    ).reset_index()
    group["齐套跨度(min)"] = group["齐套完成"] - group["首件到齐套区"]

    # ── 后处理：半框/齐套缓存峰值占用（框级，基于真实事件，无需 1e6 占位符修正）──
    def _compute_peak(events) -> int:
        if not events:
            return 0
        times = set()
        for et, xt, _ in events:
            times.add(et)
            times.add(xt)
        mx = 0
        for t in sorted(times):
            mx = max(mx, sum(1 for et, xt, _ in events if et <= t < xt))
        return mx

    buffer_peaks["半框缓存"] = max(buffer_peaks["半框缓存"], _compute_peak(buffer_parts["半框缓存"]))
    buffer_peaks["齐套缓存"] = max(buffer_peaks["齐套缓存"], _compute_peak(buffer_parts["齐套缓存"]))
    buffer_peaks["坡口工作站"] = max(buffer_peaks["坡口工作站"], _compute_peak(buffer_parts["坡口工作站"]))
    if buffer_peaks["半框缓存"] > half_buffer_cap:
        deadlock_warnings.append(
            f"[半框溢出] 半框缓存峰值={buffer_peaks['半框缓存']}/{half_buffer_cap}"
        )

    # ── 生成缓存区占用时间序列（用于前端可视化）──
    total_makespan_val = max(part_arrival.values()) if part_arrival else 0.0
    if not (np.isfinite(total_makespan_val) and total_makespan_val > 0):
        total_makespan_val = float(schedule["切割完成(min)"].max())
    sample_interval = max(0.5, total_makespan_val / 200)
    stop_val = total_makespan_val + sample_interval
    if stop_val <= 0 or sample_interval <= 0:
        sample_times = np.array([0.0])
    else:
        sample_times = np.arange(0.0, stop_val, sample_interval)

    buffer_timeseries = []
    for t in sample_times:
        entry = {"time_h": round(float(t) / 60, 4)}
        for m in all_machines:
            bn = f"{m}码垛"
            entry[f"{m}_码垛占用"] = _clean_and_count(bn, t)
        entry["坡口缓存占用"] = _clean_and_count("坡口缓存", t)
        entry["半框缓存占用"] = _clean_and_count("半框缓存", t)
        entry["齐套缓存占用"] = _clean_and_count("齐套缓存", t)
        entry["坡口工作站占用"] = _clean_and_count("坡口工作站", t)
        buffer_timeseries.append(entry)

    stages_df = pd.DataFrame(stages)
    makespan = float(complete["到齐套区(min)"].max())
    # 防护：确保 makespan 为有限正值
    if not (np.isfinite(makespan) and makespan > 0):
        makespan = float(schedule["切割完成(min)"].max())
    if not (np.isfinite(makespan) and makespan > 0):
        makespan = 480.0  # fallback: 8h shift
    cut_load = schedule.groupby("切割机")["切割工时(min)"].sum()
    agv_network.crane_busy = crane_pool.total_busy

    # ── P4-1：理论下界 LB（不依赖65h，随数据集自动缩放）──
    # LB1：总切割工时 / 切割机数
    # LB2：各加工/物流资源总加工需求 / 资源数
    # LB3：单张钢板最短关键链（原料吊运 + 切割 + 工位处理）
    n_cut_machines = len(all_machines)
    total_cut_minutes = float(cut_load.sum()) if len(cut_load) > 0 else 0.0
    lb_candidates = [total_cut_minutes / max(1, n_cut_machines)]
    if len(stages_df) > 0:
        def _resource_lb(keyword: str, count: int) -> float:
            total = float(stages_df.loc[
                stages_df["资源"].astype(str).str.contains(keyword, na=False),
                "时长(min)",
            ].sum())
            return total / max(1, count)
        lb_candidates.append(_resource_lb("自动打磨", getattr(cfg, "small_grinders", 2)))
        lb_candidates.append(_resource_lb("人工打磨", getattr(cfg, "large_grinders", 2)))
        lb_candidates.append(_resource_lb("自动坡口", getattr(cfg, "auto_bevel_machines", 1)))
        lb_candidates.append(_resource_lb("人工坡口", getattr(cfg, "manual_bevel_stations", 1)))
        lb_candidates.append(_resource_lb("AGV", getattr(cfg, "agvs", 2)))
        lb_candidates.append(_resource_lb("天车", 1))
        lb_candidates.append(_resource_lb("自动分拣", getattr(cfg, "small_sorters", 2)))
    lb3_min = 0.0
    if "工位处理时间(min)" in schedule.columns:
        for _, _r in schedule.iterrows():
            _raw_done = float(_r.get("原料吊运完成(min)", _r.get("切割开始(min)", 0.0)) or 0.0)
            _raw_start = float(_r.get("原料吊运开始(min)", _r.get("切割开始(min)", 0.0)) or 0.0)
            _chain = max(0.0, _raw_done - _raw_start) + float(_r["切割工时(min)"]) + float(_r.get("工位处理时间(min)", 0.0) or 0.0)
            lb3_min = max(lb3_min, _chain)
    if lb3_min > 0:
        lb_candidates.append(lb3_min)
    theoretical_lb_min = max(lb_candidates)

    # ── P4-1：等待时间软指标（切割前等待 + 齐套等待）──
    precut_wait_min = 0.0
    if "切割开始(min)" in schedule.columns and "原料吊运完成(min)" in schedule.columns:
        precut_wait_min = float(
            (schedule["切割开始(min)"] - schedule["原料吊运完成(min)"]).clip(lower=0).sum()
        )
    kit_wait_min = 0.0
    if len(complete) > 0 and len(group) > 0:
        _gk_complete = dict(zip(
            zip(group["分段号"], group["齐套优先级"]),
            group["齐套完成"],
        ))
        for _, _r in complete.iterrows():
            _key = (str(_r["分段号"]), int(_r["齐套优先级"]))
            if _key in _gk_complete:
                kit_wait_min += max(0.0, float(_gk_complete[_key]) - float(_r["到半框缓存(min)"]))
    total_wait_min = precut_wait_min + kit_wait_min
    n_plates = len(schedule)

    # ── 产能 KPI：张板/班(8h) ──
    shift_hours = 8.0
    throughput_per_shift = n_plates / (makespan / 60) * shift_hours  # plates per 8h shift

    # ── 切割机稼动率 ──
    # 方法1：综合稼动率（含残材/上料/穿孔等全部切割相关时间）
    # = 总切割工时 / (切割机数量 × makespan)
    n_cut_machines = len(all_machines)
    total_cut_minutes = float(cut_load.sum())
    overall_oee = total_cut_minutes / (n_cut_machines * makespan) if makespan > 0 else 0.0

    # 方法2：纯切割稼动率（仅计算直切+V坡时间，排除空行程/划线/穿孔/换板等辅助时间）
    # 用于对标竞赛 ≥61% 的硬性指标
    pure_cut_per_machine = {}
    for m in all_machines:
        m_plates = schedule[schedule["切割机"] == m]
        if len(m_plates) > 0:
            # 从原始 features 估算纯切割时间：总工时 - 每板固定开销
            n = len(m_plates)
            # Method B includes setup; Method A does not
            overhead_per_plate = cfg.cut_remainder_minutes
            if cfg.use_component_formula:
                overhead_per_plate += cfg.plate_setup_minutes
            # 还需减去空行程和划线的时间（从切割公式分解）
            total_overhead = n * overhead_per_plate
            raw_total = float(m_plates["切割工时(min)"].sum())
            pure_cut_per_machine[m] = max(0.0, raw_total - total_overhead)
        else:
            pure_cut_per_machine[m] = 0.0
    total_pure_cut = sum(pure_cut_per_machine.values())
    pure_cut_oee = total_pure_cut / (n_cut_machines * makespan) if makespan > 0 else 0.0

    # 方法3：班次稼动率（考虑8h班制，含交接班损耗）
    n_shifts = max(1, math.ceil(makespan / (shift_hours * 60)))
    shift_available_minutes = n_cut_machines * n_shifts * shift_hours * 60
    shift_based_oee = total_cut_minutes / shift_available_minutes if shift_available_minutes > 0 else 0.0

    # 保留原有 per-machine 利用率（负荷密度 = cut_load[m] / makespan）
    per_machine_util = {}
    for m in all_machines:
        per_machine_util[f"{m}利用率"] = float(cut_load.get(m, 0) / makespan) if makespan > 0 else 0.0

    # ── 缓存区占用率 ──
    bevel_peak = max(buffer_peaks.get("坡口缓存", 0), 1)
    half_peak = max(buffer_peaks.get("半框缓存", 0), 1)
    kit_peak = max(buffer_peaks.get("齐套缓存", 0), 1)
    bevel_ws_peak = max(buffer_peaks.get("坡口工作站", 0), 1)
    machine_buffer_peaks = {k: v for k, v in buffer_peaks.items() if "码垛" in k}

    # ── 各资源利用率 ──
    resource_utils = {}
    if len(stages_df) > 0:
        total_stage_makespan = float(stages_df["结束(min)"].max())
        for res_name, total_time in stages_df.groupby("资源")["时长(min)"].sum().items():
            resource_utils[f"{res_name}利用率"] = float(total_time / total_stage_makespan) if total_stage_makespan > 0 else 0.0

    # ── 胎架累计空闲时间（软指标）──
    platen_idle_min = 0.0
    if len(schedule) > 0:
        idle_sched = schedule.copy()
        idle_sched["_table"] = idle_sched.get("工位", pd.Series(0, index=idle_sched.index)).fillna(0).astype(int)
        idle_sched["_occ_start"] = idle_sched.get("工位开始(min)", idle_sched["切割开始(min)"]).fillna(idle_sched["切割开始(min)"])
        idle_sched["_occ_end"] = idle_sched.get("工位完工(min)", idle_sched["切割完成(min)"]).fillna(idle_sched["切割完成(min)"])
        for (_mach, _table), _g in idle_sched.sort_values("切割序号").groupby(["切割机", "_table"]):
            _prev_end = None
            for _, _srow in _g.iterrows():
                _occ_start = float(_srow["_occ_start"])
                if _prev_end is not None and _occ_start > _prev_end:
                    platen_idle_min += _occ_start - _prev_end
                _prev_end = float(_srow["_occ_end"])

    metrics = {
        "总完工时间(h)": makespan / 60,
        "理论下界(min)": round(float(theoretical_lb_min), 4),
        "理论下界(h)": round(float(theoretical_lb_min) / 60.0, 4),
        "产能效率(LB/Cmax)": round(float(theoretical_lb_min) / makespan, 4) if makespan > 0 else 0.0,
        "产能缺口((Cmax-LB)/LB)": round((makespan - theoretical_lb_min) / theoretical_lb_min, 4) if theoretical_lb_min > 0 else 0.0,
        "加权平均齐套跨度(h)": float(np.average(group["齐套跨度(min)"], weights=group["零件数"])) / 60,
        "最大齐套跨度(h)": float(group["齐套跨度(min)"].max()) / 60,
        "切割负载差(h)": float(cut_load.max() - cut_load.min()) / 60,
        "切割前等待总时间(min)": round(precut_wait_min, 4),
        "切割前等待总时间(h)": round(precut_wait_min / 60.0, 4),
        "齐套等待总时间(min)": round(kit_wait_min, 4),
        "齐套等待总时间(h)": round(kit_wait_min / 60.0, 4),
        "总等待时间(min)": round(total_wait_min, 4),
        "总等待时间(h)": round(total_wait_min / 60.0, 4),
        "平均钢板等待时间(h)": round(total_wait_min / 60.0 / max(1, n_plates), 4),
        "等待占比": round(total_wait_min / makespan, 4) if makespan > 0 else 0.0,
        "胎架累计空闲时间(min)": round(platen_idle_min, 4),
        "胎架累计空闲时间(h)": round(platen_idle_min / 60.0, 4),
        "整体产能(张板/班)": throughput_per_shift,
        # 稼动率指标组（对标竞赛要求）
        "切割机综合稼动率": overall_oee,           # 含全部切割相关时间 / 可用时间（对标 ≥61%）
        "切割机纯切割稼动率": pure_cut_oee,         # 仅直切+V坡 / 可用时间
        "切割机班次稼动率": shift_based_oee,         # 基于8h班制的OEE
        "总切割工时(min)": total_cut_minutes,
        "纯切割工时(min)": total_pure_cut,
        # 兼容旧版
        "切割机平均利用率": overall_oee,
        "坡口缓存区峰值占用率": float(bevel_peak / bevel_buffer_cap),
        "半框缓存区峰值占用率": float(half_peak / half_buffer_cap),
        "齐套缓存区峰值占用率": float(kit_peak / kit_buffer_cap),
        "坡口工作站峰值占用率": float(bevel_ws_peak / bevel_workstation_cap),
        # 死锁检测
        "死锁警告数": len(deadlock_warnings),
        "死锁详情": deadlock_warnings,
        # AGV路径冲突统计
        **agv_network.get_stats(),
        **per_machine_util,
        **{f"{m}码垛峰值占用率": float(min(1.0, machine_buffer_peaks.get(f"{m}码垛", 0) / max(1, buffer_cap_per_machine_map.get(m, 15)))) for m in all_machines},
        **resource_utils,
    }
    return new_schedule, complete, group, metrics, stages_df, buffer_timeseries


def simulate(
    schedule: pd.DataFrame,
    parts: pd.DataFrame,
    cfg: ModelConfig,
    pp: ProcessParams | None = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, float], pd.DataFrame, list]:
    """兼容入口：返回 (零件完成表, 齐套分组, 指标, 工序表, 缓存时序)。"""
    _, complete, groups, metrics, stages_df, buffer_timeseries = build_joint_schedule(
        schedule, parts, cfg, pp,
    )
    return complete, groups, metrics, stages_df, buffer_timeseries


# Competition target values for normalization (P3-3: 统一从 ModelConfig 引用)
_CMAX_TARGET = ModelConfig.CMAX_TARGET_H      # 67.25 hours
_KITSPAN_TARGET = ModelConfig.KITSPAN_TARGET_H  # 12.7 hours
_LOADDIFF_TARGET = ModelConfig.LOADDIFF_TARGET_H  # 1.0 hours


def unified_capacity_objective(
    metrics: Dict[str, float],
    cfg: ModelConfig,
    fifo: dict | None = None,
    objective_type: str | None = None,
) -> float:
    """产能优先统一目标函数（P4-1）。

    结构：J = Cmax/LB + eps * Secondary
    - Cmax/LB 是无量纲产能指标，不依赖绝对65h；
    - Secondary 只在线性/二次之间切换，且归一化后封顶到 [0,1]；
    - eps 默认0.009，保证产能差1%时产能优的解一定赢。
    """
    cmax = float(metrics["总完工时间(h)"])
    lb_h = float(metrics.get("理论下界(h)", 0.0) or 0.0)
    if lb_h <= 0:
        lb_h = float(getattr(cfg, "theoretical_lb_h", 0.0) or 0.0)
    if lb_h <= 0:
        lb_h = max(cmax, 1e-9)
    capacity_term = cmax / max(lb_h, 1e-9)

    kit = float(metrics["加权平均齐套跨度(h)"])
    load = float(metrics["切割负载差(h)"])
    wait = float(metrics.get("总等待时间(h)", 0.0))

    f_kit = float(fifo["加权平均齐套跨度(h)"]) if fifo and fifo.get("加权平均齐套跨度(h)") else None
    f_load = float(fifo["切割负载差(h)"]) if fifo and fifo.get("切割负载差(h)") else None
    f_wait = float(fifo["总等待时间(h)"]) if fifo and fifo.get("总等待时间(h)") else None
    if not f_kit or f_kit <= 0:
        f_kit = max(kit, 1e-9)
    if not f_load or f_load <= 0:
        f_load = max(load, 1e-9)
    if not f_wait or f_wait <= 0:
        f_wait = max(cmax, 1e-9)

    # 辅助指标统一归一化并封顶到 [0,1]，保证 eps 的1%阈值成立
    n_kit = min(1.0, kit / f_kit)
    n_load = min(1.0, load / f_load)
    n_wait = min(1.0, wait / f_wait)

    # P4-2：辅助指标权重优先读取扫描权重 obj_weight_*。
    # 原三权重和为1，因此等待权重取 1 - w_kit - w_load = w_cmax。
    _obj_kit = getattr(cfg, "obj_weight_kit", None)
    _obj_load = getattr(cfg, "obj_weight_load", None)
    _obj_cmax = getattr(cfg, "obj_weight_cmax", None)
    if _obj_kit is not None and _obj_load is not None and _obj_cmax is not None:
        w_kit = float(_obj_kit)
        w_load = float(_obj_load)
        w_wait = max(0.0, 1.0 - w_kit - w_load)
        _total = w_kit + w_load + w_wait
        if _total > 0:
            w_kit /= _total
            w_load /= _total
            w_wait /= _total
        else:
            w_kit = float(getattr(cfg, "secondary_weight_kit", 0.40))
            w_load = float(getattr(cfg, "secondary_weight_load", 0.25))
            w_wait = float(getattr(cfg, "secondary_weight_wait", 0.35))
    else:
        w_kit = float(getattr(cfg, "secondary_weight_kit", 0.40))
        w_load = float(getattr(cfg, "secondary_weight_load", 0.25))
        w_wait = float(getattr(cfg, "secondary_weight_wait", 0.35))
    if objective_type is None:
        objective_type = getattr(cfg, "objective_type", "linear")

    if objective_type == "quadratic":
        secondary = w_kit * n_kit * n_kit + w_load * n_load * n_load + w_wait * n_wait * n_wait
    else:
        secondary = w_kit * n_kit + w_load * n_load + w_wait * n_wait

    eps = float(getattr(cfg, "capacity_eps", 0.009))
    return capacity_term + eps * secondary


def select_best_by_capacity(
    candidates,
    cfg: ModelConfig,
    objective_type: str | None = None,
):
    """从候选解中按“产能优先 + 1%容差”选择最终解（P4-1）。

    先按 Cmax/LB 排序，只允许产能差在 capacity_tolerance_pct 内的解
    进入辅助指标比较，避免用齐套/等待去换产能。
    candidates: [(name, order, metrics), ...]
    """
    if not candidates:
        return None, None, None
    if objective_type is None:
        objective_type = getattr(cfg, "objective_type", "linear")

    def _cap(met):
        cmax = float(met["总完工时间(h)"])
        lb = float(met.get("理论下界(h)", 0.0) or 0.0)
        if lb <= 0:
            lb = float(getattr(cfg, "theoretical_lb_h", 0.0) or 0.0)
        if lb <= 0:
            lb = max(cmax, 1e-9)
        return cmax / max(lb, 1e-9)

    best_cap = min(_cap(m) for _n, _o, m in candidates)
    tol = float(getattr(cfg, "capacity_tolerance_pct", 0.01))
    bucket = [
        c for c in candidates
        if _cap(c[2]) <= best_cap * (1.0 + tol)
    ]
    best = min(
        bucket,
        key=lambda c: unified_capacity_objective(
            c[2], cfg, fifo=None, objective_type=objective_type
        ),
    )
    return best


def legacy_balanced_objective(
    metrics: Dict[str, float],
    cfg: ModelConfig,
    objective_type: str | None = None,
    fifo_cmax: float | None = None,
    fifo_kit: float | None = None,
    fifo_load: float | None = None,
) -> float:
    """原有“兼顾三个指标”评价函数（参考副本保留版）。

    linear 分支：原 SA 的 satisficing 加权 + Cmax/缓存/胎架软惩罚；
    quadratic 分支：原 FIFO 相对二次评价 + 胎架软惩罚。
    """
    if objective_type is None:
        objective_type = getattr(cfg, "objective_type", "linear")
    cmax = float(metrics["总完工时间(h)"])
    kit_span = float(metrics["加权平均齐套跨度(h)"])
    load_diff = float(metrics["切割负载差(h)"])

    if objective_type == "quadratic":
        f_cmax = fifo_cmax if fifo_cmax else max(cmax, 1.0)
        f_kit = fifo_kit if fifo_kit else max(kit_span, 1.0)
        f_load = fifo_load if fifo_load else max(load_diff, 0.5)
        c = cmax / max(f_cmax, 1e-9)
        k = kit_span / max(f_kit, 1e-9)
        l = load_diff / max(f_load, 1e-9)
        w_c = float(getattr(cfg, "obj_weight_cmax", 0.4))
        w_k = float(getattr(cfg, "obj_weight_kit", 0.4))
        w_l = float(getattr(cfg, "obj_weight_load", 0.2))
        alpha = float(getattr(cfg, "quadratic_penalty_cmax", 5.0))
        beta = float(getattr(cfg, "quadratic_penalty_kit", 5.0))
        gamma = float(getattr(cfg, "quadratic_penalty_load", 2.0))
        base = (
            w_c * c * c
            + w_k * k * k
            + w_l * l * l
            + alpha * max(0.0, c - 1.0) ** 2
            + beta * max(0.0, k - 1.0) ** 2
            + gamma * max(0.0, l - 1.0) ** 2
        )
    else:
        f_cmax = fifo_cmax if fifo_cmax else ModelConfig.CMAX_TARGET_H
        f_kit = fifo_kit if fifo_kit else max(kit_span + 1.0, 0.1)
        f_load = fifo_load if fifo_load else max(load_diff, 0.5)
        norm_cmax = cmax / max(f_cmax, 1.0)
        norm_load = load_diff / max(f_load, 0.1)
        kit_target = ModelConfig.KITSPAN_TARGET_H
        if kit_span <= kit_target:
            norm_kit = (kit_target / max(f_kit, 0.1)) * 0.5 + (kit_span / max(f_kit, 0.1)) * 0.5
        else:
            excess = (kit_span - kit_target) / kit_target
            norm_kit = (kit_span / max(f_kit, 0.1)) * (1.0 + excess)
        base = (
            float(getattr(cfg, "obj_weight_cmax", 0.40)) * norm_cmax
            + float(getattr(cfg, "obj_weight_kit", 0.40)) * norm_kit
            + float(getattr(cfg, "obj_weight_load", 0.20)) * norm_load
        )
        if cmax > ModelConfig.CMAX_TARGET_H:
            base += 5.0 * (cmax - ModelConfig.CMAX_TARGET_H) / ModelConfig.CMAX_TARGET_H
        if fifo_cmax and cmax > fifo_cmax:
            base += 2.0 * (cmax - fifo_cmax) / fifo_cmax
        buffer_penalty = 0.0
        for key, val in metrics.items():
            if "码垛峰值占用率" in key and isinstance(val, (int, float)):
                if val > 0.85:
                    buffer_penalty += (val - 0.85) * 0.30
        base += 0.05 * buffer_penalty

    idle_h = float(metrics.get("胎架累计空闲时间(h)", 0.0))
    idle_weight = float(getattr(cfg, "platen_idle_weight", 0.01))
    return base + idle_weight * idle_h


def select_best_by_legacy(
    candidates,
    cfg: ModelConfig,
    objective_type: str | None = None,
    fifo_cmax: float | None = None,
    fifo_kit: float | None = None,
    fifo_load: float | None = None,
):
    """从候选解中按原有“兼顾三指标”评价选择最优。"""
    if not candidates:
        return None, None, None
    best = min(
        candidates,
        key=lambda c: legacy_balanced_objective(
            c[2],
            cfg,
            objective_type=objective_type,
            fifo_cmax=fifo_cmax,
            fifo_kit=fifo_kit,
            fifo_load=fifo_load,
        ),
    )
    return best


def select_best_by_track(
    candidates,
    cfg: ModelConfig,
    objective_type: str | None = None,
    fifo_cmax: float | None = None,
    fifo_kit: float | None = None,
    fifo_load: float | None = None,
):
    """按 cfg.eval_track 选择：capacity=当前产能优先，balanced=原三指标。"""
    track = getattr(cfg, "eval_track", "capacity")
    if track == "balanced":
        return select_best_by_legacy(
            candidates, cfg, objective_type=objective_type,
            fifo_cmax=fifo_cmax, fifo_kit=fifo_kit, fifo_load=fifo_load,
        )
    return select_best_by_capacity(candidates, cfg, objective_type=objective_type)


def objective(metrics: Dict[str, float], cfg: ModelConfig | None = None) -> float:
    """Normalized multi-objective: Cmax (0.40) + KitSpan (0.40) + LoadDiff (0.20).
    All components normalized by competition targets. 胎架累计空闲时间作为软惩罚项。"""
    norm_cmax = metrics["总完工时间(h)"] / _CMAX_TARGET
    norm_kit = metrics["加权平均齐套跨度(h)"] / _KITSPAN_TARGET
    norm_load = metrics["切割负载差(h)"] / _LOADDIFF_TARGET
    base = 0.40 * norm_cmax + 0.40 * norm_kit + 0.20 * norm_load
    idle_h = float(metrics.get("胎架累计空闲时间(h)", 0.0))
    weight = float(cfg.platen_idle_weight) if cfg is not None else 0.01
    return base + weight * idle_h


def monte_carlo_robustness(
    plates: pd.DataFrame,
    parts: pd.DataFrame,
    cfg: ModelConfig,
    pp: ProcessParams | None = None,
    n_samples: int = 50,
    perturbation_pct: float = 0.10,
    seed: int = 20260723,
) -> dict:
    """Monte Carlo鲁棒性分析：对每个仿真在切割工时上添加随机扰动。

    对切割工时乘以 U(1-perturbation, 1+perturbation) 随机因子，
    运行 N 次独立仿真，收集KPI分布，输出均值和置信区间。

    Returns:
        dict with keys: "样本数", "KPI均值", "KPI标准差", "KPI_95CI下限", "KPI_95CI上限", "详细样本"
    """
    import time as time_mod
    from improved_optimizer import SATabuOptimizer

    t0 = time_mod.time()
    rng = np.random.default_rng(seed)

    features_df = plate_features(plates, parts, cfg, pp.speed_table if pp else None)
    # ── P1-3 修复：按列名匹配而非硬编码位置索引 ──
    _name_cands = [c for c in features_df.columns if '套料图名' in c]
    name_col = _name_cands[0] if _name_cands else features_df.columns[2]
    _seg_cands = [c for c in features_df.columns if '分段号' in c]
    seg_col = _seg_cands[0] if _seg_cands else features_df.columns[1]
    cut_cands = [c for c in features_df.columns if '工时' in c and 'min' in c]
    if not cut_cands:
        raise KeyError(f"未找到切割工时列，可用列: {list(features_df.columns)}")
    cut_col = cut_cands[0]
    pri_cands = [c for c in features_df.columns if '优先' in c or '最低' in c]
    if not pri_cands:
        raise KeyError(f"未找到优先级列，可用列: {list(features_df.columns)}")
    pri_col = pri_cands[0]

    # 初始解
    init_order = features_df.sort_values(
        [seg_col, pri_col, cut_col], ascending=[True, True, True], kind="stable"
    )[name_col].tolist()

    # 预收集KPI列表
    kpi_keys = ["总完工时间(h)", "加权平均齐套跨度(h)", "整体产能(张板/班)",
                "切割机综合稼动率", "切割机纯切割稼动率", "AGV路径冲突次数", "死锁警告数"]
    samples: dict[str, list[float]] = {k: [] for k in kpi_keys}

    print(f"[MonteCarlo] Running {n_samples} perturbed simulations (+/-{perturbation_pct:.0%})...")
    for i in range(n_samples):
        # 扰动：对切割工时添加随机因子
        perturbed = features_df.copy()
        factor = rng.uniform(1 - perturbation_pct, 1 + perturbation_pct, size=len(perturbed))
        perturbed[cut_col] = perturbed[cut_col] * factor

        # 创建临时优化器
        opt = SATabuOptimizer(perturbed, parts, cfg, pp, seed=seed + i * 1000)
        order, metrics, _stats = opt.optimize(
            init_order, max_iterations=min(40, n_samples), time_limit_seconds=5.0,
        )

        for k in kpi_keys:
            val = metrics.get(k, 0.0)
            if isinstance(val, (int, float, np.floating)):
                samples[k].append(float(val))
            elif isinstance(val, list):
                samples[k].append(float(len(val)))  # count items in list
            else:
                samples[k].append(0.0)

        if (i + 1) % 10 == 0:
            elapsed = time_mod.time() - t0
            cmax_mean = np.mean(samples["总完工时间(h)"])
            print(f"  [{i+1}/{n_samples}] Cmax mean={cmax_mean:.1f}h, elapsed={elapsed:.0f}s")

    # 统计汇总
    result = {"样本数": n_samples, "扰动幅度": f"+/-{perturbation_pct:.0%}",
              "运行时间(s)": round(time_mod.time() - t0, 1)}
    result["KPI均值"] = {}
    result["KPI标准差"] = {}
    result["KPI_95CI下限"] = {}
    result["KPI_95CI上限"] = {}
    result["详细样本"] = samples

    for k in kpi_keys:
        vals = np.array(samples[k])
        mean_v = float(np.mean(vals))
        std_v = float(np.std(vals))
        ci_lo = float(np.percentile(vals, 2.5))
        ci_hi = float(np.percentile(vals, 97.5))
        result["KPI均值"][k] = round(mean_v, 4)
        result["KPI标准差"][k] = round(std_v, 4)
        result["KPI_95CI下限"][k] = round(ci_lo, 4)
        result["KPI_95CI上限"][k] = round(ci_hi, 4)

    print(f"[MonteCarlo] Done. Cmax={result['KPI均值']['总完工时间(h)']:.2f}h "
          f"CI=[{result['KPI_95CI下限']['总完工时间(h)']:.1f}, {result['KPI_95CI上限']['总完工时间(h)']:.1f}]h")
    return result


def optimise(features: pd.DataFrame, parts: pd.DataFrame, cfg: ModelConfig, pp: ProcessParams | None = None) -> Tuple[pd.DataFrame, Dict[str, float], pd.DataFrame, pd.DataFrame]:
    """按 ModelConfig 选择 SA+Tabu 或 GA+LNS 进行齐套感知排产优化。

    评价函数也按 cfg.objective_type 选择线性或二次非线性。
    """
    method = getattr(cfg, "optimizer_method", "sa_tabu")
    objective_type = getattr(cfg, "objective_type", "linear")
    if method == "quadratic":  # 旧版兼容
        method = "sa_tabu"
        objective_type = "quadratic"

    if method == "ga_lns":
        from ga_lns_optimizer import run_ga_lns_optimization
        result = run_ga_lns_optimization(
            plates=pd.DataFrame(),
            parts=parts,
            features=features,
            cfg=cfg,
            pp=pp,
            speed_table=pp.speed_table if pp else None,
            checks={},
            iterations=cfg.local_search_iterations,
            objective_type=objective_type,
        )
    elif objective_type == "quadratic":
        from pareto_optimizer import run_quadratic_optimization
        result = run_quadratic_optimization(
            plates=pd.DataFrame(),
            parts=parts,
            features=features,
            cfg=cfg,
            pp=pp,
            speed_table=pp.speed_table if pp else None,
            checks={},
            iterations=cfg.local_search_iterations,
        )
    else:
        from improved_optimizer import run_multi_strategy_inline
        result = run_multi_strategy_inline(
            plates=pd.DataFrame(),  # not used by inline path — features has all needed data
            parts=parts,
            features=features,
            cfg=cfg,
            pp=pp,
            speed_table=pp.speed_table if pp else None,
            checks={},
            iterations=cfg.local_search_iterations,
        )
    schedule = result["schedule"]
    metrics = result["opt_metrics"]
    _, groups, _, stages, _ = simulate(schedule, parts, cfg, pp)
    return schedule, metrics, groups, stages


def _find_system_font() -> str | None:
    """跨平台中文字体查找：优先级 macOS > Windows > Linux > None"""
    import platform, os
    candidates = []
    system = platform.system()
    if system == "Darwin":
        candidates = [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
        ]
    elif system == "Windows":
        candidates = [
            os.path.expandvars(r"%SystemRoot%\Fonts\msyh.ttc"),
            os.path.expandvars(r"%SystemRoot%\Fonts\msyhbd.ttc"),
            os.path.expandvars(r"%SystemRoot%\Fonts\simhei.ttf"),
            os.path.expandvars(r"%SystemRoot%\Fonts\simsun.ttc"),
        ]
    else:  # Linux
        candidates = [
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
            "/usr/share/fonts/truetype/arphic/uming.ttc",
        ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None

def plot_outputs(schedule: pd.DataFrame, groups: pd.DataFrame, stages: pd.DataFrame, output: Path) -> None:
    # 使用 Pillow 生成无额外依赖的 PNG 图，中文字体缺失时仍可保留英文标签。
    font_path = _find_system_font()
    try:
        if font_path:
            title_font = ImageFont.truetype(font_path, 24); label_font = ImageFont.truetype(font_path, 17); small_font = ImageFont.truetype(font_path, 14)
        else:
            title_font = label_font = small_font = ImageFont.load_default()
    except OSError:
        title_font = label_font = small_font = ImageFont.load_default()

    # 动态颜色映射：支持N2/N5/N8...任意数量切割机
    machine_colors = ["#2E74B5", "#70AD47", "#8B5CF6", "#F59E0B", "#EF4444", "#06B6D4"]
    all_machines_in_sched = sorted(schedule["切割机"].unique())
    machine_color_map = {m: machine_colors[i % len(machine_colors)] for i, m in enumerate(all_machines_in_sched)}

    row_h = 65; margin_top = 80; margin_bottom = 60
    n_machines = len(all_machines_in_sched)
    gantt_height = margin_top + n_machines * row_h + margin_bottom
    width, left, right = 1280, 130, 50
    img = Image.new("RGB", (width, gantt_height), "white"); draw = ImageDraw.Draw(img)
    machine_label = "/".join(all_machines_in_sched)
    draw.text((left, 20), f"优化方案：{machine_label} 切割甘特图", fill="#17365D", font=title_font)
    max_time = schedule["切割完成(min)"].max() / 60
    for i, machine in enumerate(all_machines_in_sched):
        y = margin_top + i * row_h
        draw.text((40, y + 18), machine, fill="#222222", font=label_font)
        draw.line((left, y + 42, width - right, y + 42), fill="#B7C9D6", width=2)
        d = schedule[schedule["切割机"] == machine]
        color = machine_color_map.get(machine, "#2E74B5")
        for _, r in d.iterrows():
            x = left + (width - left - right) * (r["切割开始(min)"] / 60) / max_time
            w = max(2, (width - left - right) * (r["切割工时(min)"] / 60) / max_time)
            draw.rounded_rectangle((x, y, x + w, y + 38), radius=3, fill=color, outline="white")
    grid_bottom = margin_top + n_machines * row_h
    for h in range(0, math.ceil(max_time) + 1, max(1, math.ceil(max_time / 10))):
        x = left + (width - left - right) * h / max_time
        draw.line((x, margin_top - 10, x, grid_bottom), fill="#E6E6E6")
        draw.text((x - 8, grid_bottom + 5), str(h), fill="#555555", font=small_font)
    draw.text((width // 2 - 50, grid_bottom + 25), "时间（小时）", fill="#333333", font=label_font)
    img.save(output / "cutting_gantt.png")

    labels = (groups["分段号"].astype(str) + "-P" + groups["齐套优先级"].astype(str)).tolist()
    values = (groups["齐套跨度(min)"] / 60).tolist(); height = max(520, 65 + len(labels) * 32)
    img = Image.new("RGB", (1280, height), "white"); draw = ImageDraw.Draw(img)
    draw.text((35, 18), "各分段-优先级齐套组的等待跨度", fill="#17365D", font=title_font)
    vmax = max(values) or 1
    for i, (label, value) in enumerate(zip(labels, values)):
        y = 65 + i * 32; draw.text((35, y), label, fill="#333333", font=small_font)
        bar = 170 + 930 * value / vmax; draw.rectangle((170, y + 3, bar, y + 22), fill="#ED7D31")
        draw.text((bar + 8, y + 2), f"{value:.2f} h", fill="#555555", font=small_font)
    img.save(output / "kit_span.png")

    util = (stages.groupby("资源")["时长(min)"].sum().sort_values(ascending=False) / stages["结束(min)"].max() * 100)
    # P0-5 FIX: guard against empty stages (no downstream operations)
    names = util.index.tolist()
    if not names:
        # No downstream resource data — save a placeholder image
        img = Image.new("RGB", (1280, 400), "white")
        draw = ImageDraw.Draw(img)
        draw.text((35, 180), "No downstream resource data", fill="#888888", font=label_font)
        img.save(output / "resource_utilisation.png")
        return
    height = 480; img = Image.new("RGB", (1280, height), "white"); draw = ImageDraw.Draw(img)
    draw.text((35, 18), "后续资源利用率（默认参数场景）", fill="#17365D", font=title_font)
    # P0-5: names already validated above — guaranteed non-empty
    gap = 30; bw = min(100, (1180 - gap * (len(names) - 1)) / len(names)); base = 390
    for i, (name, value) in enumerate(util.items()):
        x = 55 + i * (bw + gap); top = base - 270 * value / 100
        draw.rectangle((x, top, x + bw, base), fill="#5B9BD5")
        draw.text((x, top - 22), f"{value:.1f}%", fill="#333333", font=small_font)
        draw.text((x, base + 12), name, fill="#333333", font=small_font)
    draw.line((45, base, 1230, base), fill="#888888", width=2)
    img.save(output / "resource_utilisation.png")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--speed-table", default=None, type=Path, help="附件3：工艺用时计算表.xlsx（可选）")
    parser.add_argument("--output", default=Path("outputs"), type=Path)
    args = parser.parse_args()
    out = args.output; out.mkdir(parents=True, exist_ok=True)
    cfg = ModelConfig()
    # 加载附件3工艺参数（若提供）
    pp = None
    speed_table = None
    has_speed_table = False
    if args.speed_table and args.speed_table.exists():
        pp = load_process_params(args.speed_table)
        speed_table = pp.speed_table
        has_speed_table = True
    plates, parts, checks = load_and_validate(args.input)
    features = plate_features(plates, parts, cfg, speed_table)

    # ── 构造式优化：不同排序策略 → 评估 → 选最优 ──
    # ── P1-3 修复：按列名匹配 ──
    _nc = [c for c in features.columns if '套料图名' in c]
    name_col = _nc[0] if _nc else features.columns[2]
    _sc = [c for c in features.columns if '分段号' in c]
    seg_col = _sc[0] if _sc else features.columns[1]
    pri_cands = [c for c in features.columns if '优先' in c or '最低' in c]
    if not pri_cands:
        raise KeyError(f"未找到优先级列，可用列: {list(features.columns)}")
    pri_col = pri_cands[0]
    cut_col = "切割工时(min)"

    def _eval_order(order_names):
        order_names = enforce_first_plate_not_long(order_names, features, cfg)
        ordered = features.set_index(name_col).loc[order_names].reset_index()
        _machines = machine_names(cfg)
        free = {m: 0.0 for m in _machines}
        worktables = {m: [0.0, 0.0] for m in _machines}
        gun_free = {m: 0.0 for m in _machines}
        last_table = {m: 1 for m in _machines}
        crane_pool = CranePool()
        rows = []
        for seq, (_, r) in enumerate(ordered.iterrows(), 1):
            m = pick_machine(r, free)
            raw_start, raw_empty_end, raw_loaded_end = _reserve_raw_material_crane(
                crane_pool, min(worktables[m]), cfg,
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
            en = st + r[cut_col]
            handling = float(r.get("工位处理时间(min)", 0.0))
            table_end = en + handling
            worktables[m][table_idx] = table_end
            gun_free[m] = en
            free[m] = max(gun_free[m], min(worktables[m]))
            rows.append({
                "套料图名": r[name_col], "分段号": r[seg_col],
                "切割机": m, "切割序号": seq,
                "切割开始(min)": st, "切割完成(min)": en,
                "切割工时(min)": r[cut_col], "最低优先级": r[pri_col],
                "原料吊运开始(min)": raw_start,
                "原料空驶完成(min)": raw_empty_end,
                "原料吊运完成(min)": raw_loaded_end,
                "原料吊运返回完成(min)": raw_loaded_end,
                "工位开始(min)": table_occ_start,
                "等待结束(min)": st,
                "工位处理时间(min)": handling,
                "工位完工(min)": table_end,
                "工位": table_idx,
            })
        sched = pd.DataFrame(rows)
        new_sched, _, _, met, _, _ = build_joint_schedule(sched, parts, cfg, pp)
        return new_sched, met

    # FIFO 基线
    seq_candidates = [c for c in features.columns if '序号' in c or 'seq' in c.lower()]
    seq_col = seq_candidates[0] if seq_candidates else features.columns[0]
    fifo_order = features.sort_values(seq_col, kind="stable")[name_col].tolist()
    base_schedule, base_metrics = _eval_order(fifo_order)
    _, base_groups, _, _, _ = simulate(base_schedule, parts, cfg, pp)

    # 尝试多个构造策略
    candidates = {
        "分段+优先级+短工时": features.sort_values([seg_col, pri_col, cut_col], ascending=[True, True, True], kind="stable")[name_col].tolist(),
        "分段+优先级+长工时": features.sort_values([seg_col, pri_col, cut_col], ascending=[True, True, False], kind="stable")[name_col].tolist(),
        "分段+短工时": features.sort_values([seg_col, cut_col], ascending=[True, True], kind="stable")[name_col].tolist(),
        "分段+长工时": features.sort_values([seg_col, cut_col], ascending=[True, False], kind="stable")[name_col].tolist(),
    }

    best_name, best_metrics, best_order = "", None, None
    best_obj = float("inf")
    for name, order in candidates.items():
        _, met = _eval_order(order)
        obj_val = objective(met, cfg)
        if obj_val < best_obj:
            best_obj, best_metrics, best_name, best_order = obj_val, met, name, order[:]

    schedule, opt_metrics = _eval_order(best_order)
    complete, groups, _, stages, _ = simulate(schedule, parts, cfg, pp)

    comparison = pd.DataFrame([{"方案": "FIFO基线", **base_metrics}, {"方案": f"构造优化({best_name})", **opt_metrics}])
    for col in ["总完工时间(h)", "加权平均齐套跨度(h)", "最大齐套跨度(h)", "切割负载差(h)"]:
        base_val = comparison.loc[0, col]
        opt_val = comparison.loc[1, col]
        comparison.loc[1, col + "改善率"] = (
            (base_val - opt_val) / base_val if base_val != 0 else 0.0
        )
    schedule.to_csv(out / "optimized_plate_schedule.csv", index=False, encoding="utf-8-sig")
    complete.to_csv(out / "part_completion.csv", index=False, encoding="utf-8-sig")
    groups.to_csv(out / "kit_groups.csv", index=False, encoding="utf-8-sig")
    stages.to_csv(out / "process_stages.csv", index=False, encoding="utf-8-sig")
    comparison.to_csv(out / "comparison.csv", index=False, encoding="utf-8-sig")
    plot_outputs(schedule, groups, stages, out)
    param_source = "附件3实测厚度相关速度表" if has_speed_table else "ModelConfig默认参数"
    summary = {"数据校验": checks, "工艺参数来源": param_source, "参数": asdict(cfg), "FIFO基线": base_metrics, "齐套感知优化": opt_metrics, "目标函数": "0.40*Cmax + 0.40*加权平均齐套跨度 + 0.20*切割负载差"}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
