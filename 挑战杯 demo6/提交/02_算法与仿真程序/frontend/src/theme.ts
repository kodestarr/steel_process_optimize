/**
 * Billow-inspired design tokens.
 * All visual constants live here so components reference one source of truth.
 */

// ── Palette ────────────────────────────────────────────
export const COLORS = {
  // Surfaces
  pageBg: '#F7F8FA',
  cardBg: '#FFFFFF',
  sidebarBg: '#FAFBFC',

  // Text
  textPrimary: '#0F172A',
  textSecondary: '#475569',
  textTertiary: '#94A3B8',

  // Brand
  primary: '#3B82F6',
  primaryHover: '#2563EB',
  primaryLight: '#EFF6FF',

  // Semantic
  success: '#10B981',
  warning: '#F59E0B',
  warningLight: '#FFFBEB',
  error: '#EF4444',

  // Borders & dividers
  border: '#E2E8F0',
  divider: '#F1F5F9',

  // Shadows (tailwind-style)
  shadowSm: '0 1px 2px rgba(0,0,0,0.04)',
  shadowMd: '0 2px 8px rgba(0,0,0,0.06)',
  shadowCard: '0 1px 2px rgba(0,0,0,0.04), 0 2px 8px rgba(0,0,0,0.06)',
  shadowHeader: '0 1px 3px rgba(0,0,0,0.04)',
  shadowPopover: '0 4px 16px rgba(0,0,0,0.08)',
} as const;

// ── Chart palette ──────────────────────────────────────
export const CHART = {
  series: ['#3B82F6', '#10B981', '#F59E0B', '#8B5CF6', '#EC4899', '#06B6D4', '#64748B'] as const,

  gantt: {
    N2: '#3B82F6',
    N5: '#10B981',
    N2Hover: '#2563EB',
    N5Hover: '#059669',
  },

  kitSpan: '#F59E0B',
  utilization: '#3B82F6',

  gridLine: '#F1F5F9',
  axisLabel: '#94A3B8',
} as const;

// ── Process stage colours ──────────────────────────────
export const STAGE = {
  cut: '#1E3A5F',
  sort: '#3B82F6',
  autoGrind: '#10B981',
  manualGrind: '#6366F1',
  autoBevel: '#F59E0B',
  manualBevel: '#EF4444',
  agv: '#94A3B8',
  timeline: '#F59E0B',
} as const;

export const STAGE_COLORS: Record<string, string> = {
  '切割': STAGE.cut,
  '分拣': STAGE.sort,
  '自动打磨': STAGE.autoGrind,
  '人工打磨': STAGE.manualGrind,
  '自动坡口': STAGE.autoBevel,
  '人工坡口': STAGE.manualBevel,
  'AGV转运': STAGE.agv,
};

// ── Typography ─────────────────────────────────────────
export const FONT = {
  stack: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Microsoft YaHei', sans-serif",
  sizeXs: 11,
  sizeSm: 12,
  sizeBase: 14,
  sizeLg: 16,
  sizeXl: 20,
  sizeH1: 24,
  weightNormal: 400,
  weightMedium: 500,
  weightSemibold: 600,
  weightBold: 700,
} as const;

// ── Spacing ────────────────────────────────────────────
export const SPACE = {
  xs: 4,
  sm: 8,
  md: 16,
  lg: 24,
  xl: 32,
  xxl: 48,
} as const;

// ── Radii ──────────────────────────────────────────────
export const RADIUS = {
  sm: 6,
  md: 8,
  lg: 12,
  xl: 16,
} as const;
