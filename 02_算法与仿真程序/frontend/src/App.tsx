import React, { useState, useCallback, useMemo, useEffect, useRef } from 'react';
import {
  Tabs,
  Button,
  Space,
  Segmented,
  Tag,
  Dropdown,
  message,
  Modal,
  Typography,
  ConfigProvider,
} from 'antd';
import {
  PlayCircleOutlined,
  DownloadOutlined,
  FileWordOutlined,
  FileExcelOutlined,
  FileImageOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  BookOutlined,
} from '@ant-design/icons';
import type { UploadResponse, ModelParams, RunResponse } from './types';
import { DEFAULT_PARAMS } from './components/ParamConfig';
import DataImport from './components/DataImport';
import ParamConfig from './components/ParamConfig';
import ResultOverview from './components/ResultOverview';
import GanttChart from './components/GanttChart';
import KitAnalysis from './components/KitAnalysis';
import UtilCharts from './components/UtilCharts';

import ReschedulePanel from './components/ReschedulePanel';
import HistorySidebar from './components/HistorySidebar';
import LoadingPipeline from './components/LoadingPipeline';
import ModelArchitecture from './components/ModelArchitecture';
import { runModel, cancelRun, reportUrl, downloadUrl } from './api';
import { COLORS, FONT } from './theme';

import './App.css';

const { Text } = Typography;

function formatDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.round(totalSeconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rs = s % 60;
  if (m < 60) return `${m}m ${rs}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m ${rs}s`;
}

