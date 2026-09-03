import React, { useMemo } from 'react';
import { Card, Typography, Row, Col, Progress, Statistic, Table, Tag, Space } from 'antd';
import ReactECharts from 'echarts-for-react';
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  SyncOutlined,
  InboxOutlined,
} from '@ant-design/icons';
import type { KitSpanItem } from '../types';
import { COLORS, CHART } from '../theme';

const { Title, Text } = Typography;

interface Props {
  kitData: KitSpanItem[];
  makespanHours: number;
}

interface SegmentKitInfo {
  segment: string;
  groups: { priority: number; partCount: number; completion: number; span: number }[];
  totalParts: number;
  earliestComplete: number;
  latestComplete: number;
  span: number;
}

const KitDashboard: React.FC<Props> = ({ kitData, makespanHours }) => {
  // ── ALL HOOKS FIRST (P0-1 fix: must call before any conditional return) ──

  // Parse kit data into per-segment hierarchy
  const segmentInfo = useMemo(() => {
    if (!kitData || kitData.length === 0) return [];
    const map = new Map<string, SegmentKitInfo>();
    const parsed = kitData.map((d) => {
      // label format: "AP2-P3" or "SEG01-P5"
      const parts = d.label.split('-P');
      return {
        segment: parts[0] || d.label,
        priority: parseInt(parts[1] || '1', 10),
        partCount: d.partCount,
        completion: d.completion,
        span: d.span,
      };
    });

    for (const item of parsed) {
      if (!map.has(item.segment)) {
        map.set(item.segment, {
          segment: item.segment,
          groups: [],
          totalParts: 0,
          earliestComplete: Infinity,
          latestComplete: 0,
          span: 0,
        });
      }
      const seg = map.get(item.segment)!;
      seg.groups.push({
        priority: item.priority,
        partCount: item.partCount,
        completion: item.completion,
        span: item.span,
      });
      seg.totalParts += item.partCount;
      seg.earliestComplete = Math.min(seg.earliestComplete, item.completion - item.span);
      seg.latestComplete = Math.max(seg.latestComplete, item.completion);
      seg.span = seg.latestComplete - seg.earliestComplete;
    }

    // Sort by latest completion (latest first = bottleneck segments)
    return [...map.values()].sort((a, b) => b.latestComplete - a.latestComplete);
  }, [kitData]);

  // Overall stats
  const overallStats = useMemo(() => {
    // P0-10 FIX: guard against undefined/null kitData
    const safeData = Array.isArray(kitData) ? kitData : [];
    if (safeData.length === 0) {
      return { maxCompletion: 0, avgCompletion: 0, totalParts: 0, readyAtMid: 0, totalGroups: 0 };
    }
    const allCompletions = safeData.map((d) => d.completion);
    const maxCompletion = Math.max(...allCompletions, 1);
    // P0-11 FIX: guard division by zero
    const avgCompletion = allCompletions.reduce((a, b) => a + b, 0) / (allCompletions.length || 1);
    const totalParts = safeData.reduce((a, b) => a + b.partCount, 0);

    // Kit readiness at makespan midpoint
    const midTime = makespanHours / 2;
    const readyAtMid = safeData.filter((d) => d.completion <= midTime).length;
    const totalGroups = safeData.length;

    return { maxCompletion, avgCompletion, totalParts, readyAtMid, totalGroups };
  }, [kitData, makespanHours]);

  // Timeline chart option
  const timelineOption = useMemo(() => ({
    tooltip: {
      trigger: 'item',
      formatter: (p: any) => {
        const d = p.data;
        return `<b>${d.label}</b><br/>零件数: ${d.partCount}<br/>首件到达: ${d.firstArrival?.toFixed(2)}h<br/>齐套完成: ${d.completion?.toFixed(2)}h<br/>跨度: ${d.span?.toFixed(2)}h`;
      },
    },
    grid: { left: 150, right: 60, top: 20, bottom: 40 },
    xAxis: {
      type: 'value',
      name: '时间 (h)',
      max: Math.ceil(makespanHours),
      axisLabel: { formatter: '{value}h' },
    },
    yAxis: {
      type: 'category',
      data: kitData.map((d) => d.label),
      inverse: true,
      axisLabel: { fontSize: 10 },
    },
    series: [
      // Arrival to completion span (grey base)
      {
        name: '等待区间',
        type: 'bar',
        stack: 'x',
        data: kitData.map((d) => ({
          name: d.label,
          value: d.firstArrival,
          label: d.label,
          partCount: d.partCount,
          firstArrival: d.firstArrival,
          completion: d.completion,
          span: d.span,
          itemStyle: { color: 'transparent' },
        })),
        barWidth: '60%',
      },
      // Active span (colored by span duration)
      {
        name: '齐套跨度',
        type: 'bar',
        stack: 'x',
        data: kitData.map((d) => {
          const ratio = d.span / Math.max(...kitData.map((k) => k.span), 1);
          let color: string = COLORS.success;
          if (ratio > 0.5) color = COLORS.warning;
          if (ratio > 0.8) color = COLORS.error;
          return {
            name: d.label,
            value: d.span,
            label: d.label,
            partCount: d.partCount,
            firstArrival: d.firstArrival,
            completion: d.completion,
            span: d.span,
            itemStyle: { color, borderRadius: [6, 6, 6, 6] },
          };
        }),
        barWidth: '60%',
        label: {
          show: true,
          position: 'right',
          formatter: (p: any) => `${p.value?.toFixed(1)}h`,
          fontSize: 9,
        },
      },
    ],
  }), [kitData, makespanHours]);

  // Segment progress chart
  const segmentChartOption = useMemo(() => ({
    tooltip: {
      trigger: 'axis',
      formatter: (params: any) => {
        const seg = segmentInfo[params[0]?.dataIndex];
        if (!seg) return '';
        return `<b>${seg.segment}</b><br/>零件总数: ${seg.totalParts}<br/>齐套组数: ${seg.groups.length}<br/>完成时间: ${seg.latestComplete.toFixed(1)}h`;
      },
    },
    grid: { left: 100, right: 80, top: 20, bottom: 40 },
    xAxis: {
      type: 'category',
      data: segmentInfo.map((s) => s.segment),
      axisLabel: { fontSize: 10, rotate: 30 },
    },
    yAxis: [
      {
        type: 'value',
        name: '时间 (h)',
        axisLabel: { formatter: '{value}h' },
      },
      {
        type: 'value',
        name: '零件数',
      },
    ],
    series: [
      {
        name: '齐套完成时间',
        type: 'bar',
        data: segmentInfo.map((s) => ({
          value: s.latestComplete,
          itemStyle: {
            color: s.latestComplete > makespanHours * 0.8 ? COLORS.warning : CHART.gantt.N5,
            borderRadius: [6, 6, 0, 0],
          },
        })),
        label: {
          show: true,
          position: 'top',
          formatter: '{c}h',
          fontSize: 10,
        },
      },
      {
        name: '零件数',
        type: 'line',
        yAxisIndex: 1,
        data: segmentInfo.map((s) => s.totalParts),
        smooth: true,
        lineStyle: { color: COLORS.primary, width: 2 },
        itemStyle: { color: COLORS.primary },
        symbol: 'circle',
        symbolSize: 6,
      },
    ],
  }), [segmentInfo, makespanHours]);

  // Table columns for group details
  const groupColumns = [
    { title: '分段', dataIndex: 'segment', key: 'segment', width: 80, sorter: (a: any, b: any) => a.segment.localeCompare(b.segment) },
    { title: '齐套组', dataIndex: 'priority', key: 'priority', width: 60, render: (_: any, r: any) => <Tag>P{r.priority}</Tag> },
    { title: '零件数', dataIndex: 'partCount', key: 'partCount', width: 70, sorter: (a: any, b: any) => a.partCount - b.partCount },
    {
      title: '完成时间', dataIndex: 'completion', key: 'completion', width: 100,
      render: (v: number) => `${v.toFixed(1)}h`,
      sorter: (a: any, b: any) => a.completion - b.completion,
    },
    {
      title: '跨度', dataIndex: 'span', key: 'span', width: 80,
      render: (v: number) => {
        const maxSpan = Math.max(...kitData.map((d) => d.span), 1);
        return <Tag color={v > maxSpan * 0.7 ? 'orange' : 'green'}>{v.toFixed(1)}h</Tag>;
      },
    },
    {
      title: '状态', key: 'status', width: 80,
      render: (_: any, r: any) => {
        if (r.completion <= makespanHours * 0.5) return <Tag color="green" icon={<CheckCircleOutlined />}>已完成</Tag>;
        if (r.completion <= makespanHours * 0.8) return <Tag color="blue" icon={<SyncOutlined />}>进行中</Tag>;
        return <Tag color="orange" icon={<ClockCircleOutlined />}>瓶颈</Tag>;
      },
    },
  ];

  const groupTableData = kitData.map((d) => {
    const parts = d.label.split('-P');
    return {
      segment: parts[0] || d.label,
      priority: parseInt(parts[1] || '1', 10),
      partCount: d.partCount,
      completion: d.completion,
      span: d.span,
      key: d.label,
    };
  });

  // ── Empty state guard (P0-1 fix: AFTER all hooks) ──
  if (!kitData || kitData.length === 0) {
    return (
      <div style={{ maxWidth: 1200, margin: '0 auto', padding: 40, textAlign: 'center' }}>
        <Title level={4}>📦 齐套配盘看板</Title>
        <Text type="secondary">暂无齐套数据，请先运行排产模型</Text>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto' }}>
      <Title level={4}>📦 齐套配盘看板</Title>

      {/* Overall KPI row */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="齐套组总数"
              value={overallStats.totalGroups}
              prefix={<InboxOutlined style={{ color: COLORS.primary }} />}
              suffix={`组 / ${segmentInfo.length} 分段`}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="零件总数"
              value={overallStats.totalParts}
              prefix={<InboxOutlined style={{ color: CHART.gantt.N5 }} />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="平均齐套完成"
              value={overallStats.avgCompletion.toFixed(1)}
              suffix="h"
              prefix={<ClockCircleOutlined style={{ color: COLORS.warning }} />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="中位线前完成率"
              value={`${(overallStats.readyAtMid / (overallStats.totalGroups || 1) * 100).toFixed(0)}%`}
              prefix={<CheckCircleOutlined style={{ color: COLORS.success }} />}
              valueStyle={{ color: overallStats.readyAtMid > overallStats.totalGroups * 0.7 ? COLORS.success : COLORS.warning }}
            />
          </Card>
        </Col>
      </Row>

      {/* Global kit readiness gauge */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={8}>
          <Card size="small" title="🎯 全局齐套率">
            <Progress
              type="dashboard"
              percent={Math.round((overallStats.readyAtMid / (overallStats.totalGroups || 1)) * 100)}
              strokeColor={{
                '0%': COLORS.success,
                '50%': COLORS.warning,
                '80%': COLORS.error,
              }}
              format={() => `${Math.round((overallStats.readyAtMid / (overallStats.totalGroups || 1)) * 100)}%`}
            />
            <Text type="secondary" style={{ display: 'block', textAlign: 'center', marginTop: 8 }}>
              中点线({(makespanHours / 2).toFixed(0)}h)前齐套率
            </Text>
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small" title="⚠️ 瓶颈分段">
            {(() => {
              const bottleneck = segmentInfo[0]; // sorted by latestCompletion desc
              const delay = bottleneck ? bottleneck.latestComplete - makespanHours * 0.5 : 0;
              return (
                <div style={{ textAlign: 'center' }}>
                  <Text strong style={{ fontSize: 24, color: COLORS.warning }}>
                    {bottleneck?.segment || '-'}
                  </Text>
                  <br />
                  <Text type="secondary">
                    {bottleneck ? `${bottleneck.latestComplete.toFixed(1)}h 完成 · ${bottleneck.totalParts}件` : ''}
                  </Text>
                  {delay > 0 && (
                    <Tag color="orange" style={{ marginTop: 8 }}>
                      延迟 {delay.toFixed(1)}h
                    </Tag>
                  )}
                </div>
              );
            })()}
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small" title="📈 齐套效率">
            <Statistic
              title="平均组跨度"
              value={(kitData.reduce((s, d) => s + d.span, 0) / (kitData.length || 1)).toFixed(2)}
              suffix="h"
              prefix={<ClockCircleOutlined />}
            />
            <Statistic
              title="最大组跨度"
              value={Math.max(...kitData.map(d => d.span)).toFixed(2)}
              suffix="h"
              valueStyle={{ color: COLORS.warning, fontSize: 16 }}
            />
          </Card>
        </Col>
      </Row>

      {/* Per-segment progress */}
      <Card title="📊 各分段齐套进度" size="small" style={{ marginBottom: 16 }}>
        <Row gutter={[16, 12]}>
          {segmentInfo.map((seg) => {
            const completedGroups = seg.groups.filter((g) => g.completion <= makespanHours * 0.75).length;
            const pct = (completedGroups / seg.groups.length) * 100;
            return (
              <Col span={8} key={seg.segment}>
                <Card size="small" hoverable>
                  <Space direction="vertical" style={{ width: '100%' }} size={4}>
                    <Text strong>{seg.segment}</Text>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      {seg.groups.length} 组 · {seg.totalParts} 件 · 完成: {seg.latestComplete.toFixed(1)}h
                    </Text>
                    <Progress
                      percent={Math.round(pct)}
                      size="small"
                      strokeColor={pct >= 80 ? COLORS.success : pct >= 50 ? COLORS.primary : COLORS.warning}
                      format={() => `${completedGroups}/${seg.groups.length} 组`}
                    />
                  </Space>
                </Card>
              </Col>
            );
          })}
        </Row>
      </Card>

      {/* Segment completion chart */}
      <Card size="small" style={{ marginBottom: 16 }}>
        <ReactECharts option={segmentChartOption} style={{ height: 300 }} />
      </Card>

      {/* Kit span timeline */}
      <Card title="⏱ 齐套组时间线（首件到达→齐套完成）" size="small" style={{ marginBottom: 16 }}>
        <ReactECharts option={timelineOption} style={{ height: Math.max(400, kitData.length * 28 + 80) }} />
      </Card>

      {/* Group detail table */}
      <Card title="📋 齐套组明细" size="small">
        <Table
          dataSource={groupTableData}
          columns={groupColumns}
          rowKey="key"
          pagination={{ pageSize: 15, size: 'small' }}
          size="small"
          bordered
        />
      </Card>
    </div>
  );
};

export default KitDashboard;
