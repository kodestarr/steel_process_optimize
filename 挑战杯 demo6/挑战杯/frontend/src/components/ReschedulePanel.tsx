import React, { useState, useCallback } from 'react';
import {
  Card, Button, Select, Slider, InputNumber,
  Space, Typography, Alert, Statistic, Row, Col,
  Tag, Descriptions,
} from 'antd';
import {
  ThunderboltOutlined,
  AlertOutlined,
  ReloadOutlined,
  PlusCircleOutlined,
  ClockCircleOutlined,
  CheckCircleOutlined,
} from '@ant-design/icons';
import type { RunResponse, GanttItem, StageItem, UtilItem } from '../types';
import { rescheduleModel } from '../api';
import { COLORS, FONT } from '../theme';

const { Title, Text } = Typography;

interface Props {
  runId: string | null;
  currentResult: RunResponse | null;
  onRescheduled: (gantt: GanttItem[], makespan: number, metrics: Record<string, number>, stagesData?: StageItem[], utilData?: UtilItem[], bufTs?: import('../types').BufferTimeseriesEntry[]) => void;
}

type ScenarioType = 'machine_failure' | 'rush_order' | 'reoptimize';

const ReschedulePanel: React.FC<Props> = ({ runId, currentResult, onRescheduled }) => {
  const [scenario, setScenario] = useState<ScenarioType>('machine_failure');
  const [faultTime, setFaultTime] = useState(12.0);
  const [faultMachine, setFaultMachine] = useState('N5');
  const [faultDuration, setFaultDuration] = useState(3.0);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<{
    reschedule_time_s: number;
    frozen_plates: number;
    reoptimized_plates: number;
    stats: string;
    makespanHours: number;
    metrics: Record<string, number>;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const machines = currentResult?.ganttData
    ? [...new Set(currentResult.ganttData.map((d) => d.machine))].sort()
    : ['N2', 'N5'];

  const handleReschedule = useCallback(async () => {
    if (!runId) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await rescheduleModel({
        run_id: runId,
        scenario,
        fault_time_h: scenario === 'machine_failure' ? faultTime : undefined,
        fault_machine: scenario === 'machine_failure' ? faultMachine : undefined,
        fault_duration_h: scenario === 'machine_failure' ? faultDuration : undefined,
        time_limit_s: 5.0,
      });
      setResult({
        reschedule_time_s: res.reschedule_time_s,
        frozen_plates: res.frozen_plates,
        reoptimized_plates: res.reoptimized_plates,
        stats: res.stats,
        makespanHours: res.makespanHours,
        metrics: res.metrics.optimized,
      });
      onRescheduled(res.ganttData, res.makespanHours, res.metrics.optimized, res.stagesData, res.utilizationData, res.bufferTimeseries);
    } catch (e: any) {
      const msg = e?.response?.data?.detail ?? e?.message ?? '重调度失败';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [runId, scenario, faultTime, faultMachine, faultDuration, onRescheduled]);

  const scenarioOptions = [
    { value: 'machine_failure' as const, label: '🔧 机器故障', desc: '模拟切割机突发故障' },
    { value: 'rush_order' as const, label: '🚨 紧急插单', desc: '插入高优先级紧急订单' },
    { value: 'reoptimize' as const, label: '🔄 全局重优化', desc: '重新优化全部未加工钢板' },
  ];

  // Preset fault scenarios for one-click injection
  const faultPresets = [
    { label: 'N5 轻故障', time: 6, machine: 'N5', dur: 1.5, desc: 'N5第6h故障1.5h' },
    { label: 'N5 中故障', time: 12, machine: 'N5', dur: 3.0, desc: 'N5第12h故障3h' },
    { label: 'N2 严重故障', time: 18, machine: 'N2', dur: 6.0, desc: 'N2第18h故障6h' },
    { label: 'N8 轻故障', time: 24, machine: 'N8', dur: 2.0, desc: 'N8第24h故障2h' },
    { label: '双机故障', time: 8, machine: 'N5', dur: 4.0, desc: 'N5第8h故障4h(压测)' },
  ];

  const applyPreset = useCallback((preset: typeof faultPresets[0]) => {
    setScenario('machine_failure');
    setFaultTime(preset.time);
    setFaultMachine(preset.machine);
    setFaultDuration(preset.dur);
  }, []);

  const disabled = !runId || !currentResult;

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto' }}>
      <Title level={4}>⚡ 动态重调度仿真</Title>

      {!runId && (
        <Alert
          type="info"
          message="请先运行排产模型，然后在此处进行重调度仿真"
          showIcon
          style={{ marginBottom: 16 }}
        />
      )}

      {/* Scenario Selection */}
      <Card size="small" style={{ marginBottom: 16 }}>
        <Space direction="vertical" style={{ width: '100%' }}>
          <Text strong>场景类型</Text>
          <Space wrap>
            {scenarioOptions.map((opt) => (
              <Card
                key={opt.value}
                size="small"
                hoverable
                onClick={() => { setScenario(opt.value); setResult(null); setError(null); }}
                style={{
                  cursor: 'pointer',
                  border: scenario === opt.value ? `2px solid ${COLORS.primary}` : `1px solid ${COLORS.border}`,
                  background: scenario === opt.value ? COLORS.primaryLight : 'var(--bg-card, #ffffff)',
                  minWidth: 180,
                }}
              >
                <Text strong={scenario === opt.value}>{opt.label}</Text>
                <br />
                <Text type="secondary" style={{ fontSize: FONT.sizeXs }}>{opt.desc}</Text>
              </Card>
            ))}
          </Space>
        </Space>
      </Card>

      {/* Scenario-specific params */}
      {scenario === 'machine_failure' && (
        <Card size="small" title={<><AlertOutlined /> 故障参数</>} style={{ marginBottom: 16 }}>
          <Row gutter={24}>
            <Col span={8}>
              <Text>故障发生时间</Text>
              <Slider
                min={1} max={Math.floor((currentResult?.makespanHours ?? 60) * 0.8)} step={0.5}
                value={faultTime}
                onChange={(v) => setFaultTime(v)}
                marks={{ 0: '0h', 12: '12h', 24: '24h', 36: '36h', 48: '48h' }}
              />
              <InputNumber value={faultTime} onChange={(v) => v != null && setFaultTime(v)}
                min={0} max={60} step={0.5} addonAfter="h" style={{ width: '100%' }} />
            </Col>
            <Col span={8}>
              <Text>故障机器</Text>
              <Select
                value={faultMachine}
                onChange={setFaultMachine}
                style={{ width: '100%', marginTop: 8 }}
                options={machines.map((m) => ({ value: m, label: m }))}
              />
            </Col>
            <Col span={8}>
              <Text>故障持续时长</Text>
              <Slider
                min={0.5} max={12} step={0.5}
                value={faultDuration}
                onChange={(v) => setFaultDuration(v)}
                marks={{ 1: '1h', 4: '4h', 8: '8h', 12: '12h' }}
              />
              <InputNumber value={faultDuration} onChange={(v) => v != null && setFaultDuration(v)}
                min={0.5} max={24} step={0.5} addonAfter="h" style={{ width: '100%' }} />
            </Col>
          </Row>
          <div style={{ marginTop: 12, borderTop: '1px solid var(--divider, #F1F5F9)', paddingTop: 12 }}>
            <Text type="secondary" style={{ marginRight: 8 }}>⚡ 预设场景:</Text>
            <Space wrap>
              {faultPresets.map((p, i) => (
                <Button
                  key={i}
                  size="small"
                  type={faultTime === p.time && faultMachine === p.machine && faultDuration === p.dur ? 'primary' : 'default'}
                  onClick={() => applyPreset(p)}
                  title={p.desc}
                >
                  {p.label}
                </Button>
              ))}
            </Space>
          </div>
        </Card>
      )}

      {scenario === 'rush_order' && (
        <Card size="small" title={<><PlusCircleOutlined /> 插单参数</>} style={{ marginBottom: 16 }}>
          <Alert
            type="info"
            message="紧急插单：将新钢板以最高优先级插入当前排程队列最前端，剩余钢板重优化"
            showIcon
            style={{ marginBottom: 12 }}
          />
          <Text type="secondary">插单钢板数由后端从当前排程自动选取最高优先级的未加工钢板模拟</Text>
        </Card>
      )}

      {scenario === 'reoptimize' && (
        <Card size="small" title={<><ReloadOutlined /> 全局重优化</>} style={{ marginBottom: 16 }}>
          <Text type="secondary">
            冻结已开始加工的钢板，对剩余 {currentResult ? Math.floor(currentResult.ganttData.length * 0.7) : '—'} 张钢板
            进行全局重优化（SA+Tabu, ≤5秒响应）
          </Text>
        </Card>
      )}

      {/* Trigger */}
      <Button
        type="primary"
        size="large"
        block
        icon={<ThunderboltOutlined />}
        loading={loading}
        disabled={disabled}
        onClick={handleReschedule}
        style={{ marginBottom: 24, height: 48 }}
      >
        {loading ? '正在重调度...' : `执行${scenarioOptions.find(o => o.value === scenario)?.label}`}
      </Button>

      {error && (
        <Alert type="error" message={error} showIcon closable style={{ marginBottom: 16 }} />
      )}

      {/* Results */}
      {result && (
        <>
          <Alert
            type="success"
            message={`重调度完成 (${result.reschedule_time_s.toFixed(2)}s)`}
            showIcon
            icon={<CheckCircleOutlined />}
            style={{ marginBottom: 16 }}
          />

          <Card size="small" title="重调度结果" style={{ marginBottom: 16 }}>
            <Row gutter={16}>
              <Col span={6}>
                <Statistic title="冻结钢板" value={result.frozen_plates} suffix="张"
                  prefix={<CheckCircleOutlined style={{ color: COLORS.success }} />} />
              </Col>
              <Col span={6}>
                <Statistic title="重优化钢板" value={result.reoptimized_plates} suffix="张"
                  prefix={<ReloadOutlined style={{ color: COLORS.primary }} />} />
              </Col>
              <Col span={6}>
                <Statistic title="新完工时间" value={result.makespanHours.toFixed(1)} suffix="h"
                  prefix={<ClockCircleOutlined style={{ color: COLORS.warning }} />} />
              </Col>
              <Col span={6}>
                <Statistic title="响应时间" value={result.reschedule_time_s.toFixed(2)} suffix="s"
                  valueStyle={{ color: result.reschedule_time_s < 5 ? COLORS.success : COLORS.error }} />
              </Col>
            </Row>
          </Card>

          <Card size="small" title="重调度后 KPI" style={{ marginBottom: 16 }}>
            <Descriptions column={4} size="small" bordered>
              <Descriptions.Item label="总完工时间">
                <Tag color="blue">{(result.metrics['总完工时间(h)'] ?? 0).toFixed(1)}h</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="齐套跨度">
                <Tag color="orange">{(result.metrics['加权平均齐套跨度(h)'] ?? 0).toFixed(2)}h</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="产能">
                <Tag color="green">{(result.metrics['整体产能(张板/班)'] ?? 0).toFixed(1)} 张/班</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="平均稼动率">
                <Tag color="purple">{((result.metrics['切割机平均利用率'] ?? 0) * 100).toFixed(1)}%</Tag>
              </Descriptions.Item>
            </Descriptions>
          </Card>
        </>
      )}
    </div>
  );
};

export default ReschedulePanel;
