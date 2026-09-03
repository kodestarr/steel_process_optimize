import React from 'react';
import { Card, Statistic, Row, Col, Table, Tag, Typography, Space } from 'antd';
import {
  ClockCircleOutlined,
  FieldTimeOutlined,
  ThunderboltOutlined,
  DashboardOutlined,
} from '@ant-design/icons';
import type { RunResponse } from '../types';
import { COLORS, FONT } from '../theme';

const { Title, Text } = Typography;

interface Props {
  data: RunResponse;
  prevData?: RunResponse | null;
}

const ResultOverview: React.FC<Props> = ({ data, prevData }) => {
  const { metrics } = data;
  const opt = metrics.optimized;
  const algorithmName = data.algorithmName ?? 'SA+Tabu（线性评价）';
  const objectiveText = algorithmName.includes('二次')
    ? '优化目标: FIFO相对二次函数 0.40c² + 0.40k² + 0.20l² + 二次惩罚'
    : '优化目标: 0.40×Cmax + 0.40×平均齐套跨度 + 0.20×切割负载差';

  // Dynamically discover machine utilization keys
  const machineUtilKeys = Object.keys(opt).filter(
    (k) => /^[A-Z]\d+利用率$/.test(k) && !k.includes('平均')
  ).sort();

  // 完整指标定义（含单位、改善方向、用于KPI卡片和对比表）
  const metricDefs = [
    { label: '总完工时间', key: '总完工时间(h)', unit: 'h', icon: <ClockCircleOutlined />, better: 'lower' as const },
    { label: '加权平均齐套跨度', key: '加权平均齐套跨度(h)', unit: 'h', icon: <FieldTimeOutlined />, better: 'lower' as const },
    { label: '最大齐套跨度', key: '最大齐套跨度(h)', unit: 'h', icon: <FieldTimeOutlined />, better: 'lower' as const },
    { label: '整体产能', key: '整体产能(张板/班)', unit: '张/班', icon: <ThunderboltOutlined />, better: 'higher' as const },
    { label: '切割负载差', key: '切割负载差(h)', unit: 'h', icon: <ThunderboltOutlined />, better: 'lower' as const },
    { label: '综合稼动率', key: '切割机综合稼动率', unit: '%', icon: <DashboardOutlined />, better: 'higher' as const },
    { label: '纯切割稼动率', key: '切割机纯切割稼动率', unit: '%', icon: <DashboardOutlined />, better: 'higher' as const },
    { label: '班次稼动率', key: '切割机班次稼动率', unit: '%', icon: <DashboardOutlined />, better: 'higher' as const },
    ...machineUtilKeys.map((k) => ({
      label: `${k.replace('利用率', '')} 利用率`, key: k, unit: '%',
      icon: <DashboardOutlined />, better: 'higher' as const,
    })),
  ];

  // KPI 卡片用的指标数组
  const kpis = metricDefs.map((m) => ({ ...m, precision: m.key.includes('利用率') ? 2 : 2 }));

  // 对比表格
  const comparisonCols = [
    { title: '方案', dataIndex: '方案', key: '方案', width: 160 },
    ...metricDefs.map((m) => ({
      title: `${m.label} (${m.unit})`,
      key: m.key,
      render: (_: unknown, row: any) => {
        const val = row[m.key];
        const impKey = m.key + '改善率';
        const imp = row[impKey];
        if (val == null && imp == null) return <span>—</span>;

        const isUtil = m.key.includes('利用率');
        // P0-10 FIX: Full NaN/Infinity defense — never render "NaN" in UI
        const safeVal = Number(val ?? 0);
        const display = (!isFinite(safeVal) || isNaN(safeVal))
          ? 'N/A'
          : isUtil ? `${(safeVal * 100).toFixed(1)}%` : safeVal.toFixed(2);

        // 改善率标签
        let impTag = null;
        if (imp != null && imp !== '' && Math.abs(Number(imp)) > 0.0001) {
          const pct = (Number(imp) * 100).toFixed(1);
          const isBetter = (m.better === 'lower' && Number(imp) > 0) || (m.better === 'higher' && Number(imp) > 0);
          const color = isBetter ? 'green' : 'red';
          const arrow = m.better === 'higher'
            ? (Number(imp) > 0 ? '↑' : '↓')
            : (Number(imp) > 0 ? '↓' : '↑');
          impTag = (
            <Tag color={color} style={{ fontSize: FONT.sizeXs, marginLeft: 4, padding: '0 4px', lineHeight: '16px' }}>
              {arrow} {pct}%
            </Tag>
          );
        }

        return <span>{display}{impTag}</span>;
      },
    })),
  ];

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto' }}>
      <Title level={4}>📈 结果概览（齐套感知优化方案）</Title>

      {/* 工艺参数来源 */}
      <div style={{ marginBottom: 16 }}>
        <Space>
          <Tag color="blue" icon={<ThunderboltOutlined />}>{algorithmName}</Tag>
          {data.checks?.['工艺参数来源'] ? (
            <Tag color={String(data.checks['工艺参数来源']).includes('附件3') ? 'orange' : 'default'} icon={<ThunderboltOutlined />}>
              {String(data.checks['工艺参数来源'])}
            </Tag>
          ) : null}
          <Text type="secondary" style={{ fontSize: FONT.sizeSm }}>{objectiveText}</Text>
        </Space>
      </div>

      {/* OEE定义说明（P1-1: 清晰展示两种OEE口径） */}
      <Card size="small" style={{ marginBottom: 16, background: '#F0F7FF', border: '1px solid #BAE0FF' }}>
        <Space direction="vertical" size={4}>
          <Text strong style={{ fontSize: FONT.sizeSm }}>📐 切割机稼动率口径说明：</Text>
          <Text style={{ fontSize: FONT.sizeXs, color: COLORS.textSecondary }}>
            <Tag color="blue">综合稼动率</Tag> = (总切割工时含辅助) / (切割机数 × makespan) — 对标竞赛 ≥61% 指标；
            <Tag color="cyan" style={{ marginLeft: 8 }}>纯切割稼动率</Tag> = (仅直切+V坡时间) / (切割机数 × makespan) — 排除空行程、划线、穿孔、换板等辅助时间；双工位下残材并行不计
            <Tag color="orange" style={{ marginLeft: 8 }}>班次稼动率</Tag> = 总切割工时 / (切割机数 × 班次数 × 8h × 60min) — 含交接班损耗的8h班制OEE
          </Text>
        </Space>
      </Card>

      {/* KPI 卡片 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        {kpis.map((kpi) => {
          const val = opt[kpi.key];
          const prevVal = prevData?.metrics.optimized[kpi.key];
          // P2-1 修复：防御 NaN（缺失 key 时 val 为 undefined）
          const safeVal = val != null ? Number(val) : 0;
          const displayVal = kpi.key.includes('利用率') ? safeVal * 100 : safeVal;

          let change: 'up' | 'down' | 'same' = 'same';
          if (prevVal != null) {
            const diff = val - prevVal;
            if (Math.abs(diff) > 0.001) change = diff > 0 ? 'up' : 'down';
          }

          return (
            <Col span={6} key={kpi.key}>
              <Card size="small" hoverable>
                <Statistic
                  title={kpi.label}
                  value={displayVal}
                  precision={kpi.precision ?? 2}
                  suffix={kpi.unit}
                  prefix={kpi.icon}
                  valueStyle={{
                    color:
                      change === 'down' && kpi.better === 'lower'
                        ? COLORS.success
                        : change === 'up' && kpi.better === 'higher'
                        ? COLORS.success
                        : change === 'up' && kpi.better === 'lower'
                        ? COLORS.error
                        : change === 'down' && kpi.better === 'higher'
                        ? COLORS.error
                        : undefined,
                  }}
                />
                {prevVal != null && change !== 'same' && (
                  <Text type="secondary" style={{ fontSize: FONT.sizeSm }}>
                    上轮: {kpi.key.includes('利用率') ? (prevVal * 100).toFixed(2) + '%' : prevVal.toFixed(2)}
                  </Text>
                )}
              </Card>
            </Col>
          );
        })}
      </Row>

      {/* 对比表 */}
      <Card title="📊 FIFO 基线 vs 优化方案" size="small">
        <Table
          dataSource={metrics.comparison}
          columns={comparisonCols}
          rowKey="方案"
          pagination={false}
          size="small"
          bordered
        />
      </Card>
    </div>
  );
};

export default ResultOverview;
