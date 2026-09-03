import simpy
import random
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from enum import Enum, auto
import numpy as np


class PartType(Enum):
    """部材类型"""
    SMALL_PART = auto()      # 小部材
    LARGE_PART = auto()      # 大部材
    SCRAP = auto()            # 残材
    REUSABLE = auto()         # 利材


class ProcessingRoute(Enum):
    """工艺路线类型"""
    N2_SMALL_AUTO = auto()           # N2小部材自动线（非坡口）
    N2_SMALL_BEVEL = auto()          # N2小部材坡口线
    N2_LARGE_GRIND = auto()          # N2大部材打磨线
    N2_LARGE_BEVEL = auto()          # N2大部材坡口线
    N5_SMALL_AUTO = auto()           # N5小部材自动线（非坡口）
    N5_SMALL_BEVEL = auto()          # N5小部材坡口线
    N5_LARGE_GRIND = auto()          # N5大部材打磨线
    N5_LARGE_BEVEL = auto()          # N5大部材坡口线


@dataclass
class SteelPlate:
    """钢板实体"""
    id: int
    env: simpy.Environment

    # 几何参数（典型钢板尺寸）
    length_mm: float = 6000.0
    width_mm: float = 2000.0
    thickness_mm: float = 20.0

    # 工艺长度参数
    straight_cut_length_mm: float = 0.0
    bevel_cut_length_mm: float = 0.0
    grinding_length_mm: float = 0.0
    beveling_length_mm: float = 0.0

    # 工艺属性
    part_type: Optional[PartType] = None
    route: Optional[ProcessingRoute] = None
    has_bevel: bool = False

    # 统计信息
    enter_time: float = field(default_factory=lambda: 0.0)
    exit_time: float = field(default_factory=lambda: 0.0)
    process_log: List[Dict] = field(default_factory=list)

    def log(self, station: str, action: str, duration: float = 0):
        self.process_log.append({
            'timestamp': self.env.now,
            'station': station,
            'action': action,
            'duration': duration,
            'plate_id': self.id
        })


class TransportManager:
    """搬运系统管理器"""
    def __init__(self, env: simpy.Environment):
        self.env = env
        self.agv_res = simpy.Resource(env, capacity=3)
        self.gantry_auto_res = simpy.Resource(env, capacity=2)
        self.gantry_manual_res = simpy.Resource(env, capacity=2)
        self.crane_res = simpy.Resource(env, capacity=2)

    def agv(self, plate: SteelPlate, from_loc: str, to_loc: str, 
            distance_m: float = 50, speed_m_min: float = 30):
        duration = distance_m / speed_m_min
        with self.agv_res.request() as req:
            yield req
            plate.log(f"AGV_{from_loc}_to_{to_loc}", "start", 0)
            yield self.env.timeout(duration)
            plate.log(f"AGV_{from_loc}_to_{to_loc}", "finish", duration)

    def gantry_manual(self, plate: SteelPlate, location: str, large: bool = False):
        duration = 20 if large else 5
        with self.gantry_manual_res.request() as req:
            yield req
            plate.log(f"GantryManual_{location}", "start", 0)
            yield self.env.timeout(duration)
            plate.log(f"GantryManual_{location}", "finish", duration)

    def gantry_auto(self, plate: SteelPlate, from_loc: str, to_loc: str):
        duration = random.uniform(1, 2)
        with self.gantry_auto_res.request() as req:
            yield req
            plate.log(f"GantryAuto_{from_loc}_to_{to_loc}", "start", 0)
            yield self.env.timeout(duration)
            plate.log(f"GantryAuto_{from_loc}_to_{to_loc}", "finish", duration)

    def crane(self, plate: SteelPlate, from_loc: str, to_loc: str, large: bool = False):
        duration = 5 if large else 3
        with self.crane_res.request() as req:
            yield req
            plate.log(f"Crane_{from_loc}_to_{to_loc}", "start", 0)
            yield self.env.timeout(duration)
            plate.log(f"Crane_{from_loc}_to_{to_loc}", "finish", duration)

    def manual_carry(self, plate: SteelPlate, location: str):
        duration = 3
        plate.log(f"Manual_{location}", "start", 0)
        yield self.env.timeout(duration)
        plate.log(f"Manual_{location}", "finish", duration)


