import React, { useEffect, useState, useMemo, useCallback } from 'react';
import {
  Alert,
  Button,
  List,
  Typography,
  Popconfirm,
  Modal,
  Input,
  Space,
  Tag,
  Empty,
  Spin,
  message,
  Select,
  Tooltip,
} from 'antd';
import {
  HistoryOutlined,
  DeleteOutlined,
  EditOutlined,
  ReloadOutlined,
  PushpinOutlined,
  PushpinFilled,
  SortAscendingOutlined,
  SortDescendingOutlined,
} from '@ant-design/icons';
import type { HistoryItem } from '../types';
import { fetchHistory, deleteRun, renameRun, fetchRunDetail } from '../api';
import type { RunResponse } from '../types';
import { COLORS } from '../theme';

const { Text } = Typography;

// 排序字段
type SortField = 'time' | 'makespan' | 'kitSpan' | 'loadDiff' | 'n2Util' | 'n5Util' | 'score';

const SORT_OPTIONS: { value: SortField; label: string }[] = [
  { value: 'time', label: '时间' },
  { value: 'makespan', label: '总完工时间' },
  { value: 'kitSpan', label: '平均齐套跨度' },
  { value: 'loadDiff', label: '切割负载差' },
  { value: 'n2Util', label: 'N2 利用率' },
  { value: 'n5Util', label: 'N5 利用率' },
  { value: 'score', label: '综合评分' },
];

// 提取指标值
function getMetric(item: HistoryItem, field: SortField): number {
  const m = item.optimized_metrics || {};
  switch (field) {
    case 'makespan': return m['总完工时间(h)'] ?? Infinity;
    case 'kitSpan':  return m['加权平均齐套跨度(h)'] ?? Infinity;
    case 'loadDiff': return m['切割负载差(h)'] ?? Infinity;
    case 'n2Util':   return m['N2利用率'] ?? 0;
    case 'n5Util':   return m['N5利用率'] ?? 0;
    case 'time':     return new Date(item.created_at).getTime();
    default:         return 0;
  }
}

function getBaseMetric(item: HistoryItem, field: SortField): number | null {
  const m = item.base_metrics || {};
  switch (field) {
    case 'makespan': return m['总完工时间(h)'] ?? null;
    case 'kitSpan':  return m['加权平均齐套跨度(h)'] ?? null;
    case 'loadDiff': return m['切割负载差(h)'] ?? null;
    default:         return null;
  }
}

// 综合评分：归一化后加权，分数越高越好
function computeScores(items: HistoryItem[]): Map<string, number> {
  const scores = new Map<string, number>();
  if (items.length === 0) return scores;

  for (const item of items) {
    const mk = getMetric(item, 'makespan');
    const ks = getMetric(item, 'kitSpan');
    const ld = getMetric(item, 'loadDiff');

    // 运行完成时已固化的评分：直接使用，不再现场计算
    if (typeof item.score === 'number' && isFinite(item.score)) {
      scores.set(item.id, Math.round(item.score * 1000) / 1000);
      continue;
    }

    const baseMk = getBaseMetric(item, 'makespan');
    const baseKs = getBaseMetric(item, 'kitSpan');
    const baseLd = getBaseMetric(item, 'loadDiff');

    // 有 FIFO 基线时使用固定口径，避免新记录改变旧记录评分
    if (
      baseMk !== null && isFinite(baseMk) && baseMk > 0 &&
      baseKs !== null && isFinite(baseKs) && baseKs > 0 &&
      baseLd !== null && isFinite(baseLd) && baseLd >= 0
    ) {
      const raw = 0.40 * (mk / baseMk) + 0.40 * (ks / baseKs) + 0.20 * (ld / Math.max(baseLd, 0.5));
      const score = 1 / (1 + raw);
      scores.set(item.id, isFinite(score) ? Math.round(score * 1000) / 1000 : 0);
      continue;
    }

    // 旧记录没有 FIFO 基线时的固定兜底口径，同样不随历史列表变化
    const raw = 0.40 * (mk / 100) + 0.40 * (ks / 60) + 0.20 * (ld / 2);
    const score = 1 / (1 + raw);
    scores.set(item.id, isFinite(score) ? Math.round(score * 1000) / 1000 : 0);
  }
  return scores;
}

interface Props {
  activeRunId: string | null;
  onSelect: (runId: string, data: RunResponse) => void;
  collapsed?: boolean;
}

