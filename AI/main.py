from steel_plant_simulation import run

# 运行8小时仿真，每12分钟到达一张钢板
plant = run(sim_time=480, interval=12)

# 获取统计结果
stats = plant.stats()

# 导出工艺日志
df = plant.get_log()
df.to_csv("report.csv", index=False)