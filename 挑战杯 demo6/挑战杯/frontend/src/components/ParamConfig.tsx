import React from 'react';
import {
  Slider,
  InputNumber,
  Collapse,
  Row,
  Col,
  Divider,
  Space,
  Typography,
  Tag,
  Select,
  Tooltip,
} from 'antd';
import type { ModelParams, UploadResponse } from '../types';
import {
  SettingOutlined,
  ToolOutlined,
  ExperimentOutlined,
  ThunderboltOutlined,
  QuestionCircleOutlined,
} from '@ant-design/icons';

const { Title, Text } = Typography;

// ── 默认值 ──
export const DEFAULT_PARAMS: ModelParams = {
  cut_speed_mm_min: 1700,
  v_cut_speed_mm_min: 800,
  rapid_speed_mm_min: 24000,
  marking_speed_mm_min: 24000,
  pierce_minutes: 0,
  plate_setup_minutes: 3,
  cut_remainder_minutes: 0,
  small_sort_minutes: 1.58,
  small_sorters: 2,
  small_grind_speed_mm_min: 2520,
  large_grind_speed_mm_min: 1950,
  auto_bevel_speed_mm_min: 1000,
  manual_bevel_speed_mm_min: 250,
  small_transfer_minutes: 5,
  large_transfer_minutes: 5,
  small_truss_direct_palletize_minutes: 1.0,
  small_truss_palletize_minutes: 1.17,
  small_grind_scan_minutes: 1.0,
  auto_bevel_overhead_minutes: 3.5,
  n2_bevel_truss_minutes: 1.1667,
  small_grinders: 2,
  large_grinders: 2,
  auto_bevel_machines: 1,
  manual_bevel_stations: 1,
  agvs: 2,
  cutting_machines: 2,
  finish_buffer_capacity: 28,
  bevel_buffer_capacity: 3,
  half_buffer_capacity: 14,
  kit_buffer_capacity: 16,
  bevel_workstation_capacity: 7,
  kit_dwell_minutes: 60,
  truss_travel_minutes: 0.5,
  local_search_iterations: 260,
  random_seed: 20260723,
  optimizer_method: 'sa_tabu',
  objective_type: 'linear',
  ga_population_size: 16,
  ga_generations: 10,
  ga_crossover_rate: 0.85,
  ga_mutation_rate: 0.2,
  ga_tournament_size: 3,
  lns_destroy_ratio: 0.25,
  lns_iterations: 4,
  // P1-1: 暴露后端关键幽灵参数
  crane_overlap_minutes: 3.0,
  crane_return_ratio: 1.0,
  use_component_formula: 1,  // 1=分项明细公式：直切+V坡+空行程+划线（穿孔已折算）
  obj_weight_cmax: 0.40,
  obj_weight_kit: 0.40,
  obj_weight_load: 0.20,
};

interface Props {
  params: ModelParams;
  onChange: React.Dispatch<React.SetStateAction<ModelParams>>;
  upload?: UploadResponse | null;
}

const optimizerMethod = (params: ModelParams): string => params.optimizer_method || 'sa_tabu';

// ── 工具函数：渲染一个参数行 ──
function ParamRow({
  label,
  tooltip,
  value,
  min,
  max,
  step = 1,
  unit = '',
  onChange,
}: {
  label: string;
  tooltip?: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  unit?: string;
  onChange: (v: number) => void;
}) {
  const isFloat = step < 1;
  return (
    <Row align="middle" style={{ marginBottom: 12 }}>
      <Col span={8}>
        <Text title={tooltip} style={{ fontSize: 13 }}>
          {label}
        </Text>
      </Col>
      <Col span={10}>
        <Slider
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(v) => onChange(v as number)}
          style={{ marginRight: 12 }}
        />
      </Col>
      <Col span={6}>
        <InputNumber
          size="small"
          value={value}
          min={min}
          max={max}
          step={step}
          onChange={(v) => v !== null && onChange(v)}
          addonAfter={unit || undefined}
          style={{ width: '100%' }}
          stringMode={isFloat}
        />
      </Col>
    </Row>
  );
}

