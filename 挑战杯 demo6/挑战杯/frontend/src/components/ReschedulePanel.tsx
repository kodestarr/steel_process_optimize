import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Input,
  InputNumber,
  Radio,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd';
import {
  AlertOutlined,
  CloudUploadOutlined,
  DeleteOutlined,
  PlusCircleOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
  ToolOutlined,
} from '@ant-design/icons';
import type {
  DynamicRescheduleResponse,
  DynamicFirstBatch,
  DynamicFirstPlate,
  DynamicJobStartResponse,
  DynamicResource,
  DynamicStateResponse,
  DynamicUploadResponse,
  FaultEventInput,
  RemainingModel,
} from '../api';
import {
  clearDynamicUpload,
  cancelDynamicJob,
  fetchDynamicJob,
  fetchDynamicState,
  startDynamicReschedule,
  uploadDynamicData,
} from '../api';
import type { GanttItem, RunResponse, StageItem, UtilItem } from '../types';
import { COLORS, FONT } from '../theme';
import LoadingPipeline from './LoadingPipeline';

const { Title, Text } = Typography;

interface Props {
  runId: string | null;
  currentResult: RunResponse | null;
  onRescheduled: (
    gantt: GanttItem[],
    makespan: number,
    metrics: Record<string, number>,
    stagesData?: StageItem[],
    utilData?: UtilItem[],
    kitData?: import('../types').KitSpanItem[],
    comparisonData?: import('../types').ComparisonRow[],
    files?: Record<string, string>,
  ) => void;
  onPartialResult?: (
    partial: DynamicJobStartResponse['partialResult'] | null,
    files?: Record<string, string>,
  ) => void;
  onDynamicRunningChange?: (running: boolean) => void;
}

interface FaultRow extends FaultEventInput {
  _key: string;
}

