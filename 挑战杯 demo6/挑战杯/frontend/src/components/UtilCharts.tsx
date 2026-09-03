import React, { useMemo } from 'react';
import { Card, Table, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { UtilItem } from '../types';
import { CHART } from '../theme';

const { Title } = Typography;

interface Props {
  data: UtilItem[];
}

const UTIL_ORDER = [
  '天车',
  'N2',
  'N2分拣',
  'N2大件打磨',
  'N2小件打磨',
  'N5',
  'N5分拣',
  'N5大件打磨',
  'N5小件打磨',
  'AGV',
  '自动坡口',
  '人工坡口',
];

function normalizeUtilName(name: string): string {
  if (name === '天车' || name === 'N2' || name === 'N5' || name === 'N8') return name;
  if (name.startsWith('自动分拣N2')) return 'N2分拣';
  if (name.startsWith('自动分拣N5')) return 'N5分拣';
  if (name.startsWith('人工打磨N2')) return 'N2大件打磨';
  if (name.startsWith('人工打磨N5')) return 'N5大件打磨';
  if (name.startsWith('人工打磨1')) return 'N2大件打磨';
  if (name.startsWith('人工打磨2')) return 'N5大件打磨';
  if (name.startsWith('自动打磨N2')) return 'N2小件打磨';
  if (name.startsWith('自动打磨N5')) return 'N5小件打磨';
  if (name.startsWith('自动打磨1')) return 'N2小件打磨';
  if (name.startsWith('自动打磨2')) return 'N5小件打磨';
  if (name.startsWith('AGV')) return 'AGV';
  if (name.startsWith('自动坡口')) return '自动坡口';
  if (name.startsWith('人工坡口')) return '人工坡口';
  return name;
}

const UtilCharts: React.FC<Props> = ({ data }) => {
  // P2-3 修复：空数据/undefined 守卫
  const safeData = Array.isArray(data) ? data : [];
  const orderedData = useMemo(() => {
    const grouped = new Map<string, UtilItem>();
    safeData.forEach((d) => {
      const name = normalizeUtilName(d.name);
      const cur = grouped.get(name);
      if (cur) {
        grouped.set(name, {
          name,
          totalHours: cur.totalHours + d.totalHours,
          utilization: (cur.utilization + d.utilization) / 2,
        });
      } else {
        grouped.set(name, { ...d, name });
      }
    });
    return [...grouped.values()].sort((a, b) => {
      const ia = UTIL_ORDER.indexOf(a.name);
      const ib = UTIL_ORDER.indexOf(b.name);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
  }, [safeData]);

  const option = useMemo(() => {
    const names = orderedData.map((d) => d.name);
    const values = orderedData.map((d) => d.utilization);

    return {
      tooltip: {
        trigger: 'axis',
        formatter: (params: any) => {
          const idx = params[0]?.dataIndex;
          if (idx == null) return '';
          const d = orderedData[idx];
          return `<b>${d.name}</b><br/>利用率: ${d.utilization}%<br/>总工时: ${d.totalHours} h`;
        },
      },
      grid: { left: 140, right: 60, top: 20, bottom: 100 },
      xAxis: {
        type: 'category',
        data: names,
        axisLabel: { rotate: 30, fontSize: 11 },
      },
      yAxis: {
        type: 'value',
        name: '利用率 (%)',
        max: 100,
      },
      series: [
        {
          type: 'bar',
          data: values.map((v) => ({
            value: v,
            itemStyle: {
              color: CHART.utilization,
              borderRadius: [6, 6, 0, 0],
            },
          })),
          label: {
            show: true,
            position: 'top',
            formatter: '{c}%',
            fontSize: 10,
          },
        },
      ],
    };
  }, [orderedData]);

  const columns = [
    { title: '资源', dataIndex: 'name', key: 'name' },
    {
      title: '总工时 (h)',
      dataIndex: 'totalHours',
      key: 'totalHours',
      sorter: (a: UtilItem, b: UtilItem) => a.totalHours - b.totalHours,
    },
    {
      title: '利用率',
      dataIndex: 'utilization',
      key: 'utilization',
      sorter: (a: UtilItem, b: UtilItem) => a.utilization - b.utilization,
      render: (v: number) => `${v}%`,
    },
  ];

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto' }}>
      <Title level={4}>🔧 设备资源利用率</Title>
      <Card style={{ marginBottom: 16 }}>
        <ReactECharts option={option} style={{ height: 400 }} />
      </Card>
      <Card title="明细表" size="small">
        <Table
          dataSource={orderedData}
          columns={columns}
          rowKey="name"
          pagination={false}
          size="small"
          bordered
        />
      </Card>
    </div>
  );
};

export default UtilCharts;
