// ── 上传 ──
export interface UploadResponse {
  file_id: string;
  filename: string;
  validation: Record<string, string | number | boolean>;
  summary: {
    钢板数: number;
    零件数: number;
    齐套组数: number;
    分段数: number;
    套料图数: number;
  };
  speed_table_uploaded: boolean;
  speed_sheet_count: number;
}

// ── 参数 ──
export interface ModelParams {
  cut_speed_mm_min: number;
  v_cut_speed_mm_min: number;
  rapid_speed_mm_min: number;
  marking_speed_mm_min: number;
  pierce_minutes: number;
  plate_setup_minutes: number;
  cut_remainder_minutes: number;
  small_sort_minutes: number;
  small_sorters: number;
  small_grind_speed_mm_min: number;
  large_grind_speed_mm_min: number;
  auto_bevel_speed_mm_min: number;
  manual_bevel_speed_mm_min: number;
  small_transfer_minutes: number;
  large_transfer_minutes: number;
  small_grinders: number;
  large_grinders: number;
  auto_bevel_machines: number;
  manual_bevel_stations: number;
  agvs: number;
  cutting_machines: number;
  finish_buffer_capacity: number;
  bevel_buffer_capacity: number;
  half_buffer_capacity: number;
  kit_buffer_capacity: number;
  bevel_workstation_capacity: number;
  kit_dwell_minutes: number;
  truss_travel_minutes: number;
  local_search_iterations: number;
  random_seed: number;
  optimizer_method: string;  // 'sa_tabu' | 'ga_lns'
  objective_type: string;    // 'linear' | 'quadratic'
  ga_population_size: number;
  ga_generations: number;
  ga_crossover_rate: number;
  ga_mutation_rate: number;
  ga_tournament_size: number;
  lns_destroy_ratio: number;
  lns_iterations: number;
  // P1-1: 暴露后端关键幽灵参数
  crane_overlap_minutes: number;
  crane_return_ratio: number;
  use_component_formula: number;  // 1=分项明细公式：直切+V坡+空行程+划线（穿孔已折算）
  // 目标函数权重
  obj_weight_cmax: number;
  obj_weight_kit: number;
  obj_weight_load: number;
  small_truss_direct_palletize_minutes: number;
  small_truss_palletize_minutes: number;
  small_grind_scan_minutes: number;
  auto_bevel_overhead_minutes: number;
  n2_bevel_truss_minutes: number;
}

// ── 运行结果 ──
export interface GanttItem {
  name: string;
  machine: string;
  start: number;
  end: number;
  duration: number;
  section: string;
  priority: number;
  table?: number;
  tableEnd?: number;
  tableStart?: number;
  waitEnd?: number;
}

export interface KitSpanItem {
  label: string;
  partCount: number;
  firstArrival: number;
  completion: number;
  span: number;
}

export interface UtilItem {
  name: string;
  totalHours: number;
  utilization: number;
}

export interface StageItem {
  part: string;
  stage: string;
  resource: string;
  start: number;
  end: number;
  plate?: string;
}

export interface MetricsMap {
  [key: string]: number;
}

export interface ComparisonRow {
  方案: string;
  [key: string]: string | number;
}

export interface BufferTimeseriesEntry {
  time_h: number;
  [key: string]: number;  // "N2_码垛占用", "N5_码垛占用", "坡口缓存占用", "齐套缓存占用", ...
}

export interface BufferCapacityConfig {
  machine_caps: Record<string, number>;  // {"N2": 10, "N5": 18, ...}
  bevel_capacity: number;
  half_capacity: number;
  kit_capacity: number;
}

export interface RunResponse {
  run_id: string;
  algorithmName?: string;
  metrics: {
    fifo: MetricsMap;
    optimized: MetricsMap;
    comparison: ComparisonRow[];
  };
  makespanHours: number;
  ganttData: GanttItem[];
  kitSpanData: KitSpanItem[];
  utilizationData: UtilItem[];
  stagesData: StageItem[];
  bufferTimeseries?: BufferTimeseriesEntry[];
  bufferConfig?: BufferCapacityConfig;  // 后端返回的真实缓存区容量配置
  checks: Record<string, string | number | boolean>;
  files: {
    schedule_csv: string;
    completion_csv: string;
    kit_csv: string;
    stages_csv: string;
    comparison_csv: string;
    gantt_png: string;
    kit_png: string;
    util_png: string;
  };
}

export interface HistoryItem {
  id: string;
  name: string;
  filename: string;
  created_at: string;
  params: Partial<ModelParams>;
  base_metrics?: MetricsMap;
  score?: number;
  algorithm_name?: string;
  optimized_metrics: MetricsMap;
}

export interface HistoryResponse {
  runs: HistoryItem[];
}