class ProcessingStation:
    """加工工位基类"""
    def __init__(self, env: simpy.Environment, name: str, capacity: int = 1):
        self.env = env
        self.name = name
        self.machine = simpy.Resource(env, capacity=capacity)
        self.capacity = capacity
        self.process_count = 0
        self.total_busy_time = 0.0

    def process(self, plate: SteelPlate, duration: float):
        with self.machine.request() as req:
            yield req
            start = self.env.now
            plate.log(self.name, "start", 0)
            yield self.env.timeout(duration)
            plate.log(self.name, "finish", duration)
            self.process_count += 1
            self.total_busy_time += self.env.now - start


class CuttingMachine(ProcessingStation):
    """切割机"""
    def __init__(self, env: simpy.Environment, name: str, 
                 straight_speed: float = 500,
                 bevel_speed: float = 400,
                 pierce_eff: float = 0.8,
                 consumable_eff: float = 0.6):
        super().__init__(env, name, capacity=1)
        self.straight_speed = straight_speed
        self.bevel_speed = bevel_speed
        self.pierce_eff = pierce_eff
        self.consumable_eff = consumable_eff
        self.manual_feed = 3

    def calc_time(self, plate: SteelPlate) -> float:
        straight = plate.straight_cut_length_mm / self.straight_speed
        bevel = plate.bevel_cut_length_mm / self.bevel_speed
        total = (straight + bevel) / (self.pierce_eff * self.consumable_eff)
        return total + self.manual_feed

    def cut(self, plate: SteelPlate):
        yield from self.process(plate, self.calc_time(plate))


class GrindingStation(ProcessingStation):
    """打磨站"""
    def __init__(self, env: simpy.Environment, name: str, 
                 grind_speed: float = 42, scan_time: float = 1,
                 is_manual: bool = False, manual_speed: float = 1.95):
        super().__init__(env, name, capacity=1 if is_manual else 2)
        self.grind_speed = grind_speed  # mm/s for auto
        self.scan_time = scan_time
        self.is_manual = is_manual
        self.manual_speed = manual_speed  # m/min for manual

    def calc_time(self, plate: SteelPlate) -> float:
        if self.is_manual:
            return (plate.grinding_length_mm / 1000) / self.manual_speed
        else:
            grind = (plate.grinding_length_mm * 2) / (self.grind_speed * 60)
            return grind + self.scan_time

    def grind(self, plate: SteelPlate):
        yield from self.process(plate, self.calc_time(plate))


class BevelingStation(ProcessingStation):
    """坡口站"""
    def __init__(self, env: simpy.Environment, name: str,
                 bevel_speed: float = 250, scan_time: float = 2,
                 preheat: float = 0.5, flip: float = 1,
                 is_manual: bool = False, capacity: int = 7):
        super().__init__(env, name, capacity=1 if is_manual else capacity)
        self.bevel_speed = bevel_speed
        self.scan_time = scan_time
        self.preheat = preheat
        self.flip = flip
        self.is_manual = is_manual

    def calc_time(self, plate: SteelPlate) -> float:
        if self.is_manual:
            return plate.beveling_length_mm / self.bevel_speed
        else:
            auto_speed = 200  # mm/min assumed
            process = plate.beveling_length_mm / auto_speed
            return process + self.scan_time + self.preheat + self.flip

    def bevel(self, plate: SteelPlate):
        yield from self.process(plate, self.calc_time(plate))


class SortingStation:
    """分拣站"""
    def __init__(self, env: simpy.Environment, name: str):
        self.env = env
        self.name = name
        self.sort_time = 2

    def sort(self, plate: SteelPlate):
        plate.log(self.name, "start", 0)
        yield self.env.timeout(self.sort_time)
        # 根据面积判断大小
        area = plate.length_mm * plate.width_mm
        if area < 2_000_000:  # < 2m²
            plate.part_type = PartType.SMALL_PART
        else:
            plate.part_type = PartType.LARGE_PART
        plate.log(self.name, "finish", self.sort_time)
        return plate.part_type


class Buffer:
    """缓存区"""
    def __init__(self, env: simpy.Environment, name: str, capacity: int):
        self.env = env
        self.name = name
        self.capacity = capacity
        self.store = simpy.Store(env, capacity=capacity)
        self.put_count = 0
        self.get_count = 0
        self.max_occupied = 0

    def put(self, plate: SteelPlate):
        plate.log(self.name, "enter", 0)
        yield self.store.put(plate)
        self.put_count += 1
        self.max_occupied = max(self.max_occupied, len(self.store.items))

    def get(self):
        plate = yield self.store.get()
        plate.log(self.name, "leave", 0)
        self.get_count += 1
        return plate


