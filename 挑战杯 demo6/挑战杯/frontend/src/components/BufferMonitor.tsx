import React, { useMemo } from 'react';
import { Card, Typography, Row, Col, Progress, Statistic, Space, Tag, Alert, Empty } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { StageItem, BufferTimeseriesEntry, BufferCapacityConfig } from '../types';
import { COLORS, CHART } from '../theme';

const { Title, Text } = Typography;

// ── 物理约束容量配置（与后端 ModelConfig 严格对齐）──
const PHYSICAL_BUFFER_CAPS: Record<string, number> = {
  N2: 10,
  N5: 18,
};
const BEVEL_BUFFER_CAP = 3;   // 坡口缓存区容量（题目规定）
const HALF_BUFFER_CAP = 30;   // 半框缓存区容量（待齐套料框，ModelConfig.half_buffer_capacity）
const KIT_BUFFER_CAP = 15;    // 齐套缓存区容量（已齐套待取走，ModelConfig.kit_buffer_capacity）

interface Props {
  stages: StageItem[];
  makespanHours: number;
  bufferCapacity?: number;  // 废弃：保留向后兼容，优先使用 bufferConfig
  bufferTimeseries?: BufferTimeseriesEntry[];
  bufferConfig?: BufferCapacityConfig;  // 后端API返回的真实容量配置
  deadlockWarnings?: string[];  // 后端返回的缓存死锁/溢出告警
  deadlockCount?: number;       // 告警数量
}