const HistorySidebar: React.FC<Props> = ({ activeRunId, onSelect, collapsed }) => {
  const [runs, setRuns] = useState<HistoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadingRunId, setLoadingRunId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState<SortField>('time');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');
  const [pinnedIds, setPinnedIds] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetchHistory();
      setRuns(res.runs);
      setError(null);
    } catch (e: any) {
      setError(e?.message ?? '加载历史记录失败，请检查后端服务');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [activeRunId, load]);

  // 综合评分
  const scoreMap = useMemo(() => computeScores(runs), [runs]);

  // 排序
  const sortedRuns = useMemo(() => {
    const sorted = [...runs].sort((a, b) => {
      let va: number, vb: number;
      if (sortBy === 'score') {
        va = scoreMap.get(a.id) ?? 0;
        vb = scoreMap.get(b.id) ?? 0;
      } else {
        va = getMetric(a, sortBy);
        vb = getMetric(b, sortBy);
      }
      const dir = sortOrder === 'asc' ? 1 : -1;
      // time newer = higher value, so desc = newest first
      return (va - vb) * dir;
    });

    // pinned 置顶，保持 pinned 间相对顺序
    const pinned = sorted.filter((r) => pinnedIds.has(r.id));
    const unpinned = sorted.filter((r) => !pinnedIds.has(r.id));
    return [...pinned, ...unpinned];
  }, [runs, sortBy, sortOrder, pinnedIds, scoreMap]);

  const togglePin = useCallback((id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setPinnedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

  const toggleSortOrder = useCallback(() => {
    setSortOrder((o) => (o === 'asc' ? 'desc' : 'asc'));
  }, []);

  const handleSelect = useCallback(async (run: HistoryItem) => {
    setLoadingRunId(run.id);
    try {
      const data = await fetchRunDetail(run.id);
      // 详情页核心指标一律以 history.json 里的快照为准，
      // 与左侧列表完全同源，避免详情数据被缓存或后端串数据。
      onSelect(run.id, {
        ...data,
        metrics: {
          ...data.metrics,
          optimized: run.optimized_metrics ?? data.metrics.optimized,
          fifo: run.base_metrics ?? data.metrics.fifo,
        },
        algorithmName: run.algorithm_name ?? data.algorithmName,
      });
    } catch {
      message.error('加载历史结果失败');
    } finally {
      setLoadingRunId(null);
    }
  }, [onSelect]);

  const handleDelete = useCallback(async (id: string) => {
    try {
      await deleteRun(id);
      setRuns((prev) => prev.filter((r) => r.id !== id));
      setPinnedIds((prev) => { const n = new Set(prev); n.delete(id); return n; });
      message.success('已删除');
    } catch { message.error('删除失败'); }
  }, []);

  const handleRename = useCallback(async () => {
    if (!editingId || !editingName.trim()) return;
    try {
      await renameRun(editingId, editingName.trim());
      setRuns((prev) => prev.map((r) => (r.id === editingId ? { ...r, name: editingName.trim() } : r)));
      setEditingId(null);
      message.success('已重命名');
    } catch { message.error('重命名失败'); }
  }, [editingId, editingName]);

  // 折叠态
  if (collapsed) {
    return (
      <div style={{ padding: '12px 6px', height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
        <Button size="small" icon={<ReloadOutlined />} onClick={load} loading={loading} style={{ marginBottom: 12 }} />
        {sortedRuns.map((item) => (
          <Tooltip key={item.id} title={item.name} placement="right">
            <div
              onClick={() => handleSelect(item)}
              style={{
                width: 36, height: 36, borderRadius: 8, marginBottom: 6,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                cursor: 'pointer', fontSize: 18,
                background: item.id === activeRunId ? COLORS.primaryLight : undefined,
                border: item.id === activeRunId ? '1px solid #BFDBFE' : '1px solid transparent',
                position: 'relative',
              }}
            >
              {pinnedIds.has(item.id) ? '📌' : '📋'}
            </div>
          </Tooltip>
        ))}
        {runs.length === 0 && <div style={{ fontSize: 11, color: COLORS.textTertiary, writingMode: 'vertical-rl' }}>暂无</div>}
      </div>
    );
  }

  return (
    <div style={{ padding: '12px 8px', height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* 标题行 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <Space>
          <HistoryOutlined />
          <Text strong>运行历史</Text>
          <Tag>{sortedRuns.length}</Tag>
        </Space>
        <Button size="small" icon={<ReloadOutlined />} onClick={load} loading={loading} />
      </div>

      {/* Error banner */}
      {error && (
        <Alert type="error" message={error} showIcon closable onClose={() => setError(null)}
          style={{ marginBottom: 8, fontSize: 11 }} />
      )}

      {/* 排序控件 */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 10 }}>
        <Select
          size="small"
          value={sortBy}
          onChange={(v) => setSortBy(v)}
          style={{ flex: 1 }}
          options={SORT_OPTIONS}
        />
        <Tooltip title={sortOrder === 'desc' ? '降序' : '升序'}>
          <Button
            size="small"
            icon={sortOrder === 'desc' ? <SortDescendingOutlined /> : <SortAscendingOutlined />}
            onClick={toggleSortOrder}
          />
        </Tooltip>
      </div>

      {runs.length === 0 && !loading && (
        <Empty description="暂无记录" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      )}

      <Spin spinning={loading}>
        <List
          dataSource={sortedRuns}
          style={{ flex: 1, overflow: 'auto' }}
          renderItem={(item) => {
            const isPinned = pinnedIds.has(item.id);
            const isActive = item.id === activeRunId;
            const isLoading = loadingRunId === item.id;
            const scoreVal = scoreMap.get(item.id);

            return (
              <List.Item
                key={item.id}
                style={{
                  cursor: 'pointer',
                  padding: '6px 10px',
                  borderRadius: 6,
                  marginBottom: 4,
                  background: isPinned ? COLORS.warningLight : isActive ? COLORS.primaryLight : undefined,
                  border: isPinned ? '1px solid #FDE68A' : isActive ? '1px solid #BFDBFE' : '1px solid transparent',
                }}
                onClick={() => handleSelect(item)}
              >
                <div style={{ width: '100%' }}>
                  {/* 第一行：名称 + 操作 */}
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <Text strong style={{ fontSize: 13, maxWidth: 110 }} ellipsis={{ tooltip: item.name }}>
                      {item.name}
                    </Text>
                    <Space size={2}>
                      {/* 图钉 */}
                      <Button
                        type="text" size="small"
                        icon={isPinned ? <PushpinFilled style={{ color: COLORS.warning }} /> : <PushpinOutlined />}
                        onClick={(e) => togglePin(item.id, e)}
                        title={isPinned ? '取消固定' : '固定到顶部'}
                      />
                      <Button type="text" size="small" icon={<EditOutlined />}
                        onClick={(e) => { e.stopPropagation(); setEditingId(item.id); setEditingName(item.name); }}
                      />
                      <Popconfirm title="确认删除？"
                        onConfirm={(e) => { e?.stopPropagation(); handleDelete(item.id); }}
                        onCancel={(e) => e?.stopPropagation()}
                      >
                        <Button type="text" size="small" danger icon={<DeleteOutlined />}
                          onClick={(e) => e.stopPropagation()}
                        />
                      </Popconfirm>
                    </Space>
                  </div>

                  {/* 第二行：时间 + 指标快照 + 评分 */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 2 }}>
                    <Text type="secondary" style={{ fontSize: 10 }}>
                      {item.created_at?.slice(0, 16)?.replace('T', ' ')}
                    </Text>
                    {scoreVal != null && isFinite(scoreVal) && (
                      <Tag color="blue" style={{ fontSize: 10, margin: 0, lineHeight: '16px', padding: '0 4px' }}>
                        {scoreVal.toFixed(3)}
                      </Tag>
                    )}
                    {isActive && <Tag color="blue" style={{ fontSize: 10, margin: 0 }}>当前</Tag>}
                    {isPinned && <Tag color="gold" style={{ fontSize: 10, margin: 0 }}>已固定</Tag>}
                    {isLoading && <Spin size="small" />}
                  </div>

                  {/* 指标快照行 */}
                  {item.optimized_metrics && (
                    <div style={{ marginTop: 2, opacity: 0.55 }}>
                      <Text style={{ fontSize: 9 }}>
                        Cmax={item.optimized_metrics['总完工时间(h)']?.toFixed(1)}h
                        {' '}Tkit={item.optimized_metrics['加权平均齐套跨度(h)']?.toFixed(1)}h
                      </Text>
                    </div>
                  )}
                </div>
              </List.Item>
            );
          }}
        />
      </Spin>

      <Modal title="重命名" open={!!editingId} onOk={handleRename} onCancel={() => setEditingId(null)} okText="确认" cancelText="取消">
        <Input value={editingName} onChange={(e) => setEditingName(e.target.value)} placeholder="输入新名称" onPressEnter={handleRename} />
      </Modal>
    </div>
  );
};

export default HistorySidebar;
