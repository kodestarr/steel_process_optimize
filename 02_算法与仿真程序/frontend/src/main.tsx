import React, { Component } from 'react'
import { createRoot } from 'react-dom/client'
import { ConfigProvider } from 'antd'
import './index.css'
import App from './App.tsx'
import { COLORS, FONT, RADIUS } from './theme.ts'

// Ant Design 6 theme — light industrial theme tokens
const baseTheme = {
  token: {
    colorPrimary: COLORS.primary,
    colorSuccess: COLORS.success,
    colorWarning: COLORS.warning,
    colorError: COLORS.error,
    colorInfo: COLORS.primary,
    colorTextBase: COLORS.textPrimary,
    colorTextSecondary: COLORS.textSecondary,
    colorTextTertiary: COLORS.textTertiary,
    colorBgBase: COLORS.cardBg,
    colorBgContainer: COLORS.cardBg,
    colorBgLayout: COLORS.pageBg,
    colorBorder: COLORS.border,
    colorBorderSecondary: COLORS.divider,
    borderRadius: RADIUS.md,
    borderRadiusLG: RADIUS.lg,
    fontFamily: FONT.stack,
    fontSize: FONT.sizeBase,
    fontSizeLG: FONT.sizeLg,
    fontSizeSM: FONT.sizeSm,
    lineHeight: 1.6,
    controlHeight: 36,
    controlHeightLG: 42,
    paddingContentHorizontal: 20,
    paddingContentVertical: 16,
    boxShadow: COLORS.shadowSm,
    boxShadowSecondary: COLORS.shadowCard,
  },
  components: {
    Card: {
      borderRadiusLG: RADIUS.lg,
      paddingLG: 24,
      colorBgContainer: COLORS.cardBg,
    },
    Button: {
      borderRadius: RADIUS.md,
      controlHeight: 36,
      primaryShadow: 'none',
    },
    Table: {
      borderRadius: 10,
      headerBg: '#F8FAFC',
      headerColor: COLORS.textSecondary,
      borderColor: COLORS.divider,
    },
    Tabs: {
      inkBarColor: COLORS.primary,
      itemActiveColor: COLORS.primary,
      itemHoverColor: COLORS.primaryHover,
      itemColor: COLORS.textSecondary,
    },
    Tag: {
      borderRadiusSM: RADIUS.sm,
    },
    Modal: {
      borderRadiusLG: RADIUS.xl,
    },
    Slider: {
      trackBg: COLORS.primary,
      trackHoverBg: COLORS.primaryHover,
      handleColor: COLORS.primary,
      handleActiveColor: COLORS.primaryHover,
    },
    Collapse: {
      borderRadiusLG: RADIUS.lg,
      headerBg: COLORS.sidebarBg,
      contentPadding: '16px 24px',
      colorBorder: COLORS.divider,
    },
    Input: {
      borderRadius: RADIUS.md,
    },
    Select: {
      borderRadius: RADIUS.md,
    },
    Descriptions: {
      titleMarginBottom: 16,
    },
    Statistic: {
      contentFontSize: 28,
    },
  },
}

// 全局错误边界：防止任何组件 crash 导致白屏
class ErrorBoundary extends Component<{ children: React.ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }
  static getDerivedStateFromError(e: Error) { return { error: e } }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 40, textAlign: 'center' }}>
          <h2 style={{ color: COLORS.textPrimary }}>页面出错了</h2>
          <p style={{ color: COLORS.textTertiary }}>{this.state.error.message}</p>
          <button
            onClick={() => { this.setState({ error: null }); window.location.reload() }}
            style={{
              marginTop: 16,
              padding: '8px 24px',
              cursor: 'pointer',
              borderRadius: RADIUS.md,
              border: 'none',
              background: COLORS.primary,
              color: '#fff',
              fontSize: 14,
            }}
          >
            刷新页面
          </button>
        </div>
      )
    }
    return this.props.children
  }
}

createRoot(document.getElementById('root')!).render(
  <ErrorBoundary>
    <ConfigProvider theme={baseTheme}>
      <App />
    </ConfigProvider>
  </ErrorBoundary>,
)
