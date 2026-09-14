import React, { useMemo } from 'react';
import { Card, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { KitSpanItem } from '../types';
import { CHART } from '../theme';

const { Title } = Typography;

interface Props {
  data: KitSpanItem[];
}

const KitAnalysis: React.FC<Props> = ({ data }) => {
  const option = useMemo(() => {
    const sorted = [...data].sort((a, b) => b.span - a.span);
    const labels = sorted.map((d) => d.label);

    // 等待区间：firstArrival → completion，灰色底 + 跨度高亮
    const firstArrivals = sorted.map((d) => d.firstArrival);
    const spans = sorted.map((d) => d.span);

    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params: any) => {
          const idx = params[0]?.dataIndex;
          if (idx == null) return '';
          const d = sorted[idx];
          return `<b>${d.label}</b> — ${d.partCount} 个零件<br/>⏱ 首件到达: <b>${d.firstArrival.toFixed(2)} h</b><br/>🏁 齐套完成: <b>${d.completion.toFixed(2)} h</b><br/>📏 等待跨度: <b>${d.span.toFixed(2)} h</b>`;
        },
      },
      grid: { left: 130, right: 60, top: 20, bottom: 30 },
      xAxis: {
        type: 'value',
        name: '时间 (h)',
        axisLabel: { formatter: '{value} h' },
      },
      yAxis: {
        type: 'category',
        data: labels,
        inverse: true,
        axisLabel: { fontSize: 11 },
      },
      series: [
        // 底层：首件到达 → 齐套完成 的完整区间（灰色）
        {
          name: '等待区间',
          type: 'bar',
          stack: 'range',
          data: firstArrivals,
          itemStyle: {
            color: 'transparent',
            borderColor: 'transparent',
          },
          barWidth: '50%',
          emphasis: { itemStyle: { color: 'transparent' } },
        },
        // 上层：跨度条（橙色）
        {
          name: '齐套跨度',
          type: 'bar',
          stack: 'range',
          data: spans,
          itemStyle: {
            color: CHART.kitSpan,
            borderRadius: [6, 6, 6, 6],
          },
          barWidth: '50%',
          label: {
            show: true,
            position: 'right',
            formatter: (p: any) => `${p.value.toFixed(1)}h`,
            fontSize: 10,
            color: CHART.kitSpan,
          },
        },
      ],
    };
  }, [data]);

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto' }}>
      <Title level={4}>📦 各齐套组时间区间（首件到达 → 齐套完成）</Title>
      <Card>
        <ReactECharts
          option={option}
          style={{ height: Math.max(420, data.length * 32 + 80) }}
        />
      </Card>
    </div>
  );
};

export default KitAnalysis;
