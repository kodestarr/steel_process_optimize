import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Card, InputNumber, Select, Space, Tag, Tooltip, Typography } from 'antd';
import { LockOutlined, UnlockOutlined } from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';
import type { GanttItem, StageItem } from '../types';

const { Title, Text } = Typography;

interface Props {
  data: GanttItem[];
  makespanHours: number;
  stages?: StageItem[];  // all process stages for full-resource view
}

// Color palette for different resource types
const RESOURCE_COLORS: Record<string, string> = {
  N2: '#2E74B5', N5: '#70AD47', N8: '#8B5CF6',
  自动打磨1: '#F59E0B', 自动打磨2: '#F59E0B',
  '自动打磨N2-1': '#F59E0B', '自动打磨N2-2': '#F59E0B',
  '自动打磨N5-1': '#F59E0B', '自动打磨N5-2': '#F59E0B',
  人工打磨1: '#EF4444', 人工打磨2: '#EF4444',
  自动坡口1: '#06B6D4',
  人工坡口1: '#EC4899',
  AGV1: '#F59E0B', AGV2: '#F59E0B',
  自动分拣1: '#10B981', 自动分拣2: '#10B981',
  '自动分拣N2-1': '#10B981', '自动分拣N2-2': '#10B981',
  '自动分拣N5-1': '#10B981', '自动分拣N5-2': '#10B981',
};
const DEFAULT_COLORS = ['#2E74B5', '#70AD47', '#8B5CF6', '#F59E0B', '#EF4444', '#06B6D4', '#EC4899', '#6366F1', '#10B981', '#F97316'];

function getColor(name: string, idx: number): string {
  return RESOURCE_COLORS[name] ?? DEFAULT_COLORS[idx % DEFAULT_COLORS.length];
}

function formatTime(h: number): string {
  if (!Number.isFinite(h)) return '';
  const totalSeconds = Math.max(0, Math.round(h * 3600));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}h ${minutes}min ${seconds}s`;
  if (minutes > 0) return `${minutes}min ${seconds}s`;
  return `${seconds}s`;
}

function formatDuration(h: number): string {
  if (!Number.isFinite(h)) return '';
  const totalSeconds = Math.max(0, Math.round(h * 3600));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}h ${minutes}min ${seconds}s`;
  if (minutes > 0) return `${minutes}min ${seconds}s`;
  return `${seconds}s`;
}

function estimateTextWidth(text: string, fontSize: number): number {
  const chinese = (text.match(/[\u4e00-\u9fff]/g) || []).length;
  const latin = text.length - chinese;
  return chinese * fontSize + latin * fontSize * 0.62 + 16;
}

type ViewMode = 'cutting' | 'all';

interface CuttingRow {
  resource: string;
  start: number;
  end: number;
  name: string;
  displayName?: string;
  section: string;
  duration: number;
  table?: number;
  instance?: number;
  plate?: string;
  highlightKeys?: string[];
  colorType?: 'head' | 'free' | 'wait' | 'normal';
}

