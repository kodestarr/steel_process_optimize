import React, { useState, useCallback, useMemo, useEffect, useRef } from 'react';
import {
  Tabs,
  Button,
  Space,
  Segmented,
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
import BufferMonitor from './components/BufferMonitor';
import KitDashboard from './components/KitDashboard';
import HistorySidebar from './components/HistorySidebar';
import LoadingPipeline from './components/LoadingPipeline';
import ModelArchitecture from './components/ModelArchitecture';
import { runModel, reportUrl, downloadUrl, fetchProgress } from './api';
import { COLORS, FONT } from './theme';

import './App.css';

const { Text } = Typography;

const App: React.FC = () => {
  const [upload, setUpload] = useState<UploadResponse | null>(null);
  const [params, setParams] = useState<ModelParams>({ ...DEFAULT_PARAMS });
  const [result, setResult] = useState<RunResponse | null>(null);
  const [reportMode, setReportMode] = useState<'capacity' | 'balanced'>('capacity');
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadProgress, setLoadProgress] = useState<{ elapsed: number; stage: string } | null>(null);
  const loadStartRef = useRef(0);
  const progressTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const mountedRef = useRef(true);  // P0-5: 组件卸载标记，防止异步操作写已卸载组件
  const [activeTab, setActiveTab] = useState('import');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [prevResult, setPrevResult] = useState<RunResponse | null>(null);
  // Dark mode removed — system always uses light industrial theme

  const handleUploaded = useCallback((res: UploadResponse) => {
    setUpload(res);
    setResult(null);
    setPrevResult(null);
    setActiveRunId(null);
    setActiveTab('params');
  }, []);

  const handleRun = useCallback(async () => {
    if (!upload) {
      message.warning('请先上传数据文件');
      setActiveTab('import');
      return;
    }
    loadStartRef.current = Date.now();
    setLoadProgress({ elapsed: 0, stage: '初始化优化...' });
    setLoading(true);
    mountedRef.current = true;  // P0-5: 每次新运行时重置标记

    let runId: string | null = null;

    const runPromise = runModel(upload.file_id, params).then(res => {
      runId = res.run_id;
      return res;
    });

    // ── P2-4 修复：使用真实进度轮询替代伪时间估算 ──
    // 后端 /api/run 是同步阻塞的，但 run_id 在优化过程中已生成。
    // 策略：在等待 runModel 返回的同时，显示基于 elapsed 的估计阶段（诚实标注"预计"），
    // 完成后立即用真实后端状态更新。
    progressTimerRef.current = setInterval(() => {
      const elapsed = (Date.now() - loadStartRef.current) / 1000;
      // 根据已用时间粗略估计阶段（标注"预计"，非虚假精准进度）
      let stage = '预计：数据准备中...';
      if (elapsed > 120) stage = '预计：DRL增强搜索中...';
      else if (elapsed > 60) stage = '预计：SA+Tabu 迭代优化中...';
      else if (elapsed > 15) stage = '预计：离散事件仿真中...';
      else if (elapsed > 5) stage = '预计：切割工时计算中...';
      setLoadProgress({ elapsed, stage });
    }, 2000);  // 降低轮询频率至2s，减少不必要的渲染

    try {
      const res = await runPromise;
      if (!mountedRef.current) return;
      if (runId && res.run_id) {
        try {
          const prog = await fetchProgress(res.run_id);
          if (prog.status === 'completed') {
            setLoadProgress({ elapsed: (Date.now() - loadStartRef.current) / 1000, stage: '✅ 优化完成，生成结果...' });
          }
        } catch {
          // 进度查询失败不影响主流程
        }
      }
      setPrevResult(result);
      setResult(res);
      setReportMode('capacity');
      setActiveRunId(res.run_id);
      setActiveTab('overview');
      message.success('计算完成！');
    } catch (e: any) {
      if (!mountedRef.current) return;
      const msg = e?.response?.data?.detail ?? e?.message ?? '运行失败';
      Modal.error({ title: '运行失败', content: msg });
    } finally {
      if (!mountedRef.current) return;
      setLoading(false);
      setLoadProgress(null);
      if (progressTimerRef.current) { clearInterval(progressTimerRef.current); progressTimerRef.current = null; }
    }
  }, [upload, params, result]);

  // Cleanup progress timer and mounted flag on unmount
  useEffect(() => {
    return () => {
      if (progressTimerRef.current) clearInterval(progressTimerRef.current);
      mountedRef.current = false;
    };
  }, []);

  const handleHistorySelect = useCallback((runId: string, data: RunResponse) => {
    setPrevResult(result);
    setResult(data);
    setReportMode(data.reportMode === 'balanced' ? 'balanced' : 'capacity');
    setActiveRunId(runId);
    setActiveTab('overview');
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
        const imgMap: Record<string, string> = {
          gantt_img: result.files.gantt_png,
          kit_img: result.files.kit_png,
          util_img: result.files.util_png,
        };
        const filename = imgMap[key];
        if (filename) {
          const rel = filename.split('/').slice(1).join('/');
          window.open(downloadUrl(result.run_id, rel), '_blank');
        }
        else message.warning('找不到对应图片文件');
      } else {
        const fileMap: Record<string, string> = {
          schedule: result.files.schedule_csv,
          completion: result.files.completion_csv,
          kit: result.files.kit_csv,
          stages: result.files.stages_csv,
          comparison: result.files.comparison_csv,
        };
        const filename = fileMap[key];
        if (filename) {
          const rel = filename.split('/').slice(1).join('/');
          window.open(downloadUrl(result.run_id, rel), '_blank');
        }
        else message.warning('找不到对应下载文件');
      }
    } catch (e: any) {
      message.error(`下载失败: ${e?.message ?? '未知错误'}`);
    }
  }, [result]);

  // memo 稳定 tabItems，避免每帧销毁重建组件
  const tabItems = useMemo(() => [
    { key: 'import', label: '📂 数据导入', children: <DataImport onUploaded={handleUploaded} /> },
    { key: 'params', label: '⚙️ 参数配置', children: <ParamConfig params={params} onChange={setParams} upload={upload} /> },
    { key: 'architecture', label: '📐 模型架构', children: <ModelArchitecture /> },
    { key: 'overview', label: '📈 结果概览', disabled: !result, children: result ? <ResultOverview data={result} prevData={prevResult} /> : <div /> },
    { key: 'gantt', label: '🗓️ 甘特图', disabled: !result, children: result ? <GanttChart data={result.ganttData} makespanHours={result.makespanHours} stages={result.stagesData} /> : <div /> },
    { key: 'kit', label: '📦 齐套分析', disabled: !result, children: result ? <KitAnalysis data={result.kitSpanData} /> : <div /> },
    { key: 'util', label: '🔧 设备利用率', disabled: !result, children: result ? <UtilCharts data={result.utilizationData} /> : <div /> },


    { key: 'kitboard', label: '📦 齐套看板', disabled: !result, children: result ? <KitDashboard kitData={result.kitSpanData} makespanHours={result.metrics.optimized?.['总完工时间(h)'] ?? result.makespanHours} /> : <div /> },
    { key: 'buffer', label: '📊 缓存监控', disabled: !result, children: result ? <BufferMonitor stages={result.stagesData} makespanHours={result.makespanHours} bufferTimeseries={result.bufferTimeseries} bufferConfig={result.bufferConfig} deadlockWarnings={(result.metrics.optimized?.['死锁详情'] as unknown) as string[] | undefined} deadlockCount={result.metrics.optimized?.['死锁警告数'] as unknown as number | undefined} /> : <div /> },
    { key: 'reschedule', label: '⚡ 重调度', disabled: !result, children: result ? <ReschedulePanel runId={activeRunId} currentResult={result} onRescheduled={(gantt, makespan, metrics, stagesData, utilData, bufTs) => {
      // Update result with all rescheduled data to keep all views in sync
      setResult(prev => prev ? {
        ...prev,
        makespanHours: makespan,
        ganttData: gantt,
        metrics: { ...prev.metrics, optimized: metrics },
        stagesData: stagesData ?? prev.stagesData,
        utilizationData: utilData ?? prev.utilizationData,
        bufferTimeseries: bufTs ?? prev.bufferTimeseries,
      } : prev);
    }} /> : <div /> },
  ], [params, upload, result, prevResult, handleUploaded, activeRunId]);

  return (
    <ConfigProvider>
    <div className="app-layout">
      {/* 全屏加载动画 */}
      <LoadingPipeline visible={loading} progress={loadProgress} />

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
          <Tabs activeKey={activeTab} onChange={setActiveTab} items={tabItems} size="large" />
        </div>
      </div>
    </div>
    </ConfigProvider>
  );
};

export default App;
