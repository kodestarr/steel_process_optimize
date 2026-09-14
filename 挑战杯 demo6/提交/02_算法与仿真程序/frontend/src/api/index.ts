import axios from 'axios';
import type {
  UploadResponse,
  ModelParams,
  RunResponse,
  HistoryResponse,
} from '../types';

const api = axios.create({ baseURL: '/api', timeout: 0 });

export async function uploadExcel(file: File, speedFile?: File | null): Promise<UploadResponse> {
  const form = new FormData();
  form.append('file', file);
  if (speedFile) {
    form.append('speed_file', speedFile);
  }
  const { data } = await api.post<UploadResponse>('/upload', form);
  return data;
}

export async function runModel(
  fileId: string,
  params: Partial<ModelParams>,
  clientToken: string,
): Promise<RunResponse> {
  const { data } = await api.post<RunResponse>('/run', {
    file_id: fileId,
    params,
    client_token: clientToken,
  });
  return data;
}

export async function cancelRun(clientToken: string): Promise<void> {
  await api.post('/run/cancel', { client_token: clientToken });
}

export async function fetchHistory(): Promise<HistoryResponse> {
  const res = await fetch(`/api/history?_=${Date.now()}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

export async function fetchRunDetail(runId: string): Promise<RunResponse> {
  const res = await fetch(`/api/run/${runId}?_=${Date.now()}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json() as RunResponse;
  if (data.run_id !== runId) {
    throw new Error(`历史详情返回了错误的 run_id: ${data.run_id} != ${runId}`);
  }
  return data;
}

export async function deleteRun(runId: string): Promise<void> {
  await api.delete(`/history/${runId}`);
}

export async function renameRun(
  runId: string,
  name: string,
): Promise<void> {
  await api.put(`/history/${runId}/name`, { name });
}

export function reportUrl(runId: string, mode?: string): string {
  return `/api/report/${runId}${mode ? `?mode=${mode}` : ''}`;
}

export function downloadUrl(runId: string, filename: string): string {
  return `/api/download/${runId}/${filename}`;
}

// ── 动态响应机制 ──
export type DynamicStrategyMode =
  | 'auto'
  | 'right_shift'
  | 'partial_reoptimize'
  | 'full_reoptimize'
  | 'sa_tabu'
  | 'ga_lns'
  | 'dq_nsga2';

export interface FaultEventInput {
  id?: string;
  resource_id: string;
  start_time: string;
  duration: string;
  end_time?: string;
}

export interface OrderChangeInput {
  plate_name: string;
  action: 'cancel' | 'priority';
  priority?: number;
}

export interface DynamicResource {
  id: string;
  label: string;
  group: string;
  actual_ids: string[];
  description?: string;
}

export interface DynamicStateResponse {
  run_id: string;
  version: number;
  state_format_version?: number;
  legacy_dynamic_state?: boolean;
  decision_time: string;
  faults: Array<FaultEventInput & { status?: string }>;
  order_changes: OrderChangeInput[];
  order_change_time?: string;
  remaining_model?: RemainingModel;
  upload_mode?: 'order_change' | 'rush_insert';
  resource_catalog: DynamicResource[];
  metrics: Record<string, number>;
  has_dynamic_state: boolean;
  input_file?: string;
  input_filename?: string;
  input_file_id?: string;
  data_change_summary?: {
    added_plates?: string[];
    removed_plates?: string[];
    common_plates?: number;
  };
}

export interface DynamicReschedulePayload {
  run_id: string;
  plate_file_id?: string;
  speed_file_id?: string;
  faults: FaultEventInput[];
  order_changes: OrderChangeInput[];
  order_change_time?: string;
  in_process_policy: 'finish' | 'interrupt';
  clear_time: string;
  strategy_mode: DynamicStrategyMode;
  remaining_model?: RemainingModel;
  upload_mode?: 'order_change' | 'rush_insert';
  time_limit_s?: number;
}

export type RemainingModel = 'stable' | 'sa_tabu' | 'ga_lns' | 'dq_nsga2';

export interface DynamicRescheduleResponse {
  run_id: string;
  version: number;
  decision_time: string;
  strategy: Exclude<DynamicStrategyMode, 'auto'>;
  remainingModel: RemainingModel;
  uploadMode?: 'order_change' | 'rush_insert';
  strategy_reason: string;
  reschedule_time_s: number;
  breakdown_ms: {
    state_load_ms: number;
    greedy_repair_ms: number;
    search_ms: number;
    simulation_ms: number;
  };
  frozen_plates: number;
  reoptimized_plates: number;
  cancelled_plates: number;
  faults: Array<FaultEventInput & { status?: string; start_min?: number; end_min?: number }>;
  resource_catalog: DynamicResource[];
  order_changes: OrderChangeInput[];
  metrics: { optimized: Record<string, number> };
  comparisonData: import('../types').ComparisonRow[];
  baselineMetrics: Record<string, number>;
  makespanDeltaHours: number;
  makespanHours: number;
  ganttData: import('../types').GanttItem[];
  kitSpanData?: import('../types').KitSpanItem[];
  stagesData?: import('../types').StageItem[];
  utilizationData?: import('../types').UtilItem[];
  capacityWarnings: string[];
  searchStats: Record<string, string | number>;
  quickPlan: { makespanHours: number; changedPlates: number };
  dataChangeSummary: {
    added_plates: string[];
    removed_plates: string[];
    common_plates: number;
  };
  inputFile: {
    file_id?: string;
    filename?: string;
  };
  files: Record<string, string>;
  state: DynamicStateResponse;
}

export interface DynamicFirstPlate {
  name: string;
  machine: string;
  table: number;
  start: number;
  end: number;
  duration_min: number;
  duration_s: number;
}

export interface DynamicFirstBatch {
  names: string[];
  start: number;
  end: number;
  duration_s: number;
}

export interface DynamicJobStartResponse {
  job_id: string;
  status: 'running';
  phase: 'first_plate_ready';
  first_plate: DynamicFirstPlate;
  first_batch: DynamicFirstBatch;
  partialResult: {
    makespanHours: number;
    ganttData: import('../types').GanttItem[];
    stagesData: import('../types').StageItem[];
    kitSpanData: import('../types').KitSpanItem[];
    metrics: Record<string, number>;
  };
  partialFiles?: Record<string, string>;
  quickMakespanHours: number;
  decision_time: string;
  preview_time_s: number;
}

export interface DynamicJobStatusResponse {
  status: 'running' | 'cancelling' | 'completed' | 'failed' | 'cancelled';
  run_id: string;
  result?: DynamicRescheduleResponse;
  error?: string;
}

export interface DynamicUploadResponse {
  file_id: string;
  filename: string;
  plate_count: number;
  plate_options: Array<{
    name: string;
    section: string;
    priority: number;
    part_count: number;
  }>;
  validation: Record<string, string | number | boolean>;
  speed_file: {
    uploaded: boolean;
    file_id: string | null;
    filename: string | null;
    sheet_count: number;
  };
  parse_time_s: number;
}

export async function fetchDynamicState(runId: string): Promise<DynamicStateResponse> {
  const { data } = await api.get<DynamicStateResponse>(`/dynamic/state/${runId}`);
  return data;
}

export async function rescheduleDynamic(
  payload: DynamicReschedulePayload,
): Promise<DynamicRescheduleResponse> {
  const { data } = await api.post<DynamicRescheduleResponse>('/dynamic/reschedule', payload);
  return data;
}

export async function startDynamicReschedule(
  payload: DynamicReschedulePayload,
): Promise<DynamicJobStartResponse> {
  const { data } = await api.post<DynamicJobStartResponse>('/dynamic/start', payload);
  return data;
}

export async function fetchDynamicJob(jobId: string): Promise<DynamicJobStatusResponse> {
  const { data } = await api.get<DynamicJobStatusResponse>(`/dynamic/job/${jobId}`);
  return data;
}

export async function cancelDynamicJob(jobId: string): Promise<void> {
  await api.post(`/dynamic/cancel/${jobId}`);
}

export async function uploadDynamicData(
  runId: string,
  plateFile: File,
  speedFile?: File | null,
): Promise<DynamicUploadResponse> {
  const form = new FormData();
  form.append('plate_file', plateFile);
  if (speedFile) form.append('speed_file', speedFile);
  const { data } = await api.post<DynamicUploadResponse>(`/dynamic/upload/${runId}`, form);
  return data;
}

export async function clearDynamicUpload(runId: string): Promise<DynamicStateResponse> {
  const { data } = await api.post<DynamicStateResponse>(`/dynamic/clear-upload/${runId}`);
  return data;
}