const RESOURCE_ORDER = [
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

function groupResourceName(res: string): { group: string; instance: number } {
  if (res === '天车') return { group: '天车', instance: 0 };
  if (res === 'N2' || res === 'N8') return { group: 'N2', instance: 0 };
  if (res === 'N5') return { group: 'N5', instance: 0 };
  if (res.startsWith('自动分拣N2')) {
    return { group: 'N2分拣', instance: 0 };
  }
  if (res.startsWith('自动分拣N5')) {
    return { group: 'N5分拣', instance: 0 };
  }
  if (res.startsWith('人工打磨N2')) {
    return { group: 'N2大件打磨', instance: 0 };
  }
  if (res.startsWith('人工打磨N5')) {
    return { group: 'N5大件打磨', instance: 0 };
  }
  if (res.startsWith('人工打磨')) {
    const idx = (parseInt(res.replace('人工打磨', '') || '1', 10) || 1) - 1;
    return { group: idx === 0 ? 'N2大件打磨' : 'N5大件打磨', instance: 0 };
  }
  if (res.startsWith('自动打磨N2')) {
    return { group: 'N2小件打磨', instance: 0 };
  }
  if (res.startsWith('自动打磨N5')) {
    return { group: 'N5小件打磨', instance: 0 };
  }
  if (res.startsWith('自动打磨')) {
    const idx = (parseInt(res.replace('自动打磨', '') || '1', 10) || 1) - 1;
    return { group: idx === 0 ? 'N2小件打磨' : 'N5小件打磨', instance: 0 };
  }
  if (res.startsWith('AGV')) {
    return { group: 'AGV', instance: Math.max(0, (parseInt(res.replace('AGV', '') || '1', 10) || 1) - 1) };
  }
  if (res.startsWith('自动坡口')) {
    return { group: '自动坡口', instance: Math.max(0, (parseInt(res.replace('自动坡口', '') || '1', 10) || 1) - 1) };
  }
  if (res.startsWith('人工坡口')) {
    return { group: '人工坡口', instance: Math.max(0, (parseInt(res.replace('人工坡口', '') || '1', 10) || 1) - 1) };
  }
  return { group: res, instance: 0 };
}

function normalizeBinLabel(name: string): string {
  return name
    .replace(/_坡口$/, '')
    .replace(/_合盘$/, '')
    .replace(/_坡口$/, '');
}

const LARGE_STAGE_WORDS = ['自由边打磨', '人工坡口', '天车吊运', '天车空驶'];

function getStageDisplayName(name: string, plate?: string, section?: string): string {
  if (plate && section && LARGE_STAGE_WORDS.some((w) => section.includes(w))) {
    return `${plate}切割后的大件`;
  }
  return name;
}

function getMaterialKeys(it: { name: string; section: string; plate?: string; highlightKeys?: string[] }): string[] {
  const keys: string[] = [];
  if (!it.name) return keys;
  if (it.highlightKeys && it.highlightKeys.length > 0) return it.highlightKeys;
  if (it.name.startsWith('料框_')) {
    keys.push(`bin:${normalizeBinLabel(it.name)}`);
  }
  const largeKeywords = ['自由边打磨', '人工坡口', '天车吊运'];
  const smallKeywords = ['分拣', '自动打磨', '桁架至坡口工作站', '桁架码垛'];
  const cuttingKeywords = ['等待切割', '切割头占用', '工位占用'];
  if (
    largeKeywords.some((k) => it.section.includes(k)) ||
    smallKeywords.some((k) => it.section.includes(k))
  ) {
    keys.push(`part:${it.name}`);
    if (it.plate) keys.push(`plate:${it.plate}`);
  } else if (cuttingKeywords.some((k) => it.section.includes(k))) {
    keys.push(`plate:${it.name}`);
  } else if (it.section.includes('天车') && !it.plate) {
    keys.push(`plate:${it.name}`);
  }
  return keys;
}

function splitCuttingData(data: GanttItem[]): CuttingRow[] {
  const rows: CuttingRow[] = [];
  data.forEach((d) => {
    const isDual = /^N[25]$/.test(d.machine);
    if (!isDual) {
      rows.push({
        resource: d.machine,
        start: d.start,
        end: d.end,
        name: d.name,
        section: d.section,
        duration: d.duration,
        table: d.table ?? 0,
        plate: d.name,
        colorType: 'normal',
      });
      return;
    }
    const tableId = d.table ?? 0;
    const resource = d.machine;
    const tableEnd = d.tableEnd ?? d.end;
    const tableStart = d.tableStart ?? d.start;
    const waitEnd = d.waitEnd ?? d.start;
    if (waitEnd > tableStart + 1e-9) {
      rows.push({
        resource,
        start: tableStart,
        end: waitEnd,
        name: d.name,
        section: '等待切割',
        duration: waitEnd - tableStart,
        table: tableId,
        plate: d.name,
        colorType: 'wait',
      });
    }
    rows.push({
      resource,
      start: d.start,
      end: d.end,
      name: d.name,
      section: '切割头占用',
      duration: d.end - d.start,
      table: tableId,
      plate: d.name,
      colorType: 'head',
    });
    if (tableEnd > d.end + 1e-9) {
      rows.push({
        resource,
        start: d.end,
        end: tableEnd,
        name: d.name,
        section: '工位占用',
        duration: tableEnd - d.end,
        table: tableId,
        plate: d.name,
        colorType: 'free',
      });
    }
  });
  return rows;
}

const GanttChart: React.FC<Props> = ({ data, makespanHours, stages }) => {
  const [viewMode, setViewMode] = useState<ViewMode>('all');
  const [visibleGroups, setVisibleGroups] = useState<string[]>([]);
  const [hoverTime, setHoverTime] = useState<number | null>(null);
  const [zoom, setZoom] = useState<{ start: number; end: number }>({ start: 0, end: 100 });
  const [locked, setLocked] = useState(false);
  const [highlightGroup, setHighlightGroup] = useState<string | null>(null);
  const [highlightPulse, setHighlightPulse] = useState(false);
  const [startH, setStartH] = useState(0);
  const [startM, setStartM] = useState(0);
  const [endH, setEndH] = useState(() => Math.max(1, Math.ceil(makespanHours)));
  const [endM, setEndM] = useState(0);
  const chartRef = useRef<any>(null);

  useEffect(() => {
    setZoom({ start: 0, end: 100 });
    setLocked(false);
    setStartH(0);
    setStartM(0);
    setEndH(Math.max(1, Math.ceil(makespanHours)));
    setEndM(0);
  }, [makespanHours]);

  useEffect(() => {
    if (!highlightGroup) return;
    const timer = setInterval(() => setHighlightPulse((p) => !p), 600);
    return () => clearInterval(timer);
  }, [highlightGroup]);

  // Build resource list and data based on view mode
  const { resources, seriesData } = useMemo(() => {
    if (viewMode === 'cutting' || !stages || stages.length === 0) {
      const items = splitCuttingData(data).map((it) => {
        const g = groupResourceName(it.resource);
        return { ...it, resource: g.group, instance: it.table ?? g.instance };
      });
      const machines = [...new Set(items.map((it) => it.resource))]
        .sort((a, b) => (RESOURCE_ORDER.indexOf(a) - RESOURCE_ORDER.indexOf(b)));
      const shownMachines = visibleGroups.length > 0
        ? machines.filter((m) => visibleGroups.includes(m))
        : machines;
      const resIndex: Record<string, number> = {};
      shownMachines.forEach((m, i) => { resIndex[m] = i; });
      const series = shownMachines.map((m, mi) => {
        const inst = items.filter(it => it.resource === m).map(it => it.instance ?? 0);
        return {
          name: m,
          resIdx: resIndex[m],
          items: items.filter(it => it.resource === m),
          instanceCount: m === 'N2' || m === 'N5' ? 2 : Math.max(1, inst.length ? Math.max(...inst) + 1 : 1),
          color: getColor(m.replace(/-[AB]$/, ''), mi),
        };
      });
      return { resources: shownMachines, seriesData: series };
    }

    // Full-resource view: combine cutting gantt + stages
    let allResourcesSet = new Set<string>();
    const allItems: CuttingRow[] = [];

    // Cutting data
    splitCuttingData(data).forEach((it) => {
      allResourcesSet.add(it.resource);
      allItems.push(it);
    });

    // Stage data (grinding, beveling, AGV, sorting, etc.)
    const skipStages = new Set(['切割']);
    stages.forEach(s => {
      if (!skipStages.has(s.stage) && s.resource && s.start < s.end) {
        allResourcesSet.add(s.resource);
        allItems.push({
          resource: s.resource,
          start: s.start,
          end: s.end,
          name: s.part,
          displayName: getStageDisplayName(s.part, s.plate, s.stage),
          section: s.stage,
          duration: s.end - s.start,
          plate: s.plate,
        });
      }
    });

    // 天车吊运与其对应的天车空驶共享同一组联动 key，保证空载一起闪烁
    const craneLoaded = allItems.filter((it) => it.section.includes('天车吊运'));
    const craneEmpty = allItems.filter((it) => it.section.startsWith('天车空驶'));
    for (const loaded of craneLoaded) {
      let paired: CuttingRow | null = null;
      let pairedEnd = -Infinity;
      for (const empty of craneEmpty) {
        if (empty.name !== loaded.name) continue;
        if ((empty.plate || '') !== (loaded.plate || '')) continue;
        if (empty.end <= loaded.start + 1e-6 && empty.end >= pairedEnd) {
          paired = empty;
          pairedEnd = empty.end;
        }
      }
      if (paired) {
        paired.highlightKeys = getMaterialKeys(loaded);
      }
    }
    allItems.forEach((it) => {
      if (!it.highlightKeys) it.highlightKeys = getMaterialKeys(it);
    });

    const groupedItems = allItems.map((it) => {
      const g = groupResourceName(it.resource);
      return { ...it, resource: g.group, instance: it.table ?? g.instance };
    });
    allResourcesSet = new Set(groupedItems.map((it) => it.resource));
    const resources = [...allResourcesSet]
      .sort((a, b) => (RESOURCE_ORDER.indexOf(a) - RESOURCE_ORDER.indexOf(b)));
    const shownResources = visibleGroups.length > 0
      ? resources.filter((r) => visibleGroups.includes(r))
      : resources;

    const resIndex: Record<string, number> = {};
    shownResources.forEach((r, i) => { resIndex[r] = i; });

    const series = shownResources.map((res, ri) => {
      const inst = groupedItems.filter(it => it.resource === res).map(it => it.instance ?? 0);
      return {
        name: res,
        resIdx: resIndex[res],
        items: groupedItems.filter(it => it.resource === res),
        instanceCount: res === 'N2' || res === 'N5' ? 2 : Math.max(1, inst.length ? Math.max(...inst) + 1 : 1),
        color: getColor(res.replace(/-[AB]$/, ''), ri),
      };
    });

    return { resources: shownResources, seriesData: series };
  }, [data, stages, viewMode, visibleGroups]);

  const option = useMemo(() => {
    // P2-1: 大数据集渲染优化 — 超过500条时关闭动画、降低重绘频率
    const totalItems = seriesData.reduce((sum, s) => sum + s.items.length, 0);
    const isLargeDataset = totalItems > 500;

    const customSeries = seriesData.map((s) => ({
      name: s.name,
      type: 'custom',
      renderItem: (_params: any, api: any) => {
        const categoryIndex = api.value(0);
        const axisMax = Math.ceil(makespanHours);
        const viewMin = (axisMax * zoom.start) / 100;
        const viewMax = (axisMax * zoom.end) / 100;
        const rawStart = api.value(1);
        const rawEnd = api.value(2);
        const clippedStart = Math.max(rawStart, viewMin);
        const clippedEnd = Math.min(rawEnd, viewMax);
        if (clippedEnd < clippedStart) return null;
        const start = api.coord([clippedStart, categoryIndex]);
        const end = api.coord([clippedEnd, categoryIndex]);
        const rowHeight = api.size([0, 1])[1];
        const isDual = /^N[25]$/.test(s.name);
        const stackIndex = isDual ? (api.value(7) ?? 0) : (api.value(8) ?? 0);
        const stackCount = isDual ? 2 : Math.max(1, s.instanceCount || 1);
        let height: number;
        let y: number;
        if (stackCount <= 1) {
          height = rowHeight * 0.55;
          y = start[1] - height / 2;
        } else {
          const slot = rowHeight / stackCount;
          height = slot * 0.75;
          y = start[1] - rowHeight / 2 + stackIndex * slot + (slot - height) / 2;
        }
        const colorType = api.value(6);
        const fill = colorType === 'free' ? '#D1D5DB' : colorType === 'wait' ? '#E5E7EB' : s.color;
        const keys = getMaterialKeys({
          name: api.value(3),
          section: api.value(5),
          plate: api.value(9),
          highlightKeys: api.value(10),
        });
        const highlighted = keys.includes(highlightGroup ?? '');
        const opacity = highlighted ? (highlightPulse ? 1 : 0.15) : 1;
        return {
          type: 'rect',
          shape: {
            x: start[0],
            y,
            // 不设大最小宽度：短工序按真实时间比例显示，避免条形重叠造成“铺满”假象
            width: Math.max(end[0] - start[0], 0.1),
            height,
          },
          style: {
            fill,
            opacity,
            radius: 2,
            stroke: highlighted ? '#DC2626' : 'transparent',
            lineWidth: highlighted ? 1 : 0,
          },
          styleEmphasis: { fill, opacity: highlighted ? 0.95 : 0.85 },
          // P2-1: 大数据集关闭阴影以提升渲染速度
          shadowBlur: isLargeDataset ? 0 : undefined,
        };
      },
      encode: { x: [1, 2], y: 0 },
      data: s.items.map(it => [s.resIdx, it.start, it.end, it.name, it.duration, it.section, it.colorType, it.table, it.instance, it.plate, it.highlightKeys]),
      itemStyle: { color: s.color },
      // P2-1: 大数据集关闭动画
      animation: !isLargeDataset,
      animationDuration: isLargeDataset ? 0 : 300,
    }));

    const graphic: any[] = [];
    const chart = chartRef.current?.getEchartsInstance?.();
    if (hoverTime !== null && hoverTime >= 0 && chart) {
      const gridComponent = chart.getModel()?.getComponent?.('grid');
      const gridRect = gridComponent?.coordinateSystem?.getRect?.();
      const px = chart.convertToPixel?.({ xAxisIndex: 0 }, hoverTime);
      if (gridRect && Number.isFinite(px)) {
        graphic.push({
          type: 'line',
          shape: { x1: px, y1: gridRect.y, x2: px, y2: gridRect.y + gridRect.height },
          style: { stroke: '#DC2626', lineWidth: 1, lineDash: [4, 4] },
          z: 100,
        });
        graphic.push({
          type: 'text',
          left: px + 8 + estimateTextWidth(formatTime(hoverTime), 12) > gridRect.x + gridRect.width
            ? px - estimateTextWidth(formatTime(hoverTime), 12) - 8
            : px + 8,
          top: gridRect.y + gridRect.height + 8,
          style: {
            text: formatTime(hoverTime),
            fill: '#111827',
            backgroundColor: '#ffffff',
            padding: [3, 8],
            borderRadius: 3,
            fontSize: 12,
          },
          z: 100,
        });
        seriesData.forEach((s, idx) => {
          const y = chart.convertToPixel?.({ yAxisIndex: 0 }, idx);
          if (!Number.isFinite(y)) return;
          const isDual = /^N[25]$/.test(s.name);
          const instanceCount = isDual ? 2 : Math.max(1, s.instanceCount || 1);
          const rowHeight = gridRect.height / seriesData.length;
          for (let i = 0; i < instanceCount; i += 1) {
            const active = s.items.find(
              (it) =>
                hoverTime >= it.start - 1e-9 &&
                hoverTime <= it.end + 1e-9 &&
                (isDual ? (it.table ?? 0) === i : (it.instance ?? 0) === i)
            );
            const labelText = active
              ? `${active.displayName ?? active.name}${active.section ? ` (${active.section})` : ''} · 工时 ${formatDuration(active.duration)}`
              : '空闲';
            const labelWidth = estimateTextWidth(labelText, 10);
            const labelLeft = px + 10 + labelWidth > gridRect.x + gridRect.width
              ? px - labelWidth - 10
              : px + 10;
            let labelY: number;
            if (instanceCount <= 1) {
              labelY = y - 8;
            } else {
              const slot = rowHeight / instanceCount;
              labelY = y - rowHeight / 2 + i * slot + slot / 2 - 8;
            }
            graphic.push({
              type: 'text',
              left: labelLeft,
              top: labelY,
              style: {
                text: labelText,
                fill: active ? '#ffffff' : '#58595a',
                backgroundColor: active ? s.color : 'rgba(107,114,128,0.12)',
                padding: [3, 7],
                borderRadius: 3,
                fontSize: 10,
              },
              z: 100,
            });
          }
        });
      }
    }

    return {
      // P2-1: 大数据集使用更轻量的 tooltip
      tooltip: { show: false },
      grid: { left: 120, right: 50, top: 20, bottom: 80 },
      dataZoom: [
        {
          type: 'slider',
          xAxisIndex: 0,
          start: zoom.start,
          end: zoom.end,
          filterMode: 'none',
          disabled: locked,
          height: 25,
          bottom: 40,
          handleSize: '80%',
          textStyle: { fontSize: 11 },
          // P2-1: 大数据集减少重绘
          throttle: isLargeDataset ? 80 : undefined,
        },
        {
          type: 'inside',
          xAxisIndex: 0,
          start: 0,
          end: 100,
          filterMode: 'none',
          disabled: locked,
          zoomOnMouseWheel: true,
          moveOnMouseMove: true,
          throttle: isLargeDataset ? 50 : undefined,
        },
      ],
      xAxis: {
        type: 'value',
        name: '时间 (h)',
        max: Math.ceil(makespanHours),
        axisLabel: { formatter: '{value}h' },
      },
      yAxis: {
        type: 'category',
        data: resources,
        axisLabel: {
          fontSize: 12,
          fontWeight: 'bold',
          formatter: (value: string) => value.replace(/-[AB]$/, ''),
        },
        inverse: true,
        // P2-1: 大数据集减少不必要触发
        triggerEvent: !isLargeDataset,
      },
      series: customSeries,
      graphic,
    };
  }, [seriesData, makespanHours, resources, hoverTime, zoom, highlightGroup, highlightPulse]);

  const handleWrapperMouseMove = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const chart = chartRef.current?.getEchartsInstance?.();
    if (!chart) return;
    const rect = chart.getDom().getBoundingClientRect();
    const point = [e.clientX - rect.left, e.clientY - rect.top];
    const coord = chart.convertFromPixel?.({ xAxisIndex: 0, yAxisIndex: 0 }, point);
    if (!Array.isArray(coord) || !Number.isFinite(coord[0])) return;
    const hover = coord[0];
    setHoverTime(hover);

    const gridComponent = chart.getModel()?.getComponent?.('grid');
    const gridRect = gridComponent?.coordinateSystem?.getRect?.();
    if (!gridRect) return;
    const rowHeight = gridRect.height / Math.max(1, seriesData.length);
    const categoryIndex = Math.round(coord[1] ?? 0);
    const yCenter = chart.convertToPixel?.({ yAxisIndex: 0 }, categoryIndex);
    if (!Number.isFinite(yCenter)) return;

    let hit: CuttingRow | null = null;
    for (const s of seriesData) {
      if (s.resIdx !== categoryIndex) continue;
      const isDual = /^N[25]$/.test(s.name);
      const stackCount = isDual ? 2 : Math.max(1, s.instanceCount || 1);
      for (const it of s.items) {
        if (hover < it.start - 1e-9 || hover > it.end + 1e-9) continue;
        const stackIndex = isDual ? (it.table ?? 0) : (it.instance ?? 0);
        let top: number;
        let barH: number;
        if (stackCount <= 1) {
          barH = rowHeight * 0.55;
          top = yCenter - barH / 2;
        } else {
          const slot = rowHeight / stackCount;
          barH = slot * 0.75;
          top = yCenter - rowHeight / 2 + stackIndex * slot + (slot - barH) / 2;
        }
        if (point[1] >= top && point[1] <= top + barH) {
          hit = it;
          break;
        }
      }
      if (hit) break;
    }
    const keys = hit ? getMaterialKeys(hit) : [];
    setHighlightGroup(keys.length > 0 ? keys[0] : null);
  }, [seriesData]);

  const handleMouseOut = useCallback(() => {
    setHoverTime(null);
  }, []);

  const handleDataZoom = useCallback((params: any) => {
    if (locked) return;
    const target = params?.batch?.[0] ?? params;
    const axisMax = Math.ceil(makespanHours);
    let start = Number(target?.start);
    let end = Number(target?.end);
    if (!Number.isFinite(start) && target?.startValue != null) {
      start = (Number(target.startValue) / axisMax) * 100;
    }
    if (!Number.isFinite(end) && target?.endValue != null) {
      end = (Number(target.endValue) / axisMax) * 100;
    }

    if (Number.isFinite(start) && Number.isFinite(end)) {
      setZoom({
        start: Math.max(0, Math.min(100, start)),
        end: Math.max(0, Math.min(100, end)),
      });
    }
    // 缩放后强制用当前缩放区间重算悬停线
    setHoverTime((h) => h);
  }, [makespanHours, locked]);

  const handleJump = useCallback(() => {
    const totalMinutes = Math.max(1, makespanHours * 60);
    const startMinutes = (Number(startH) || 0) * 60 + (Number(startM) || 0);
    const endMinutes = (Number(endH) || 0) * 60 + (Number(endM) || 0);
    if (endMinutes <= startMinutes) return;
    setZoom({
      start: Math.max(0, Math.min(100, (startMinutes / totalMinutes) * 100)),
      end: Math.max(0, Math.min(100, (endMinutes / totalMinutes) * 100)),
    });
  }, [makespanHours, startH, startM, endH, endM]);

  const handleLockToggle = useCallback(() => {
    setLocked((v) => !v);
  }, []);

  const handleBarMouseOut = useCallback(() => {
    setHighlightGroup(null);
    setHighlightPulse(false);
  }, []);

  const machineNames = viewMode === 'cutting'
    ? [...new Set(data.map(d => d.machine))].join(' / ')
    : `${resources.length} resources`;

  const chartHeight = useMemo(() => {
    const total = seriesData.reduce(
      (h, s) => h + (40 + Math.max(0, (s.instanceCount - 1)) * 24),
      0
    );
    return Math.max(300, total + 80);
  }, [seriesData]);

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <Title level={4} style={{ margin: 0 }}>
          🗓️ {viewMode === 'cutting' ? '切割甘特图' : '全资源排产甘特图'}（{machineNames}）
        </Title>
        <Space>
          <Text type="secondary">视图:</Text>
          <Select
            size="small"
            value={viewMode}
            onChange={(v: ViewMode) => setViewMode(v)}
            style={{ width: 160 }}
            options={[
              { value: 'cutting', label: '🔪 仅切割机' },
              { value: 'all', label: '🏭 全资源视图' },
            ]}
          />
        </Space>
      </div>
      <Space wrap style={{ marginBottom: 8 }}>
        <Select
          mode="multiple"
          size="small"
          placeholder="选择要展示的资源"
          value={visibleGroups}
          onChange={setVisibleGroups}
          style={{ minWidth: 300 }}
          options={RESOURCE_ORDER.map((r) => ({ value: r, label: r }))}
        />
        <Button size="small" onClick={() => setVisibleGroups([])}>全部</Button>
        <Button
          size="small"
          onClick={() => setVisibleGroups(['N2', 'N5', 'N2分拣', 'N2小件打磨', 'N5分拣', 'N5小件打磨', '自动坡口', 'AGV'])}
        >
          只看小件
        </Button>
        <Button
          size="small"
          onClick={() => setVisibleGroups(['天车', 'N2', 'N5', 'N2大件打磨', 'N5大件打磨', '人工坡口', 'AGV'])}
        >
          只看大件
        </Button>
        <Button
          size="small"
          onClick={() => setVisibleGroups(['N2', 'N2分拣', 'N2大件打磨', 'N2小件打磨', '自动坡口', 'AGV'])}
        >
          只看N2
        </Button>
      </Space>
      <Space wrap style={{ marginBottom: 8 }}>
        <Text type="secondary">时间范围:</Text>
        <InputNumber size="small" min={0} max={999} value={startH} onChange={(v) => setStartH(Number(v) || 0)} />
        <Text type="secondary">h</Text>
        <InputNumber size="small" min={0} max={59} value={startM} onChange={(v) => setStartM(Number(v) || 0)} />
        <Text type="secondary">min</Text>
        <Text type="secondary">到</Text>
        <InputNumber size="small" min={0} max={999} value={endH} onChange={(v) => setEndH(Number(v) || 0)} />
        <Text type="secondary">h</Text>
        <InputNumber size="small" min={0} max={59} value={endM} onChange={(v) => setEndM(Number(v) || 0)} />
        <Text type="secondary">min</Text>
        <Button size="small" type="primary" onClick={handleJump}>跳转</Button>
        <Tooltip title={locked ? '时间轴已锁定，不会随滚轮/拖动变化' : '点击后锁定当前时间轴'}>
          <Button
            size="small"
            icon={locked ? <LockOutlined /> : <UnlockOutlined />}
            onClick={handleLockToggle}
          >
            {locked ? '已锁定' : '锁定'}
          </Button>
        </Tooltip>
      </Space>
      <Card>
        {viewMode === 'all' && (
          <div style={{ marginBottom: 12, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <Tag color="blue">切割机</Tag>
            <Tag color="green">分拣桁架</Tag>
            <Tag color="orange">自动打磨</Tag>
            <Tag color="red">人工打磨</Tag>
            <Tag color="cyan">自动坡口</Tag>
            <Tag color="magenta">人工坡口</Tag>
            <Tag color="purple">AGV转运</Tag>
          </div>
        )}
        <div
          onMouseMove={handleWrapperMouseMove}
          onMouseEnter={handleWrapperMouseMove}
          onMouseLeave={() => {
            handleMouseOut();
            handleBarMouseOut();
          }}
          style={{ width: '100%' }}
        >
          <ReactECharts
            ref={chartRef}
            option={option}
            notMerge={true}
            onEvents={{
              datazoom: handleDataZoom,
            }}
            style={{ height: chartHeight }}
          />
        </div>
      </Card>
    </div>
  );
};

export default GanttChart;
