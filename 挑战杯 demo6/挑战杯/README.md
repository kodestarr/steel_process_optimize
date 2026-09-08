# 钢板齐套感知排产模型

本项目将附件2钢板零件数据转化为可复现的齐套感知多资源排产模型，支持附件3厚度相关工艺参数，包含 Web 可视化前端。

## 快速启动

双击项目根目录下的 **`start.bat`**，自动完成依赖安装、前端构建并启动服务，浏览器打开 `http://127.0.0.1:8000`。

## 功能概览

### Web 前端

| 功能 | 说明 |
|---|---|
| 📂 数据导入 | 上传附件2（必选）+ 附件3工艺参数表（可选），自动校验 Sheet 和列名 |
| ⚙️ 参数配置 | 切割速度、打磨速度、坡口速度、设备数量、迭代次数等 21 项参数，支持标准/高速/保守三档预设 |
| 📈 结果概览 | FIFO 基线 vs 优化方案对比，KPI 卡片带历史比对高亮，工艺参数来源标识 |
| 🗓️ 切割甘特图 | N2/N5 双机位切割时间轴 |
| 📦 齐套分析 | 各分段-优先级组的首件到达→齐套完成时间区间图 |
| 🔧 设备利用率 | 全资源（打磨、坡口、AGV）利用率柱状图 + 明细表 |
| 🎬 排产回放 | 全流程动画：播放/暂停/倍速/拖拽，10 个资源 2981 道工序按时间轴动态呈现 |
| 📜 运行历史 | 侧边栏历史记录，支持排序（时间/完工/跨度/负载/利用率/综合评分升序降序）、固定置顶、重命名、删除 |
| 📥 导出 | Word 建模报告 / CSV 明细 / PNG 图表一键下载 |

### 核心模型

- 数据一致性校验（钢板-零件关联、数量汇总、V坡长度）
- N2/N5 两台切割机的钢板分配与排序
- 小件自动工序、大件人工工序、AGV 转运和有限成品缓存的离散事件仿真
- FIFO 基线 + 齐套感知局部搜索优化
- 进化算法可选 `SA+Tabu`、`GA+LNS`、`DQN+NSGA-II`；后两者共用现有仿真评估
- `DQN+NSGA-II` 为多目标 Pareto 搜索，单次运行输出“重工时”和“兼顾三指标”两套交付解
- 附件3 厚度相关切割速度表自动查表 + 线性插值
- 优化目标：`0.48×Cmax + 0.37×平均齐套跨度 + 0.15×切割负载差`

### DQN 模型离线训练（可选）

网页运行时如果没有 `dqn_machine_model.pt`，会自动退回启发式机器码种子。需要训练真实 DQN 时：

```powershell
python train_dqn_nsga2.py --output dqn_machine_model.pt
```

脚本默认使用 `D:\作文\学校\项目\2026擂台赛\产线场景描述\附件2：钢板零件数据.xlsx` 和 `附件3：工艺用时计算表.xlsx`；如果文件在其他位置，可用 `--plate-file` 与 `--speed-file` 指定。训练环境需要 PyTorch CPU，可执行 `pip install -r backend/requirements-dqn.txt`。

## 一键运行
- 点击start.bat



## 命令行运行

```powershell
$py = 'C:\Users\22086\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'

# 仅附件2（默认恒定速度）
& $py steel_schedule_model.py --input "附件2：钢板零件数据.xlsx" --output outputs

# 附件2 + 附件3（厚度相关真实速度）
& $py steel_schedule_model.py --input "附件2：钢板零件数据.xlsx" --speed-table "附件3：工艺用时计算表.xlsx" --output outputs

# 生成 Word 报告
& $py build_report.py --results outputs --output 建模报告.docx
```

## 项目结构

```
├── start.bat                    # 一键启动
├── start.py                    # 启动脚本（安装依赖 + 构建前端 + 启动服务）
├── steel_schedule_model.py   # 核心模型
├── build_report.py           # Word 报告生成器
├── data/                     # 原始数据（附件2 + 附件3）
├── backend/
│   ├── main.py              # FastAPI 后端
│   ├── requirements.txt
│   ├── uploads/             # 上传文件
│   └── results/             # 运行结果
└── frontend/
    ├── src/
    │   ├── App.tsx          # 主布局
    │   ├── components/
    │   │   ├── DataImport.tsx       # 双文件上传
    │   │   ├── ParamConfig.tsx      # 参数配置 + 预设
    │   │   ├── ResultOverview.tsx   # 结果概览
    │   │   ├── GanttChart.tsx       # 切割甘特图
    │   │   ├── KitAnalysis.tsx      # 齐套分析
    │   │   ├── UtilCharts.tsx       # 设备利用率
    │   │   ├── ProcessAnimation.tsx # 全流程回放
    │   │   ├── HistorySidebar.tsx   # 历史记录（排序+固定）
    │   │   └── LoadingPipeline.tsx  # 加载动画
    │   ├── api/index.ts     # API 客户端
    │   └── types/index.ts   # TypeScript 类型
    └── dist/                # 构建产物（自动生成）
```

## 模型架构

- 有待补充

## 重要口径

- 附件3的速度表、设备数量、AGV行驶时间和缓存容量未完整提供时，参数回退到 ModelConfig 默认值
- 大件人工坡口长度按补充说明计算为 `Y坡 + X坡 + K坡`；I坡不计入人工坡口长度
- 当前结果用于验证模型逻辑和比较调度方案，不能直接宣称为企业现场绝对工时
- 上传附件3后切割速度按钢板厚度自动查表（54档，6mm~50mm），未覆盖厚度采用线性插值