class SteelPlant:
    """钢板加工车间"""

    def __init__(self, env: simpy.Environment):
        self.env = env
        self.transport = TransportManager(env)

        # 设备
        self.n2 = CuttingMachine(env, "N2切割机")
        self.n5 = CuttingMachine(env, "N5切割机")
        self.auto_grind = GrindingStation(env, "自由边打磨工作站", is_manual=False)
        self.manual_grind = GrindingStation(env, "胎架上打磨", is_manual=True)
        self.auto_bevel = BevelingStation(env, "坡口工作站", is_manual=False, capacity=7)
        self.manual_bevel = BevelingStation(env, "大部材人工坡口加工区", is_manual=True)

        # 分拣
        self.n2_sort = SortingStation(env, "N2分拣")
        self.n5_sort = SortingStation(env, "N5分拣")

        # 缓存区
        self.n2_rack = Buffer(env, "N2旁装框", 10)
        self.n5_rack = Buffer(env, "N5旁装框", 18)
        self.bevel_buf = Buffer(env, "坡口缓存区", 3)
        self.cache = Buffer(env, "缓存区", 14)
        self.small_area = Buffer(env, "小部材放置区", 16)
        self.large_area = Buffer(env, "大部材成品放置区", 999)

        # 统计
        self.plates: List[SteelPlate] = []
        self.completed: List[SteelPlate] = []
        self.scrap = 0
        self.reusable = 0
        self.route_counts = {r: 0 for r in ProcessingRoute}

    def generator(self, interval: float = 12):
        """钢板生成器"""
        pid = 0
        while True:
            yield self.env.timeout(random.expovariate(1.0/interval))

            # 随机生成钢板参数
            length = random.uniform(1500, 8000)
            width = random.uniform(800, 2500)

            plate = SteelPlate(
                id=pid, env=self.env,
                length_mm=length, width_mm=width,
                straight_cut_length_mm=random.uniform(300, length*0.8),
                bevel_cut_length_mm=random.uniform(0, length*0.3),
                grinding_length_mm=random.uniform(100, min(length, 3000)),
                beveling_length_mm=random.uniform(0, min(length*0.2, 800)),
                enter_time=self.env.now
            )
            plate.has_bevel = plate.bevel_cut_length_mm > 100

            self.plates.append(plate)

            if random.random() < 0.5:
                self.env.process(self.n2_line(plate))
            else:
                self.env.process(self.n5_line(plate))

            pid += 1

    def n2_line(self, plate: SteelPlate):
        """N2生产线"""
        yield from self.n2.cut(plate)
        ptype = yield from self.n2_sort.sort(plate)

        if ptype == PartType.SMALL_PART:
            yield from self.n2_small(plate)
        else:
            yield from self.n2_large(plate)

    def n5_line(self, plate: SteelPlate):
        """N5生产线"""
        yield from self.n5.cut(plate)
        ptype = yield from self.n5_sort.sort(plate)

        if ptype == PartType.SMALL_PART:
            yield from self.n5_small(plate)
        else:
            yield from self.n5_large(plate)

    def n2_small(self, plate: SteelPlate):
        """N2小部材路径"""
        # 30%概率为残材
        if random.random() < 0.3:
            yield from self.transport.gantry_manual(plate, "N2残材", large=False)
            yield from self.transport.agv(plate, "残材箱", "残材置场")
            self.scrap += 1
            plate.exit_time = self.env.now
            self.completed.append(plate)
            return

        yield from self.n2_rack.put(plate)

        if not plate.has_bevel:
            # 非坡口：装框→自动搬运→打磨→自动搬运→小部材放置区
            plate.route = ProcessingRoute.N2_SMALL_AUTO
            yield from self.n2_rack.get()
            yield from self.transport.gantry_auto(plate, "N2装框", "打磨站")
            yield from self.auto_grind.grind(plate)
            yield from self.transport.gantry_auto(plate, "打磨站", "小部材区")
            yield from self.small_area.put(plate)
            yield from self.small_area.get()
        else:
            # 坡口件：装框→AGV→坡口缓存区→坡口站→装筐→缓存区→AGV→小部材放置区
            plate.route = ProcessingRoute.N2_SMALL_BEVEL
            yield from self.n2_rack.get()
            yield from self.transport.agv(plate, "N2装框", "坡口缓存区")
            yield from self.bevel_buf.put(plate)
            yield from self.bevel_buf.get()
            yield from self.auto_bevel.bevel(plate)
            yield from self.transport.agv(plate, "坡口站", "装筐")
            yield from self.transport.agv(plate, "装筐", "缓存区")
            yield from self.cache.put(plate)
            yield from self.cache.get()
            yield from self.transport.agv(plate, "缓存区", "小部材区")
            yield from self.small_area.put(plate)
            yield from self.small_area.get()

        self.route_counts[plate.route] += 1
        plate.exit_time = self.env.now
        self.completed.append(plate)

    def n5_small(self, plate: SteelPlate):
        """N5小部材路径"""
        if random.random() < 0.3:
            yield from self.transport.gantry_manual(plate, "N5残材", large=False)
            yield from self.transport.agv(plate, "残材箱", "残材置场")
            self.scrap += 1
            plate.exit_time = self.env.now
            self.completed.append(plate)
            return

        yield from self.n5_rack.put(plate)

        if not plate.has_bevel:
            plate.route = ProcessingRoute.N5_SMALL_AUTO
            yield from self.n5_rack.get()
            yield from self.transport.gantry_auto(plate, "N5装框", "打磨站")
            yield from self.auto_grind.grind(plate)
            yield from self.transport.gantry_auto(plate, "打磨站", "小部材区")
            yield from self.small_area.put(plate)
            yield from self.small_area.get()
        else:
            plate.route = ProcessingRoute.N5_SMALL_BEVEL
            yield from self.n5_rack.get()
            yield from self.transport.agv(plate, "N5装框", "坡口缓存区")
            yield from self.bevel_buf.put(plate)
            yield from self.bevel_buf.get()
            yield from self.auto_bevel.bevel(plate)
            yield from self.transport.agv(plate, "坡口站", "装筐")
            yield from self.transport.agv(plate, "装筐", "缓存区")
            yield from self.cache.put(plate)
            yield from self.cache.get()
            yield from self.transport.agv(plate, "缓存区", "小部材区")
            yield from self.small_area.put(plate)
            yield from self.small_area.get()

        self.route_counts[plate.route] += 1
        plate.exit_time = self.env.now
        self.completed.append(plate)

    def n2_large(self, plate: SteelPlate):
        """N2大部材路径"""
        if random.random() < 0.2:
            yield from self.transport.gantry_manual(plate, "N2剩余", large=True)
            yield from self.transport.agv(plate, "利材箱", "利材堆场")
            self.reusable += 1
            plate.exit_time = self.env.now
            self.completed.append(plate)
            return

        yield from self.manual_grind.grind(plate)

        if not plate.has_bevel:
            plate.route = ProcessingRoute.N2_LARGE_GRIND
            yield from self.transport.crane(plate, "胎架", "成品区")
            yield from self.large_area.put(plate)
            yield from self.large_area.get()
            yield from self.transport.crane(plate, "成品区", "胎架")
        else:
            plate.route = ProcessingRoute.N2_LARGE_BEVEL
            yield from self.transport.crane(plate, "胎架", "人工坡口")
            yield from self.manual_bevel.bevel(plate)
            yield from self.transport.crane(plate, "人工坡口", "成品区")
            yield from self.large_area.put(plate)
            yield from self.large_area.get()

        self.route_counts[plate.route] += 1
        plate.exit_time = self.env.now
        self.completed.append(plate)

    def n5_large(self, plate: SteelPlate):
        """N5大部材路径"""
        if random.random() < 0.2:
            yield from self.transport.gantry_manual(plate, "N5剩余", large=True)
            yield from self.transport.agv(plate, "利材箱", "利材堆场")
            self.reusable += 1
            plate.exit_time = self.env.now
            self.completed.append(plate)
            return

        yield from self.manual_grind.grind(plate)

        if not plate.has_bevel:
            plate.route = ProcessingRoute.N5_LARGE_GRIND
            yield from self.transport.crane(plate, "胎架", "成品区")
            yield from self.large_area.put(plate)
            yield from self.large_area.get()
            yield from self.transport.crane(plate, "成品区", "胎架")
        else:
            plate.route = ProcessingRoute.N5_LARGE_BEVEL
            yield from self.transport.crane(plate, "胎架", "人工坡口")
            yield from self.manual_bevel.bevel(plate)
            yield from self.transport.crane(plate, "人工坡口", "成品区")
            yield from self.large_area.put(plate)
            yield from self.large_area.get()

        self.route_counts[plate.route] += 1
        plate.exit_time = self.env.now
        self.completed.append(plate)

    def stats(self) -> Dict:
        if not self.completed:
            return {}

        cycletimes = [p.exit_time - p.enter_time for p in self.completed]
        sim_time = self.env.now

        return {
            "total": len(self.plates),
            "completed": len(self.completed),
            "scrap": self.scrap,
            "reusable": self.reusable,
            "avg_cycle": np.mean(cycletimes),
            "max_cycle": np.max(cycletimes),
            "min_cycle": np.min(cycletimes),
            "n2_util": self.n2.total_busy_time / sim_time * 100,
            "n5_util": self.n5.total_busy_time / sim_time * 100,
            "auto_grind_util": self.auto_grind.total_busy_time / sim_time * 100,
            "manual_grind_util": self.manual_grind.total_busy_time / sim_time * 100,
            "auto_bevel_util": self.auto_bevel.total_busy_time / sim_time * 100,
            "manual_bevel_util": self.manual_bevel.total_busy_time / sim_time * 100,
            "n2_rack_max": self.n2_rack.max_occupied,
            "n5_rack_max": self.n5_rack.max_occupied,
            "bevel_buf_max": self.bevel_buf.max_occupied,
            "cache_max": self.cache.max_occupied,
            "routes": {k.name: v for k, v in self.route_counts.items()}
        }

    def get_log(self) -> pd.DataFrame:
        records = []
        for p in self.plates:
            for log in p.process_log:
                records.append({
                    'plate_id': p.id,
                    'route': p.route.name if p.route else 'UNKNOWN',
                    'part_type': p.part_type.name if p.part_type else 'UNKNOWN',
                    'has_bevel': p.has_bevel,
                    **log
                })
        return pd.DataFrame(records)