const makeFaultKey = () => `fault-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

const panelCardStyle: React.CSSProperties = {
  marginBottom: 16,
};

const fieldLabelStyle: React.CSSProperties = {
  display: 'block',
  marginBottom: 6,
  fontWeight: 600,
};

const ReschedulePanel: React.FC<Props> = ({
  runId,
  currentResult,
  onRescheduled,
  onPartialResult,
  onDynamicRunningChange,
}) => {
  const [dynamicState, setDynamicState] = useState<DynamicStateResponse | null>(null);
  const [faults, setFaults] = useState<FaultRow[]>([]);
  const [resourceCatalog, setResourceCatalog] = useState<DynamicResource[]>([]);
  const [inProcessPolicy, setInProcessPolicy] = useState<'finish' | 'interrupt'>('finish');
  const [clearTime, setClearTime] = useState('00:15:00');
  const [orderChangeTime, setOrderChangeTime] = useState('00:00:00');
  const [timeLimit, setTimeLimit] = useState(5);
  const [remainingModel, setRemainingModel] = useState<RemainingModel>('stable');
  const [uploadMode, setUploadMode] = useState<'order_change' | 'rush_insert'>('order_change');
  const [loading, setLoading] = useState(false);
  const [backgroundRunning, setBackgroundRunning] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [firstPlate, setFirstPlate] = useState<DynamicFirstPlate | null>(null);
  const [firstBatch, setFirstBatch] = useState<DynamicFirstBatch | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadInfo, setUploadInfo] = useState<DynamicUploadResponse | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [result, setResult] = useState<DynamicRescheduleResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const resourceOptions = useMemo(
    () => resourceCatalog.map((item) => ({
      value: item.id,
      label: `${item.group} · ${item.label}`,
      title: item.description,
    })),
    [resourceCatalog],
  );

  const hydrateState = useCallback((state: DynamicStateResponse) => {
    setDynamicState(state);
    setRemainingModel(state.remaining_model || 'stable');
    setUploadMode(state.upload_mode === 'rush_insert' ? 'rush_insert' : 'order_change');
    setOrderChangeTime(state.order_change_time || '00:00:00');
    setResourceCatalog(state.resource_catalog || []);
    setFaults((state.faults || []).map((fault, index) => ({
      ...fault,
      _key: fault.id || `stored-${index}`,
    })));
  }, []);

  useEffect(() => {
    if (!runId) {
      setDynamicState(null);
      setFaults([]);
      setResourceCatalog([]);
      setUploadInfo(null);
      setResult(null);
      return;
    }
    let cancelled = false;
    fetchDynamicState(runId)
      .then((state) => {
        if (!cancelled) hydrateState(state);
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          const err = e as { response?: { data?: { detail?: string } }; message?: string };
          setError(err?.response?.data?.detail ?? err?.message ?? '读取动态状态失败');
        }
      });
    return () => { cancelled = true; };
  }, [runId, hydrateState]);

  const addFault = useCallback(() => {
    setFaults((prev) => [
      ...prev,
      {
        _key: makeFaultKey(),
        resource_id: resourceCatalog[0]?.id ?? '',
        start_time: '00:00:00',
        duration: '02:00:00',
      },
    ]);
  }, [resourceCatalog]);

  const updateFault = useCallback((key: string, patch: Partial<FaultRow>) => {
    setFaults((prev) => prev.map((fault) => (fault._key === key ? { ...fault, ...patch } : fault)));
  }, []);

  const removeFault = useCallback((key: string) => {
    setFaults((prev) => prev.filter((fault) => fault._key !== key));
  }, []);

  const repairNow = useCallback((key: string) => {
    const now = new Date();
    const endTime = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;
    updateFault(key, { end_time: endTime, duration: '00:00:00' });
  }, [updateFault]);

  const handleUpload = useCallback(async (file: File) => {
    if (!runId) return;
    setUploading(true);
    setUploadError(null);
    setUploadInfo(null);
    try {
      const uploaded = await uploadDynamicData(runId, file);
      setUploadInfo(uploaded);
      setUploadMode('order_change');
      message.success(`附件2已读取：${uploaded.plate_count} 张钢板，用时 ${uploaded.parse_time_s.toFixed(2)}s`);
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      const msg = err?.response?.data?.detail ?? err?.message ?? '附件2上传失败';
      setUploadError(msg);
      message.error(msg);
    } finally {
      setUploading(false);
    }
  }, [runId]);

  const handleDeleteUpload = useCallback(async () => {
    if (!runId) return;
    setUploading(true);
    try {
      const state = await clearDynamicUpload(runId);
      setUploadInfo(null);
      setUploadError(null);
      setDynamicState(state);
      message.success('已删除上传的动态附件2');
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      const msg = err?.response?.data?.detail ?? err?.message ?? '删除附件2失败';
      setUploadError(msg);
      message.error(msg);
    } finally {
      setUploading(false);
    }
  }, [runId]);

  const applyDynamicResult = useCallback((response: DynamicRescheduleResponse) => {
    setResult(response);
    setDynamicState(response.state);
    setResourceCatalog(response.resource_catalog);
    setFaults(response.faults.map((fault, index) => ({ ...fault, _key: fault.id || `response-${index}` })));
    setBackgroundRunning(false);
    setJobId(null);
    onRescheduled(
      response.ganttData,
      response.makespanHours,
      response.metrics.optimized,
      response.stagesData,
      response.utilizationData,
      response.kitSpanData,
      response.comparisonData,
      response.files,
    );
    onPartialResult?.(null);
    onDynamicRunningChange?.(false);
    message.success(`动态重排完成，响应 ${response.reschedule_time_s.toFixed(2)}s`);
  }, [onDynamicRunningChange, onPartialResult, onRescheduled]);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const job = await fetchDynamicJob(jobId);
        if (cancelled) return;
        if (job.status === 'completed' && job.result) {
          applyDynamicResult(job.result);
        } else if (job.status === 'failed') {
          setBackgroundRunning(false);
          setJobId(null);
          setError(job.error || '后台优化失败');
          onPartialResult?.(null);
          onDynamicRunningChange?.(false);
        } else if ((job.status as string) === 'cancelled') {
          setBackgroundRunning(false);
          setJobId(null);
          onPartialResult?.(null);
          onDynamicRunningChange?.(false);
          message.info('动态优化已中断');
        }
      } catch (e: unknown) {
        if (!cancelled) {
          const err = e as { response?: { data?: { detail?: string } }; message?: string };
          setError(err?.response?.data?.detail ?? err?.message ?? '后台任务查询失败');
        }
      }
    };
    void poll();
    const timer = window.setInterval(poll, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [jobId, applyDynamicResult, onDynamicRunningChange, onPartialResult]);

  const handleReschedule = useCallback(async () => {
    if (!runId || !currentResult) return;
    const effectivePlateFileId = uploadInfo?.file_id ?? dynamicState?.input_file_id;

    setLoading(true);
    setError(null);
    try {
      const started = await startDynamicReschedule({
        run_id: runId,
        plate_file_id: effectivePlateFileId,
        faults: faults.map(({ _key, ...fault }) => ({ ...fault, id: fault.id || _key })),
        order_changes: [],
        order_change_time: orderChangeTime,
        in_process_policy: inProcessPolicy,
        clear_time: clearTime,
        strategy_mode: 'auto',
        remaining_model: remainingModel,
        upload_mode: uploadMode,
        time_limit_s: timeLimit,
      });
      setFirstPlate(started.first_plate);
      setFirstBatch(started.first_batch);
      setJobId(started.job_id);
      setBackgroundRunning(true);
      onDynamicRunningChange?.(true);
      if (started.partialResult) {
        onPartialResult?.(started.partialResult, started.partialFiles);
      }
      message.success(`首板方案已生成：${started.first_plate.name}，用时 ${started.preview_time_s.toFixed(2)}s`);
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      const msg = err?.response?.data?.detail ?? err?.message ?? '动态重排失败';
      setError(msg);
      message.error(msg);
    } finally {
      setLoading(false);
    }
  }, [
    clearTime,
    currentResult,
    dynamicState,
    faults,
    inProcessPolicy,
    onRescheduled,
    onDynamicRunningChange,
    onPartialResult,
    runId,
    remainingModel,
    uploadMode,
    orderChangeTime,
    timeLimit,
    uploadInfo,
  ]);

  const warnings = result?.capacityWarnings ?? [];
  const hasMachineFault = faults.some((fault) => Boolean(fault.resource_id));
  const hasOrderChange = uploadError
    ? false
    : Boolean(uploadInfo?.file_id || dynamicState?.input_file_id);
  const disabled = !runId || !currentResult || loading || uploading || backgroundRunning
    || (!hasMachineFault && !hasOrderChange);

  const handleCancelDynamic = useCallback(async () => {
    if (!jobId) return;
    try {
      await cancelDynamicJob(jobId);
      message.info('已发出紧急中断请求');
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      message.error(err?.response?.data?.detail ?? err?.message ?? '中断失败');
    } finally {
      setBackgroundRunning(false);
      setJobId(null);
      onPartialResult?.(null);
      onDynamicRunningChange?.(false);
    }
  }, [jobId, onDynamicRunningChange, onPartialResult]);

  if (backgroundRunning) {
    return <LoadingPipeline visible onCancel={handleCancelDynamic} />;
  }

  return (
    <div style={{ maxWidth: 1280, margin: '0 auto' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          gap: 16,
          marginBottom: 16,
        }}
      >
        <div>
          <Title level={4} style={{ margin: 0 }}>动态响应机制</Title>
          <Text type="secondary" style={{ fontSize: FONT.sizeXs }}>
            多故障链式事件 · 资源能力联动 · 订单数据替换 · 全链路重仿真
          </Text>
        </div>
        <Space size={12} wrap>
          <Space size={8}>
            <Text strong>剩余板优化模型</Text>
            <Select
              key={`remaining-model:${remainingModel}`}
              value={remainingModel}
              onChange={setRemainingModel}
              style={{ width: 180 }}
              options={[
                { value: 'stable', label: '稳定修复' },
                { value: 'sa_tabu', label: 'SA+Tabu' },
                { value: 'ga_lns', label: 'GA+LNS' },
                { value: 'dq_nsga2', label: 'DQN+NSGA-II' },
              ]}
            />
          </Space>
          <Space size={8}>
            <Text strong>最大响应时间</Text>
            <InputNumber
              value={timeLimit}
              min={0.25}
              max={60}
              step={0.25}
              onChange={(value) => setTimeLimit(value ?? 5)}
              addonAfter="秒"
              style={{ width: 150 }}
            />
          </Space>
        </Space>
      </div>

      {!runId && (
        <Alert
          type="info"
          showIcon
          message="请先运行一次基础排程，再进入动态响应机制"
          style={{ marginBottom: 16 }}
        />
      )}

      <Card
        size="small"
        title={<Space><AlertOutlined />故障事件清单</Space>}
        extra={<Button size="small" icon={<PlusCircleOutlined />} onClick={addFault} disabled={!resourceCatalog.length}>添加故障</Button>}
        style={panelCardStyle}
      >
        <Table
          size="small"
          rowKey="_key"
          pagination={false}
          scroll={{ x: 820 }}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前没有故障事件" /> }}
          dataSource={faults}
          columns={[
                {
                  title: '故障资源',
                  dataIndex: 'resource_id',
                  width: 245,
                  render: (value: string, row: FaultRow) => (
                    <Select
                      key={`${row._key}:${value}`}
                      showSearch
                      value={value}
                      options={resourceOptions}
                      onChange={(next) => updateFault(row._key, { resource_id: next })}
                      style={{ width: '100%' }}
                      optionFilterProp="label"
                    />
                  ),
                },
                {
                  title: '发生时间',
                  dataIndex: 'start_time',
                  width: 130,
                  render: (value: string, row: FaultRow) => (
                    <Input
                      value={value}
                      placeholder="HH:MM:SS"
                      onChange={(e) => updateFault(row._key, { start_time: e.target.value })}
                    />
                  ),
                },
                {
                  title: '修复时长',
                  dataIndex: 'duration',
                  width: 130,
                  render: (value: string, row: FaultRow) => (
                    <Input
                      value={value}
                      placeholder="HH:MM:SS"
                      onChange={(e) => updateFault(row._key, { duration: e.target.value, end_time: undefined })}
                    />
                  ),
                },
                {
                  title: '操作',
                  width: 160,
                  fixed: 'right',
                  render: (_: unknown, row: FaultRow) => (
                    <Space size={4}>
                      <Button size="small" icon={<ToolOutlined />} onClick={() => repairNow(row._key)}>提前修复</Button>
                      <Button size="small" danger icon={<DeleteOutlined />} onClick={() => removeFault(row._key)} />
                    </Space>
                  ),
                },
          ]}
        />
      </Card>

      <Card
        size="small"
        title={<Space><ReloadOutlined />订单变更与新附件</Space>}
        style={panelCardStyle}
      >
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 12,
            padding: '12px 14px',
            border: '1px solid #E5E7EB',
            borderRadius: 8,
            background: '#FAFAFA',
            marginBottom: 12,
          }}
        >
          <Space direction="vertical" size={2}>
            <Text strong>订单变更必须上传全新附件2</Text>
            <Text type="secondary" style={{ fontSize: FONT.sizeXs }}>
              系统会重新执行数据校验、钢板/零件关联检查和齐套组读取。
            </Text>
          </Space>
          <Space wrap>
            {uploadInfo && (
              <Tag color="green">
                {uploadInfo.filename} · {uploadInfo.plate_count} 张板
              </Tag>
            )}
            {hasOrderChange && (
              <Select
                key={`upload-mode:${uploadMode}`}
                value={uploadMode}
                onChange={setUploadMode}
                style={{ width: 130 }}
                options={[
                  { value: 'order_change', label: '订单变更' },
                  { value: 'rush_insert', label: '紧急插单' },
                ]}
              />
            )}
            <Upload
              accept=".xlsx,.xls"
              maxCount={1}
              showUploadList={false}
              beforeUpload={(file) => {
                void handleUpload(file);
                return false;
              }}
            >
              <Button icon={<CloudUploadOutlined />} loading={uploading}>
                {uploadInfo ? '重新上传附件2' : '上传附件2'}
              </Button>
            </Upload>
            {hasOrderChange && (
              <Button danger icon={<DeleteOutlined />} onClick={() => void handleDeleteUpload()}>
                删除附件2
              </Button>
            )}
          </Space>
        </div>

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
            gap: 16,
            padding: '12px 14px',
            border: '1px solid #E5E7EB',
            borderRadius: 8,
            marginBottom: 12,
            opacity: hasOrderChange ? 1 : 0.5,
          }}
        >
          <div>
            <Text style={fieldLabelStyle}>订单变更发生时间</Text>
            <Input
              value={orderChangeTime}
              onChange={(e) => setOrderChangeTime(e.target.value)}
              disabled={!hasOrderChange}
              placeholder="HH:MM:SS"
            />
          </div>
          <div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 6 }}>
              <Text style={{ ...fieldLabelStyle, marginBottom: 0 }}>正在进行加工的钢板的处理方式</Text>
              <Text type="secondary" style={{ fontSize: FONT.sizeXs }}>已完成钢板不回滚</Text>
            </div>
            <Radio.Group
              value={inProcessPolicy}
              onChange={(e) => setInProcessPolicy(e.target.value)}
              disabled={!hasOrderChange}
              style={{ display: 'flex', flexWrap: 'wrap', gap: 12, minHeight: 32, alignItems: 'center' }}
            >
              <Radio value="finish">继续加工完成</Radio>
              <Radio value="interrupt">立即中断并清台</Radio>
            </Radio.Group>
          </div>
          <div>
            <Text style={fieldLabelStyle}>清台时间</Text>
            <Input
              value={clearTime}
              onChange={(e) => setClearTime(e.target.value)}
              disabled={!hasOrderChange || inProcessPolicy !== 'interrupt'}
              placeholder="HH:MM:SS"
            />
          </div>
        </div>

      </Card>

      {firstPlate && (
        <Alert
          type="info"
          showIcon
          message={`首板加工信息：${firstPlate.name} · ${firstPlate.machine} · 胎架${firstPlate.table + 1}`}
          description={
            backgroundRunning
              ? `首批 ${firstBatch?.names.length ?? 1} 张钢板，预计加工窗口 ${((firstBatch?.duration_s ?? firstPlate.duration_s) / 60).toFixed(1)} 分钟。系统正在使用这段时间作为独立预算优化剩余钢板。`
              : `首批 ${firstBatch?.names.length ?? 1} 张钢板，预计加工窗口 ${((firstBatch?.duration_s ?? firstPlate.duration_s) / 60).toFixed(1)} 分钟。`
          }
          style={{ marginBottom: 16 }}
        />
      )}

      <Button
        type="primary"
        size="large"
        block
        icon={<ThunderboltOutlined />}
        loading={loading}
        disabled={disabled}
        onClick={handleReschedule}
        style={{ height: 48, marginBottom: 16 }}
      >
        执行动态重排
      </Button>

      {error && <Alert type="error" showIcon closable message={error} style={{ marginBottom: 16 }} />}

      {result && (
        <>
          <Alert
            type="success"
            showIcon
            message={`动态重排完成 · ${result.strategy_reason}`}
            description={`端到端响应 ${result.reschedule_time_s.toFixed(2)} 秒`}
            style={{ marginBottom: 16 }}
          />

          <Card size="small" title="方案对比" style={panelCardStyle}>
            <Row gutter={[16, 16]}>
              <Col xs={12} md={6}>
                <Statistic title="基础方案完工" value={Number(result.baselineMetrics?.['总完工时间(h)'] ?? 0).toFixed(1)} suffix="h" />
              </Col>
              <Col xs={12} md={6}>
                <Statistic title="动态方案完工" value={result.makespanHours.toFixed(1)} suffix="h" />
              </Col>
              <Col xs={12} md={6}>
                <Statistic
                  title="相对基础变化"
                  value={`${result.makespanDeltaHours >= 0 ? '+' : ''}${result.makespanDeltaHours.toFixed(2)}`}
                  suffix="h"
                  valueStyle={{ color: result.makespanDeltaHours > 0 ? COLORS.error : COLORS.success }}
                />
              </Col>
              <Col xs={12} md={6}>
                <Statistic title="响应时间" value={result.reschedule_time_s.toFixed(2)} suffix="s" valueStyle={{ color: result.reschedule_time_s <= timeLimit ? COLORS.success : COLORS.error }} />
              </Col>
            </Row>
          </Card>

          <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
            <Col xs={12} md={6}><Card size="small"><Statistic title="冻结钢板" value={result.frozen_plates} suffix="张" /></Card></Col>
            <Col xs={12} md={6}><Card size="small"><Statistic title="重优化钢板" value={result.reoptimized_plates} suffix="张" /></Card></Col>
            <Col xs={12} md={6}><Card size="small"><Statistic title="取消钢板" value={result.cancelled_plates} suffix="张" /></Card></Col>
            <Col xs={12} md={6}><Card size="small"><Statistic title="数据新增/删除" value={`${result.dataChangeSummary.added_plates.length}/${result.dataChangeSummary.removed_plates.length}`} /></Card></Col>
          </Row>

          <Card size="small" title="策略与阶段耗时" style={panelCardStyle}>
            <Descriptions size="small" bordered column={{ xs: 1, sm: 2, md: 3 }}>
              <Descriptions.Item label="剩余板模型"><Tag color="blue">{result.remainingModel || result.strategy}</Tag></Descriptions.Item>
              <Descriptions.Item label="受影响钢板">{result.quickPlan.changedPlates} 张</Descriptions.Item>
              <Descriptions.Item label="搜索迭代">{String(result.searchStats?.iterations ?? 0)}</Descriptions.Item>
              <Descriptions.Item label="状态装载">{result.breakdown_ms.state_load_ms.toFixed(1)} ms</Descriptions.Item>
              <Descriptions.Item label="贪心修复">{result.breakdown_ms.greedy_repair_ms.toFixed(1)} ms</Descriptions.Item>
              <Descriptions.Item label="组合搜索">{result.breakdown_ms.search_ms.toFixed(1)} ms</Descriptions.Item>
              <Descriptions.Item label="全链路仿真">{result.breakdown_ms.simulation_ms.toFixed(1)} ms</Descriptions.Item>
            </Descriptions>
          </Card>

          {warnings.length > 0 && (
            <Card size="small" title="调整说明与容量告警" style={panelCardStyle}>
              <Space direction="vertical" style={{ width: '100%' }}>
                {warnings.map((warning, index) => (
                  <Alert key={`${warning}-${index}`} type="warning" showIcon message={warning} />
                ))}
              </Space>
            </Card>
          )}
        </>
      )}
    </div>
  );
};

export default ReschedulePanel;