const ParamConfig: React.FC<Props> = ({ params, onChange, upload }) => {
  const set = <K extends keyof ModelParams>(key: K, val: ModelParams[K]) => {
    onChange((prev) => ({ ...prev, [key]: val }));
  };

  const resetAll = () => onChange({ ...DEFAULT_PARAMS });

  return (
    <div style={{ maxWidth: 900, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <Title level={4} style={{ margin: 0 }}>
          ⚙️ 模型参数配置
        </Title>
        <Space>
          {upload?.speed_table_uploaded && (
            <Tag color="orange" icon={<ThunderboltOutlined />}>附件3真实参数已启用</Tag>
          )}
          <Tag color="default" style={{ cursor: 'pointer' }} onClick={resetAll}>
            恢复默认值
          </Tag>
        </Space>
      </div>

      <Collapse
        defaultActiveKey={['optimization']}
        style={{ background: 'transparent' }}
        items={[
          {
            key: 'cut',
            label: (
              <Space>
                <ToolOutlined />
                <span>切割参数</span>
              </Space>
            ),
            children: (
              <>
                <ParamRow label="切割速度" tooltip="直线切割速度 (mm/min)" value={params.cut_speed_mm_min} min={500} max={5000} step={50} unit="mm/min" onChange={(v) => set('cut_speed_mm_min', v)} />
                <ParamRow label="V坡速度" tooltip="V坡口切割速度" value={params.v_cut_speed_mm_min} min={200} max={3000} step={50} unit="mm/min" onChange={(v) => set('v_cut_speed_mm_min', v)} />
                <ParamRow label="空行程速度" tooltip="快速移动速度" value={params.rapid_speed_mm_min} min={2000} max={30000} step={500} unit="mm/min" onChange={(v) => set('rapid_speed_mm_min', v)} />
                <ParamRow label="划线速度" value={params.marking_speed_mm_min} min={2000} max={20000} step={500} unit="mm/min" onChange={(v) => set('marking_speed_mm_min', v)} />

                <ParamRow label="换板准备时间" value={params.plate_setup_minutes} min={1} max={30} step={0.5} unit="min" onChange={(v) => set('plate_setup_minutes', v)} />
                <ParamRow label="残材断料时间" tooltip="双工位切割机下已并行处理，默认0；保留参数供特殊口径手动调回" value={params.cut_remainder_minutes} min={0} max={60} step={1} unit="min" onChange={(v) => set('cut_remainder_minutes', v)} />


              </>
            ),
          },
          {
            key: 'process',
            label: (
              <Space>
                <SettingOutlined />
                <span>加工参数</span>
              </Space>
            ),
            children: (
              <>
                <ParamRow label="桁架分拣时间" tooltip="桁架切割→打磨台，附件3：95s≈1.58min（不打磨件用路径B：60s）" value={params.small_sort_minutes} min={0.5} max={5} step={0.01} unit="min" onChange={(v) => set('small_sort_minutes', v)} />
                <ParamRow label="桁架直码时间" tooltip="桁架切割→码垛区（不打磨小件），附件3：60s=1.0min" value={params.small_truss_direct_palletize_minutes} min={0.5} max={5} step={0.01} unit="min" onChange={(v) => set('small_truss_direct_palletize_minutes', v)} />
                <ParamRow label="桁架码垛时间" tooltip="桁架打磨台→码垛区料框，附件3：70s≈1.17min" value={params.small_truss_palletize_minutes} min={0.5} max={5} step={0.01} unit="min" onChange={(v) => set('small_truss_palletize_minutes', v)} />
                <ParamRow label="打磨视觉扫描" tooltip="小件自动打磨的机器视觉扫描固定时间，附件3：1min" value={params.small_grind_scan_minutes} min={0.1} max={5} step={0.1} unit="min" onChange={(v) => set('small_grind_scan_minutes', v)} />
                <ParamRow label="小件打磨速度" value={params.small_grind_speed_mm_min} min={500} max={5000} step={100} unit="mm/min" onChange={(v) => set('small_grind_speed_mm_min', v)} />
                <ParamRow label="大件打磨速度" value={params.large_grind_speed_mm_min} min={300} max={3000} step={100} unit="mm/min" onChange={(v) => set('large_grind_speed_mm_min', v)} />
                <ParamRow label="自动坡口固定耗时" tooltip="视觉扫描2min+预热0.5min+翻面1min，附件3：3.5min" value={params.auto_bevel_overhead_minutes} min={0.5} max={10} step={0.1} unit="min" onChange={(v) => set('auto_bevel_overhead_minutes', v)} />
                <ParamRow label="自动坡口速度" value={params.auto_bevel_speed_mm_min} min={300} max={3000} step={100} unit="mm/min" onChange={(v) => set('auto_bevel_speed_mm_min', v)} />
                <ParamRow label="人工坡口速度" value={params.manual_bevel_speed_mm_min} min={200} max={2000} step={50} unit="mm/min" onChange={(v) => set('manual_bevel_speed_mm_min', v)} />


                <ParamRow label="大件转运时间" value={params.large_transfer_minutes} min={0.5} max={15} step={0.1} unit="min" onChange={(v) => set('large_transfer_minutes', v)} />
                <ParamRow label="N2至坡口桁架时间" tooltip="N2打磨后由桁架到坡口工作站，默认70s/件" value={params.n2_bevel_truss_minutes} min={0.1} max={5} step={0.1} unit="min" onChange={(v) => set('n2_bevel_truss_minutes', v)} />
              </>
            ),
          },
          {
            key: 'resource',
            label: (
              <Space>
                <ExperimentOutlined />
                <span>资源数量</span>
              </Space>
            ),
            children: (
              <>
                <ParamRow label="分拣桁架" value={params.small_sorters} min={1} max={5} step={1} unit="套" onChange={(v) => set('small_sorters', v)} />
                <ParamRow label="小件打磨机" value={params.small_grinders} min={1} max={10} step={1} unit="台" onChange={(v) => set('small_grinders', v)} />


                <ParamRow label="人工坡口工位" value={params.manual_bevel_stations} min={1} max={20} step={1} unit="个" onChange={(v) => set('manual_bevel_stations', v)} />
                <ParamRow label="切割机数量" tooltip="切割机数量（默认2=N2+N5，可加N8备机=3）" value={params.cutting_machines} min={2} max={5} step={1} unit="台" onChange={(v) => set('cutting_machines', v)} />


                <ParamRow label="成品缓存容量" value={params.finish_buffer_capacity} min={10} max={200} step={5} unit="件" onChange={(v) => set('finish_buffer_capacity', v)} />
                <ParamRow label="坡口缓存容量" tooltip="坡口工序独立缓存区容量（题目规定默认3）" value={params.bevel_buffer_capacity} min={1} max={100} step={1} unit="件" onChange={(v) => set('bevel_buffer_capacity', v)} />
                <ParamRow label="缓存区容量" tooltip="非坡口料框AGV到达后的暂存区容量（14框）" value={params.half_buffer_capacity} min={1} max={200} step={1} unit="框" onChange={(v) => set('half_buffer_capacity', v)} />
                <ParamRow label="坡口工作站容量" tooltip="坡口完成后待合盘/待转运的框数容量（7框）" value={params.bevel_workstation_capacity} min={1} max={100} step={1} unit="框" onChange={(v) => set('bevel_workstation_capacity', v)} />




              </>
            ),
          },
          {
            key: 'free',
            label: (
              <Space>
                <ToolOutlined /><span>自由参数</span>
                <Tooltip title="以下参数在题目文档中未明确给定，均为模型合理假设值">
                  <QuestionCircleOutlined style={{ color: '#94A3B8', cursor: 'help', fontSize: 13 }} />
                </Tooltip>
              </Space>
            ),
            children: (
              <>
                <ParamRow label="AGV数量" tooltip="产线中自动导引运输车数量，用于搬运料框" value={params.agvs} min={1} max={10} step={1} unit="台" onChange={(v) => set('agvs', v)} />
                <ParamRow label="AGV运输时间" tooltip="AGV单次搬运料框耗时，默认5min；空车返回与正向相同" value={params.small_transfer_minutes} min={0.5} max={20} step={0.1} unit="min" onChange={(v) => set('small_transfer_minutes', v)} />
                <ParamRow label="齐套缓存容量" tooltip="已齐套待取走料框的齐套缓存区容量（默认15框）" value={params.kit_buffer_capacity} min={5} max={500} step={5} unit="框" onChange={(v) => set('kit_buffer_capacity', v)} />
                <ParamRow label="齐套停留时间" tooltip="已齐套料框在齐套缓存区停留等待下游取走的时间" value={params.kit_dwell_minutes} min={0} max={300} step={5} unit="min" onChange={(v) => set('kit_dwell_minutes', v)} />
                <ParamRow label="桁架跨区时间" tooltip="桁架在N2/N5切割机之间移动耗时" value={params.truss_travel_minutes} min={0.1} max={5} step={0.1} unit="min" onChange={(v) => set('truss_travel_minutes', v)} />
                <ParamRow label="天车重叠时间" tooltip="天车上下料与残材断料可重叠的时间窗口" value={params.crane_overlap_minutes} min={0} max={10} step={0.5} unit="min" onChange={(v) => set('crane_overlap_minutes', v)} />
                <ParamRow label="天车空返比例" tooltip="天车不带钢板返回时间 = 载运时间 × 该比例，默认1.0（与正向相同）" value={params.crane_return_ratio} min={0} max={2} step={0.05} unit="倍" onChange={(v) => set('crane_return_ratio', v)} />
                <ParamRow label="大件打磨工位数" tooltip="大件人工打磨工位数量" value={params.large_grinders} min={1} max={20} step={1} unit="个" onChange={(v) => set('large_grinders', v)} />
              </>
            ),
          },
          {
            key: 'optimization',
            label: (
              <Space>
                <ThunderboltOutlined />
                <span>优化算法</span>
              </Space>
            ),
            children: (
              <>
                <Row align="middle" style={{ marginBottom: 12 }}>
                  <Col span={8}>
                    <Text style={{ fontSize: 13 }}>进化算法</Text>
                    <Tooltip title="SA+Tabu 和 GA+LNS 共用同一套仿真评估；GA+LNS 以遗传算法为主，并对精英解做 LNS 局部精修">
                      <QuestionCircleOutlined style={{ marginLeft: 4, color: '#94A3B8', cursor: 'help', fontSize: 13 }} />
                    </Tooltip>
                  </Col>
                  <Col span={16}>
                    <Select
                      key={params.optimizer_method}
                      size="small"
                      value={optimizerMethod(params)}
                      onChange={(v) => set('optimizer_method', String(v))}
                      style={{ width: '100%' }}
                      options={[
                        { value: 'sa_tabu', label: 'SA+Tabu' },
                        { value: 'ga_lns', label: 'GA+LNS' },
                      ]}
                    />
                  </Col>
                </Row>
                <Row align="middle" style={{ marginBottom: 12 }}>
                  <Col span={8}>
                    <Text style={{ fontSize: 13 }}>评价函数</Text>
                    <Tooltip title="线性评价使用竞赛目标导向的分段加权；二次评价使用FIFO相对二次函数">
                      <QuestionCircleOutlined style={{ marginLeft: 4, color: '#94A3B8', cursor: 'help', fontSize: 13 }} />
                    </Tooltip>
                  </Col>
                  <Col span={16}>
                    <Select
                      key={params.objective_type}
                      size="small"
                      value={params.objective_type}
                      onChange={(v) => set('objective_type', String(v))}
                      style={{ width: '100%' }}
                      options={[
                        { value: 'linear', label: '线性评价' },
                        { value: 'quadratic', label: '二次非线性评价' },
                      ]}
                    />
                  </Col>
                </Row>
                <ParamRow label="搜索迭代次数" tooltip="局部搜索迭代次数，越大越精细但越慢" value={params.local_search_iterations} min={50} max={1000} step={10} unit="次" onChange={(v) => set('local_search_iterations', v)} />
                <ParamRow label="随机种子" value={params.random_seed} min={1} max={99999999} step={1} onChange={(v) => set('random_seed', v)} />
                {params.optimizer_method === 'ga_lns' && (
                  <>
                    <ParamRow label="GA种群规模" tooltip="每代保留的钢板排列个体数" value={params.ga_population_size} min={4} max={64} step={2} unit="个" onChange={(v) => set('ga_population_size', v)} />
                    <ParamRow label="GA代数" value={params.ga_generations} min={1} max={50} step={1} unit="代" onChange={(v) => set('ga_generations', v)} />
                    <ParamRow label="LNS毁坏比例" tooltip="LNS每次移除的钢板比例，0.2~0.4较合适" value={params.lns_destroy_ratio} min={0.1} max={0.5} step={0.05} unit="比例" onChange={(v) => set('lns_destroy_ratio', v)} />
                  </>
                )}
                <Row align="middle" style={{ marginBottom: 12 }}>
                  <Col span={8}>
                    <Text style={{ fontSize: 13 }}>切割公式</Text>
                    <Tooltip title={<div style={{ maxWidth: 340, lineHeight: 1.7 }}>
                      <div style={{ fontWeight: 600, marginBottom: 4 }}>📐 附件3官方公式</div>
                      <div style={{ marginBottom: 8, opacity: 0.85 }}>(直切+V坡) ÷ 0.8 ÷ 0.6<br/>空行程、穿孔、易损件打包为折扣系数 ÷0.48</div>
                      <div style={{ fontWeight: 600, marginBottom: 4 }}>🔬 分项明细公式（当前默认）</div>
                      <div style={{ opacity: 0.85 }}>直切 + V坡 + 空行程 + 划线<br/>穿孔时间已折算进切割速度系数，不重复计入</div>
                    </div>}>
                      <QuestionCircleOutlined style={{ marginLeft: 4, color: '#94A3B8', cursor: 'help', fontSize: 13 }} />
                    </Tooltip>
                  </Col>
                  <Col span={16}>
                    <Select
                      size="small"
                      value={params.use_component_formula}
                      onChange={(v) => set('use_component_formula', v)}
                      style={{ width: '100%' }}
                      options={[
                        { value: 0, label: '📐 附件3官方公式' },
                        { value: 1, label: '🔬 分项明细公式（更精确）' },
                      ]}
                    />
                  </Col>
                </Row>
                <Divider plain style={{ fontSize: 13 }}>
                  目标函数权重
                </Divider>
                <ParamRow label="总完工时间 权重" tooltip="总完工时间在目标函数中的权重" value={params.obj_weight_cmax} min={0} max={1} step={0.01} onChange={(v) => set('obj_weight_cmax', v)} />
                <ParamRow label="齐套跨度 权重" tooltip="加权平均齐套跨度在目标函数中的权重" value={params.obj_weight_kit} min={0} max={1} step={0.01} onChange={(v) => set('obj_weight_kit', v)} />
                <ParamRow label="负载差 权重" tooltip="切割负载差在目标函数中的权重" value={params.obj_weight_load} min={0} max={1} step={0.01} onChange={(v) => set('obj_weight_load', v)} />
              </>
            ),
          },
        ]}
      />
    </div>
  );
};

export default ParamConfig;