def run(sim_time: float = 480, interval: float = 12):
    env = simpy.Environment()
    plant = SteelPlant(env)
    env.process(plant.generator(interval))
    env.run(until=sim_time)
    return plant


if __name__ == "__main__":
    plant = run(sim_time=480, interval=12)
    stats = plant.stats()

    print("=" * 60)
    print("钢板加工车间仿真结果（8小时）")
    print("=" * 60)

    print(f"\n【生产统计】")
    print(f"总到达钢板: {stats['total']}")
    print(f"完成处理: {stats['completed']}")
    print(f"残材数量: {stats['scrap']}")
    print(f"利材数量: {stats['reusable']}")

    print(f"\n【周期时间（分钟）】")
    print(f"平均: {stats['avg_cycle']:.2f}")
    print(f"最大: {stats['max_cycle']:.2f}")
    print(f"最小: {stats['min_cycle']:.2f}")

    print(f"\n【设备利用率】")
    print(f"N2切割机: {stats['n2_util']:.1f}%")
    print(f"N5切割机: {stats['n5_util']:.1f}%")
    print(f"自动打磨站: {stats['auto_grind_util']:.1f}%")
    print(f"人工打磨站: {stats['manual_grind_util']:.1f}%")
    print(f"自动坡口站: {stats['auto_bevel_util']:.1f}%")
    print(f"人工坡口站: {stats['manual_bevel_util']:.1f}%")

    print(f"\n【缓存区最大占用】")
    print(f"N2旁装框: {stats['n2_rack_max']}/10")
    print(f"N5旁装框: {stats['n5_rack_max']}/18")
    print(f"坡口缓存区: {stats['bevel_buf_max']}/3")
    print(f"缓存区: {stats['cache_max']}/14")

    print(f"\n【工艺路线分布】")
    for route, count in stats['routes'].items():
        if count > 0:
            print(f"  {route}: {count}")

    df = plant.get_log()
    df.to_csv("process_log.csv", index=False, encoding="utf-8-sig")
    print(f"\n✅ 工艺日志已保存至 process_log.csv（共{len(df)}条记录）")
