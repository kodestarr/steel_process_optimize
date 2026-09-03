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
): Promise<RunResponse> {
  const { data } = await api.post<RunResponse>('/run', {
    file_id: fileId,
    params,
  });
  return data;
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

export function reportUrl(runId: string): string {
  return `/api/report/${runId}`;
}

export function downloadUrl(runId: string, filename: string): string {
  return `/api/download/${runId}/${filename}`;
}

// ── 动态重调度 ──
export interface ReschedulePayload {
  run_id: string;
  scenario: 'machine_failure' | 'rush_order' | 'reoptimize';
  fault_time_h?: number;
  fault_machine?: string;
  fault_duration_h?: number;
  rush_plates?: string[];
  time_limit_s?: number;
}

export interface RescheduleResponse {
  scenario: string;
  reschedule_time_s: number;
  frozen_plates: number;
  reoptimized_plates: number;
  metrics: { optimized: Record<string, number> };
  makespanHours: number;
  ganttData: import('../types').GanttItem[];
  stagesData?: import('../types').StageItem[];
  utilizationData?: import('../types').UtilItem[];
  bufferTimeseries?: import('../types').BufferTimeseriesEntry[];
  stats: string;
}

export async function rescheduleModel(
  payload: ReschedulePayload,
): Promise<RescheduleResponse> {
  const { data } = await api.post<RescheduleResponse>('/reschedule', payload);
  return data;
}

// ── 优化进度轮询（P2-3: 真实进度替代伪进度定时器）──
export interface ProgressResponse {
  status: 'running' | 'completed' | 'not_found';
  progress_pct: number;
  iteration?: number;
  max_iterations?: number;
  current_obj?: number;
  best_obj?: number;
  temperature?: number;
}

export async function fetchProgress(runId: string): Promise<ProgressResponse> {
  const { data } = await api.get<ProgressResponse>(`/run/progress/${runId}`);
  return data;
}