function normalizeRunDuration(value: number | null | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

const App: React.FC = () => {
  const [upload, setUpload] = useState<UploadResponse | null>(null);
  const [params, setParams] = useState<ModelParams>({ ...DEFAULT_PARAMS });
  const [result, setResult] = useState<RunResponse | null>(null);
  const [reportMode, setReportMode] = useState<'capacity' | 'balanced'>('capacity');
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [lastRunSeconds, setLastRunSeconds] = useState<number | null>(null);
  const runStartRef = useRef(0);
  const runTokenRef = useRef<string | null>(null);
  const mountedRef = useRef(true);  // P0-5: 组件卸载标记，防止异步操作写已卸载组件
  const [activeTab, setActiveTab] = useState('import');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [prevResult, setPrevResult] = useState<RunResponse | null>(null);
  const [dynamicRunning, setDynamicRunning] = useState(false);
  const [dynamicGantt, setDynamicGantt] = useState<RunResponse['ganttData'] | null>(null);
  const [dynamicStages, setDynamicStages] = useState<RunResponse['stagesData'] | null>(null);
  const [dynamicMakespan, setDynamicMakespan] = useState<number | null>(null);
  const [dynamicFiles, setDynamicFiles] = useState<Record<string, string> | null>(null);
  // Dark mode removed — system always uses light industrial theme

  const handleUploaded = useCallback((res: UploadResponse) => {
    setUpload(res);
    setResult(null);
    setPrevResult(null);
    setActiveRunId(null);
    setDynamicRunning(false);
    setDynamicGantt(null);
    setDynamicStages(null);
    setDynamicMakespan(null);
    setDynamicFiles(null);
    setLastRunSeconds(null);
    setActiveTab('params');
  }, []);

  const handleRun = useCallback(async () => {
    if (!upload) {
      message.warning('请先上传数据文件');
      setActiveTab('import');
      return;
    }
    setLoading(true);
    mountedRef.current = true;  // P0-5: 每次新运行时重置标记
    runStartRef.current = Date.now();

    const clientToken = crypto.randomUUID();
    runTokenRef.current = clientToken;
    const runPromise = runModel(upload.file_id, params, clientToken);

    try {
      const res = await runPromise;
      if (!mountedRef.current) return;
      setPrevResult(result);
      setResult(res);
      setReportMode('capacity');
      setActiveRunId(res.run_id);
      setDynamicRunning(false);
      setActiveTab('overview');
      setLastRunSeconds(
        normalizeRunDuration(res.run_duration_s)
          ?? (Date.now() - runStartRef.current) / 1000,
      );
      message.success('计算完成！');
    } catch (e: any) {
      if (!mountedRef.current) return;
      const msg = e?.response?.data?.detail ?? e?.message ?? '运行失败';
      Modal.error({ title: '运行失败', content: msg });
    } finally {
      if (!mountedRef.current) return;
      setLoading(false);
      runTokenRef.current = null;
    }
  }, [upload, params, result]);

  const handleCancelRun = useCallback(async () => {
    const token = runTokenRef.current;
    if (!token) return;
    try {
      await cancelRun(token);
      message.info('已发出紧急中断请求');
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? e?.message ?? '中断失败');
    } finally {
      setLoading(false);
      runTokenRef.current = null;
    }
  }, []);

  // Cleanup progress timer and mounted flag on unmount
  useEffect(() => {
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const handleHistorySelect = useCallback((runId: string, data: RunResponse) => {
    setPrevResult(result);
    setResult(data);
    setReportMode(data.reportMode === 'balanced' ? 'balanced' : 'capacity');
    setActiveRunId(runId);
    setDynamicRunning(false);
    setActiveTab('overview');
    setLastRunSeconds(normalizeRunDuration(data.run_duration_s));
  }, [result]);

  const handleReportModeChange = useCallback((mode: any) => {
    const reports = (result as any)?.reports;
    if (!reports || (mode !== 'capacity' && mode !== 'balanced')) return;
    const payload = reports[mode];
    if (!payload) return;
    const rid = result?.run_id ?? '';
    setResult((prev) => ({
      ...(prev ?? result),
      ...payload,
      run_id: prev?.run_id ?? rid,
      reportMode: mode,
      reports: (prev as any)?.reports ?? reports,
    }));
    setReportMode(mode);
  }, [result]);

  const downloadItems = useMemo(() => {
    if (!result) return [];
    return [
      { key: 'report', label: '📄 Word 建模报告', icon: <FileWordOutlined /> },
      { type: 'divider' as const },
      { key: 'schedule', label: '钢板排程 CSV', icon: <FileExcelOutlined /> },
      { key: 'completion', label: '零件完成 CSV', icon: <FileExcelOutlined /> },
      { key: 'kit', label: '齐套组 CSV', icon: <FileExcelOutlined /> },
      { key: 'stages', label: '工序明细 CSV', icon: <FileExcelOutlined /> },
      { key: 'comparison', label: '方案对比 CSV', icon: <FileExcelOutlined /> },
      { type: 'divider' as const },
      { key: 'gantt_img', label: '切割甘特图 PNG', icon: <FileImageOutlined /> },
      { key: 'kit_img', label: '齐套分析图 PNG', icon: <FileImageOutlined /> },
      { key: 'util_img', label: '设备利用率图 PNG', icon: <FileImageOutlined /> },
    ];
  }, [result]);

    const handleDownload = useCallback((key: string) => {
    if (!result) return;
    try {
      if (key === 'report') {
        window.open(reportUrl(result.run_id, result.reportMode), '_blank');
      } else if (key.endsWith('_img')) {
        const activeFiles = dynamicFiles ?? result.files;
        const imgMap: Record<string, string> = {
          gantt_img: activeFiles.gantt_png,
          kit_img: activeFiles.kit_png,
          util_img: activeFiles.util_png,
        };
        const filename = imgMap[key];
        if (filename) {
          const rel = filename.startsWith(`${result.run_id}/`)
            ? filename.slice(result.run_id.length + 1)
            : filename;
          window.open(downloadUrl(result.run_id, rel), '_blank');
        }
        else message.warning('找不到对应图片文件');
      } else {
        const activeFiles = dynamicFiles ?? result.files;
        const fileMap: Record<string, string> = {
          schedule: activeFiles.schedule_csv,
          completion: activeFiles.completion_csv,
          kit: activeFiles.kit_csv,
          stages: activeFiles.stages_csv,
          comparison: activeFiles.comparison_csv,
        };
        const filename = fileMap[key];
        if (filename) {
          const rel = filename.startsWith(`${result.run_id}/`)
            ? filename.slice(result.run_id.length + 1)
            : filename;
          window.open(downloadUrl(result.run_id, rel), '_blank');
        }
        else message.warning('找不到对应下载文件');
      }
    } catch (e: any) {
      message.error(`下载失败: ${e?.message ?? '未知错误'}`);
    }
  }, [dynamicFiles, result]);

  // memo 稳定 tabItems，避免每帧销毁重建组件
  const tabItems = useMemo(() => [
    { key: 'import', label: '📂 数据导入', children: <DataImport onUploaded={handleUploaded} /> },
    { key: 'params', label: '⚙️ 参数配置', children: <ParamConfig params={params} onChange={setParams} upload={upload} /> },
    { key: 'architecture', label: '📐 模型架构', children: <ModelArchitecture /> },
    { key: 'overview', label: '📈 结果概览', disabled: !result, children: result ? <ResultOverview data={result} prevData={prevResult} /> : <div /> },
    { key: 'gantt', label: '🗓️ 甘特图', disabled: !result, children: result ? <GanttChart data={dynamicGantt ?? result.ganttData} makespanHours={dynamicMakespan ?? result.makespanHours} stages={dynamicStages ?? result.stagesData} /> : <div /> },
    { key: 'kit', label: '📦 齐套分析', disabled: !result, children: result ? <KitAnalysis data={result.kitSpanData} /> : <div /> },
    { key: 'util', label: '🔧 设备利用率', disabled: !result, children: result ? <UtilCharts data={result.utilizationData} /> : <div /> },


    { key: 'reschedule', label: '⚡ 动态响应', disabled: !result, children: result ? <ReschedulePanel runId={activeRunId} currentResult={result} onRescheduled={(gantt, makespan, metrics, stagesData, utilData, kitData, comparisonData, files) => {
      // Update result with all rescheduled data to keep all views in sync
      setResult(prev => prev ? {
        ...prev,
        makespanHours: makespan,
        ganttData: gantt,
        metrics: {
          ...prev.metrics,
          optimized: metrics,
          comparison: comparisonData ?? prev.metrics.comparison,
        },
        files: files ? { ...prev.files, ...files } : prev.files,
        stagesData: stagesData ?? prev.stagesData,
        utilizationData: utilData ?? prev.utilizationData,
        kitSpanData: kitData ?? prev.kitSpanData,
      } : prev);
      setDynamicGantt(null);
      setDynamicStages(null);
      setDynamicMakespan(null);
      setDynamicFiles(null);
    }} onPartialResult={(partial, files) => {
      if (!partial) {
        setDynamicGantt(null);
        setDynamicStages(null);
        setDynamicMakespan(null);
        setDynamicFiles(null);
        return;
      }
      setDynamicGantt(partial.ganttData);
      setDynamicStages(partial.stagesData);
      setDynamicMakespan(partial.makespanHours);
      setDynamicFiles(files ?? null);
    }} onDynamicRunningChange={setDynamicRunning} /> : <div /> },
  ], [params, upload, result, prevResult, handleUploaded, activeRunId, dynamicRunning]);

  return (
    <ConfigProvider>
    <div className="app-layout">
      {/* 全屏加载动画 */}
      <LoadingPipeline visible={loading} onCancel={handleCancelRun} />

      <div className="app-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <Button
            type="text"
            icon={sidebarCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={() => setSidebarCollapsed((v) => !v)}
            style={{ fontSize: FONT.sizeLg, color: COLORS.textSecondary }}
          />
          <h1>🏭 钢板齐套感知排产模型</h1>
          <Button
            type="text"
            icon={<BookOutlined />}
            onClick={() => setActiveTab('architecture')}
            style={{ fontWeight: 500, color: COLORS.textSecondary }}
          >
            模型架构
          </Button>
          {upload && (
            <Space size={4}>
              <Text type="secondary" style={{ fontSize: FONT.sizeSm }}>数据: {upload.filename}</Text>
              {upload.speed_table_uploaded && (
                <span style={{ fontSize: FONT.sizeXs, color: COLORS.warning, fontWeight: FONT.weightSemibold }}>⚡附件3</span>
              )}
            </Space>
          )}
          {result && (
            <Tag color="blue">
              本次运行耗时 {lastRunSeconds != null ? formatDuration(lastRunSeconds) : '未记录'}
            </Tag>
          )}
        </div>
        <Space>
          {(result as any)?.reports && (
            <Segmented
              value={reportMode}
              onChange={handleReportModeChange}
              options={[
                { label: '重工时（产能优先）', value: 'capacity' },
                { label: '兼顾三指标（原评价）', value: 'balanced' },
              ]}
            />
          )}
          <Button type="primary" icon={<PlayCircleOutlined />} onClick={handleRun} loading={loading} size="large">
            开始计算
          </Button>
          <Dropdown menu={{ items: downloadItems, onClick: ({ key }) => handleDownload(key) }} disabled={!result}>
            <Button icon={<DownloadOutlined />} size="large" disabled={!result}>导出</Button>
          </Dropdown>
        </Space>
      </div>

      <div className="app-body">
        <div className={`app-sidebar${sidebarCollapsed ? ' collapsed' : ''}`}>
          <HistorySidebar activeRunId={activeRunId} onSelect={handleHistorySelect} collapsed={sidebarCollapsed} />
        </div>
        <div className="app-content">
          <Tabs
            key={`${activeRunId ?? (result?.run_id ?? 'no-run')}-${reportMode}`}
            activeKey={activeTab}
            onChange={setActiveTab}
            items={tabItems}
            size="large"
          />
        </div>
      </div>
    </div>
    </ConfigProvider>
  );
};

export default App;