const BufferMonitor: React.FC<Props> = ({ stages, makespanHours, bufferCapacity, bufferTimeseries, bufferConfig, deadlockWarnings, deadlockCount }) => {
  // ── 优先使用后端API返回的真实容量配置，否则使用物理约束默认值 ──
  const bevelCapacity = bufferConfig?.bevel_capacity ?? BEVEL_BUFFER_CAP;
  const halfCapacity = bufferConfig?.half_capacity ?? HALF_BUFFER_CAP;
  const kitCapacity = bufferConfig?.kit_capacity ?? KIT_BUFFER_CAP;

  // Detect machine count from timeseries data
  const machineNames = useMemo(() => {
    if (!bufferTimeseries || bufferTimeseries.length === 0) return ['N2', 'N5'];
    const names: string[] = [];
    // Scan all entries for robustness, not just the first
    for (const entry of bufferTimeseries.slice(0, 5)) {
      for (const key of Object.keys(entry)) {
        if (key.endsWith('_码垛占用')) {
          const name = key.replace('_码垛占用', '');
          if (!names.includes(name)) names.push(name);
        }
      }
    }
    return names.length > 0 ? names.sort() : ['N2', 'N5'];
  }, [bufferTimeseries]);

  // ── 使用后端返回的真实差异化容量（N2=10, N5=18），或物理约束默认值 ──
  const perMachineCapacity = useMemo(() => {
    const cap: Record<string, number> = {};
    // 优先：后端 API 返回的 machine_caps
    if (bufferConfig?.machine_caps && Object.keys(bufferConfig.machine_caps).length > 0) {
      for (const [m, c] of Object.entries(bufferConfig.machine_caps)) {
        cap[m] = c;
      }
      // 填充未在配置中的机器（如N8备机）：按题目剩余容量分配
      const configured = Object.keys(cap);
      const unconfigured = machineNames.filter((m) => !configured.includes(m));
      if (unconfigured.length > 0) {
        const configuredTotal = Object.values(cap).reduce((a, b) => a + b, 0);
        const effectiveTotal = bufferConfig?.machine_caps
          ? Math.max(28, configuredTotal)  // 默认总容量28=10+18
          : 28;
        const remaining = Math.max(1, effectiveTotal - configuredTotal);
        const perExtra = Math.floor(remaining / unconfigured.length);
        unconfigured.forEach((m, i) => {
          cap[m] = i < unconfigured.length - 1 ? perExtra : remaining - perExtra * (unconfigured.length - 1);
        });
      }
      return cap;
    }
    // 回退：使用物理约束硬编码默认值（N2=10, N5=18, 其余均分剩余容量）
    const defaultTotal = 28;  // N2(10) + N5(18)
    const knownTotal = machineNames.reduce((s, m) => s + (PHYSICAL_BUFFER_CAPS[m] ?? 0), 0);
    const remaining = Math.max(1, defaultTotal - knownTotal);
    const unknown = machineNames.filter((m) => !(m in PHYSICAL_BUFFER_CAPS));
    machineNames.forEach((m) => {
      if (m in PHYSICAL_BUFFER_CAPS) {
        cap[m] = PHYSICAL_BUFFER_CAPS[m];
      } else {
        cap[m] = Math.max(1, Math.floor(remaining / Math.max(1, unknown.length)));
      }
    });
    return cap;
  }, [machineNames, bufferConfig]);

  // Parse real buffer timeseries data
  const emptyResult = useMemo(() => ({
    samples: [] as { time: number; [key: string]: number }[],
    machineMax: {} as Record<string, number>,
    machineAvg: {} as Record<string, number>,
    machineCurrent: {} as Record<string, number>,
    machineCapacity: {} as Record<string, number>,
    bevelMax: 0, bevelAvg: 0, bevelCurrent: 0,
    halfMax: 0, halfAvg: 0, halfCurrent: 0,
    kitMax: 0, kitAvg: 0, kitCurrent: 0,
  }), []);

  const bufferData = useMemo(() => {
    // P2-3: 无后端真实数据时不执行失真估算，直接返回空结果
    if (!bufferTimeseries || bufferTimeseries.length === 0) {
      return emptyResult;
    }

    // Use real timeseries data
    const samples = bufferTimeseries.map((entry) => {
      const sample: { time: number; [key: string]: number } = { time: entry.time_h };
      for (const m of machineNames) {
        sample[m] = entry[`${m}_码垛占用`] ?? 0;
      }
      sample['bevel'] = entry['坡口缓存占用'] ?? 0;
      sample['half'] = entry['半框缓存占用'] ?? 0;
      sample['kit'] = entry['齐套缓存占用'] ?? 0;
      return sample;
    });

    const result = { ...emptyResult, samples };
    for (const m of machineNames) {
      const vals = samples.map((s) => s[m] ?? 0);
      result.machineMax[m] = Math.max(...vals, 0);
      result.machineAvg[m] = vals.reduce((a, b) => a + b, 0) / Math.max(1, vals.length);
      result.machineCurrent[m] = vals.length > 0 ? vals[vals.length - 1] : 0;
      result.machineCapacity[m] = perMachineCapacity[m] || Math.floor((bufferCapacity ?? 28) / Math.max(1, machineNames.length));
    }

    const bevelVals = samples.map((s) => s['bevel'] ?? 0);
    result.bevelMax = Math.max(...bevelVals, 0);
    result.bevelAvg = bevelVals.reduce((a, b) => a + b, 0) / Math.max(1, bevelVals.length);
    result.bevelCurrent = bevelVals.length > 0 ? bevelVals[bevelVals.length - 1] : 0;

    const halfVals = samples.map((s) => s['half'] ?? 0);
    result.halfMax = Math.max(...halfVals, 0);
    result.halfAvg = halfVals.reduce((a, b) => a + b, 0) / Math.max(1, halfVals.length);
    result.halfCurrent = halfVals.length > 0 ? halfVals[halfVals.length - 1] : 0;

    const kitVals = samples.map((s) => s['kit'] ?? 0);
    result.kitMax = Math.max(...kitVals, 0);
    result.kitAvg = kitVals.reduce((a, b) => a + b, 0) / Math.max(1, kitVals.length);
    result.kitCurrent = kitVals.length > 0 ? kitVals[kitVals.length - 1] : 0;

    return result;
  }, [bufferTimeseries, stages, makespanHours, machineNames, perMachineCapacity, bufferCapacity]);

  // Chart option for buffer occupancy over time
  const chartOption = useMemo(() => {
    const series: any[] = [];

    // Machine buffer lines
    const machineColors = [CHART.gantt.N2 || '#2E74B5', CHART.gantt.N5 || '#70AD47', '#8B5CF6', '#F59E0B'];
    machineNames.forEach((m, i) => {
      const capacity = bufferData.machineCapacity[m] ?? 25;
      const predictThreshold = Math.floor(capacity * 0.70); // 70% predictive dispatch threshold
      series.push({
        name: `${m}码垛区`,
        type: 'line',
        data: bufferData.samples.map((s: { time: number; [key: string]: number }) => [s.time, s[m] ?? 0]),
        smooth: true,
        lineStyle: { color: machineColors[i % machineColors.length], width: 2 },
        areaStyle: { color: machineColors[i % machineColors.length] + '20' },
        symbol: 'none',
        markLine: {
          silent: true,
          data: [
            { yAxis: capacity, label: { formatter: `${m}上限: ${capacity}` }, lineStyle: { color: COLORS.error, type: 'dashed', width: 2 } },
            { yAxis: predictThreshold, label: { formatter: `预调度: ${predictThreshold}` }, lineStyle: { color: COLORS.warning, type: 'dotted', width: 1.5 } },
          ],
        },
      });
    });

    // Bevel buffer
    series.push({
      name: '坡口缓存',
      type: 'line',
      data: bufferData.samples.map((s: { time: number; [key: string]: number }) => [s.time, s['bevel'] ?? 0]),
      smooth: true,
      lineStyle: { color: '#06B6D4', width: 2 },
      areaStyle: { color: '#06B6D420' },
      symbol: 'none',
      markLine: {
        silent: true,
        data: [{ yAxis: bevelCapacity, label: { formatter: `坡口上限: ${bevelCapacity}` }, lineStyle: { color: COLORS.warning, type: 'dashed' } }],
      },
    });

    // Half buffer (半框缓存区)
    series.push({
      name: '半框缓存',
      type: 'line',
      data: bufferData.samples.map((s: { time: number; [key: string]: number }) => [s.time, s['half'] ?? 0]),
      smooth: true,
      lineStyle: { color: '#F59E0B', width: 2 },
      areaStyle: { color: '#F59E0B20' },
      symbol: 'none',
      markLine: {
        silent: true,
        data: [{ yAxis: halfCapacity, label: { formatter: `半框上限: ${halfCapacity}` }, lineStyle: { color: COLORS.warning, type: 'dashed' } }],
      },
    });

    // Kit buffer
    series.push({
      name: '齐套缓存',
      type: 'line',
      data: bufferData.samples.map((s: { time: number; [key: string]: number }) => [s.time, s['kit'] ?? 0]),
      smooth: true,
      lineStyle: { color: '#EC4899', width: 2 },
      areaStyle: { color: '#EC489920' },
      symbol: 'none',
      markLine: {
        silent: true,
        data: [{ yAxis: kitCapacity, label: { formatter: `齐套上限: ${kitCapacity}` }, lineStyle: { color: COLORS.error, type: 'dashed' } }],
      },
    });

    return {
      tooltip: {
        trigger: 'axis',
      },
      legend: { data: [...machineNames.map((m) => `${m}码垛区`), '坡口缓存', '半框缓存', '齐套缓存'], top: 0, type: 'scroll' },
      grid: { left: 60, right: 30, top: 40, bottom: 40 },
      xAxis: {
        type: 'value',
        name: '时间 (h)',
        max: makespanHours,
        axisLabel: { formatter: '{value}h' },
      },
      yAxis: {
        type: 'value',
        name: '占用 (件)',
      },
      series,
    };
  }, [bufferData, makespanHours, machineNames, bevelCapacity, halfCapacity, kitCapacity]);

  // Detect if using real data
  const hasRealData = bufferTimeseries && bufferTimeseries.length > 0;

  // P2-3: 无后端数据时直接渲染空状态，不使用失真估算
  if (!hasRealData) {
    return (
      <div style={{ maxWidth: 1200, margin: '0 auto' }}>
        <Title level={4}>📊 四级缓存区占用监控</Title>
        <Card style={{ textAlign: 'center', padding: 60 }}>
          <Empty
            description={
              <Space direction="vertical" size={8}>
                <Text strong>暂无缓存监控数据</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  请先运行排产模型，获取离散事件仿真生成的缓存区占用时间序列
                </Text>
              </Space>
            }
          />
        </Card>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto' }}>
      <Title level={4}>📊 四级缓存区占用监控</Title>

      {/* KPI cards for machine buffers */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        {machineNames.map((m) => {
          const capacity = bufferData.machineCapacity[m] ?? 25;
          const current = bufferData.machineCurrent[m] ?? 0;
          const peak = bufferData.machineMax[m] ?? 0;
          const avg = bufferData.machineAvg[m] ?? 0;
          return (
            <Col span={Math.floor(24 / Math.max(machineNames.length, 1))} key={m}>
              <Card size="small">
                <Statistic
                  title={`${m} 码垛区`}
                  value={current}
                  suffix={`/ ${capacity} 件`}
                  valueStyle={{ color: current > capacity * 0.8 ? COLORS.warning : COLORS.success }}
                />
                <Row gutter={8} style={{ marginTop: 8 }}>
                  <Col span={12}>
                    <Text type="secondary" style={{ fontSize: 11 }}>峰值: {peak}</Text>
                  </Col>
                  <Col span={12}>
                    <Text type="secondary" style={{ fontSize: 11 }}>均值: {avg.toFixed(1)}</Text>
                  </Col>
                </Row>
                <Progress
                  percent={Math.min(100, (current / Math.max(1, capacity)) * 100)}
                  strokeColor={current > capacity * 0.8 ? COLORS.warning : COLORS.success}
                  size="small"
                  style={{ marginTop: 4 }}
                />
              </Card>
            </Col>
          );
        })}
      </Row>

      {/* Bevel + Half + Kit buffer KPI */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={8}>
          <Card size="small">
            <Statistic
              title="坡口缓存区"
              value={bufferData.bevelCurrent}
              suffix={`/ ${bevelCapacity} 件`}
              valueStyle={{ color: bufferData.bevelCurrent > bevelCapacity * 0.8 ? COLORS.warning : COLORS.success }}
            />
            <Row gutter={8} style={{ marginTop: 8 }}>
              <Col span={12}><Text type="secondary" style={{ fontSize: 11 }}>峰值: {bufferData.bevelMax}</Text></Col>
              <Col span={12}><Text type="secondary" style={{ fontSize: 11 }}>均值: {bufferData.bevelAvg.toFixed(1)}</Text></Col>
            </Row>
            <Progress
              percent={Math.min(100, (bufferData.bevelCurrent / Math.max(1, bevelCapacity)) * 100)}
              strokeColor={bufferData.bevelCurrent > bevelCapacity * 0.8 ? COLORS.warning : COLORS.success}
              size="small" style={{ marginTop: 4 }}
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small">
            <Statistic
              title="半框缓存区"
              value={bufferData.halfCurrent}
              suffix={`/ ${halfCapacity} 框`}
              valueStyle={{ color: bufferData.halfCurrent > halfCapacity * 0.8 ? COLORS.warning : COLORS.success }}
            />
            <Row gutter={8} style={{ marginTop: 8 }}>
              <Col span={12}><Text type="secondary" style={{ fontSize: 11 }}>峰值: {bufferData.halfMax}</Text></Col>
              <Col span={12}><Text type="secondary" style={{ fontSize: 11 }}>均值: {bufferData.halfAvg.toFixed(1)}</Text></Col>
            </Row>
            <Progress
              percent={Math.min(100, (bufferData.halfCurrent / Math.max(1, halfCapacity)) * 100)}
              strokeColor={bufferData.halfCurrent > halfCapacity * 0.8 ? COLORS.warning : COLORS.success}
              size="small" style={{ marginTop: 4 }}
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small">
            <Statistic
              title="齐套缓存区"
              value={bufferData.kitCurrent}
              suffix={`/ ${kitCapacity} 框`}
              valueStyle={{ color: bufferData.kitCurrent > kitCapacity * 0.8 ? COLORS.warning : COLORS.success }}
            />
            <Row gutter={8} style={{ marginTop: 8 }}>
              <Col span={12}><Text type="secondary" style={{ fontSize: 11 }}>峰值: {bufferData.kitMax}</Text></Col>
              <Col span={12}><Text type="secondary" style={{ fontSize: 11 }}>均值: {bufferData.kitAvg.toFixed(1)}</Text></Col>
            </Row>
            <Progress
              percent={Math.min(100, (bufferData.kitCurrent / Math.max(1, kitCapacity)) * 100)}
              strokeColor={bufferData.kitCurrent > kitCapacity * 0.8 ? COLORS.warning : COLORS.success}
              size="small" style={{ marginTop: 4 }}
            />
          </Card>
        </Col>
      </Row>

      {/* Occupancy timeline chart */}
      <Card>
        <ReactECharts option={chartOption} style={{ height: 380 }} />
      </Card>

      {/* 死锁与溢出告警（P1-2: 后端齐套缓存区溢出事件前端显式告警） */}
      {deadlockCount != null && deadlockCount > 0 && (
        <Alert
          type="warning"
          showIcon
          message={`⚠️ 缓存区告警：检测到 ${deadlockCount} 次潜在溢出/死锁事件`}
          description={
            deadlockWarnings && deadlockWarnings.length > 0 ? (
              <ul style={{ margin: '4px 0', paddingLeft: 20, fontSize: 12 }}>
                {deadlockWarnings.slice(0, 5).map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
                {deadlockWarnings.length > 5 && <li>... 共 {deadlockWarnings.length} 条告警</li>}
              </ul>
            ) : (
              '建议增大 kit_buffer_capacity 或加速齐套出库'
            )
          }
          style={{ marginBottom: 12 }}
        />
      )}

      {/* Status summary */}
      <Card size="small" style={{ marginTop: 12 }}>
        <Space wrap>
          {machineNames.map((m) => {
            const capacity = bufferData.machineCapacity[m] ?? 25;
            const peak = bufferData.machineMax[m] ?? 0;
            return (
              <Tag key={m} color={peak <= capacity ? 'green' : 'red'}>
                {m}码垛 {peak <= capacity ? '未溢出' : `溢出(${peak}/${capacity})`}
              </Tag>
            );
          })}
          <Tag color={bufferData.bevelMax <= bevelCapacity ? 'green' : 'red'}>
            坡口缓存 {bufferData.bevelMax <= bevelCapacity ? '正常' : `溢出(${bufferData.bevelMax}/${bevelCapacity})`}
          </Tag>
          <Tag color={bufferData.halfMax <= halfCapacity ? 'green' : 'red'}>
            半框缓存 {bufferData.halfMax <= halfCapacity ? '正常' : `溢出(${bufferData.halfMax}/${halfCapacity})`}
          </Tag>
          <Tag color={bufferData.kitMax <= kitCapacity ? 'green' : 'red'}>
            齐套缓存 {bufferData.kitMax <= kitCapacity ? '正常' : `溢出(${bufferData.kitMax}/${kitCapacity})`}
          </Tag>
          <Text type="secondary" style={{ fontSize: 11 }}>
            {hasRealData ? '数据来源: 离散事件仿真' : '数据来源: 前端估算'}
          </Text>
        </Space>
      </Card>
    </div>
  );
};

export default BufferMonitor;
