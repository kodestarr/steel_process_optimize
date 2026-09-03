import React, { useEffect, useState } from 'react';
import './LoadingPipeline.css';

const stages = [
  { key: 'cut', label: '切割排程', icon: '✂️' },
  { key: 'sim', label: '离散仿真', icon: '🔬' },
  { key: 'opt', label: '局部搜索', icon: '🧠' },
  { key: 'done', label: '生成结果', icon: '📊' },
];

interface Props {
  visible: boolean;
  progress?: { elapsed: number; stage: string } | null;
}

const LoadingPipeline: React.FC<Props> = ({ visible, progress }) => {
  const [step, setStep] = useState(0);
  const [localElapsed, setLocalElapsed] = useState(0);
  const elapsed = progress?.elapsed ?? 0;
  const stageLabel = progress?.stage ?? '';

  // 独立计时：即使外层进度轮询被节流，已运行时间也会每秒刷新。
  useEffect(() => {
    if (!visible) {
      setLocalElapsed(0);
      return;
    }
    setLocalElapsed(0);
    const timer = setInterval(() => {
      setLocalElapsed((e) => e + 1);
    }, 1000);
    return () => clearInterval(timer);
  }, [visible]);

  const displayElapsed = Math.max(elapsed, localElapsed);

  // P0-9 FIX: Decouple stage cycling from progress updates.
  // Previously, `progress` changed every 2s, which cleared the 3s setInterval
  // before it could fire — the dots were frozen at stage 0 forever.
  // Now stage dots advance independently based only on `visible`.
  useEffect(() => {
    if (!visible) {
      setStep(0);
      return;
    }
    const timer = setInterval(() => {
      setStep((s) => (s + 1) % (stages.length + 1));
    }, 2500);  // cycle every 2.5s — independent of progress polling
    return () => clearInterval(timer);
  }, [visible]);

  if (!visible) return null;

  return (
    <div className="loading-overlay">
      <div className="loading-card">
        {/* 顶部齿轮动画 */}
        <div className="gear-box">
          <div className="gear gear-left" />
          <div className="gear-center">
            <div className="gear-core" />
          </div>
          <div className="gear gear-right" />
        </div>

        {/* 标题 */}
        <div className="loading-title">模型计算中</div>
        <div className="loading-subtitle">
          {stageLabel || '正在为您优化钢板排产方案'}
          {displayElapsed > 0 && <span style={{ marginLeft: 8, opacity: 0.7 }}>已运行 {Math.floor(displayElapsed)}s</span>}
        </div>

        {/* 进度条 */}
        <div className="pipeline-track">
          <div className="pipeline-fill">
            <div className="pipeline-shimmer" />
          </div>
          {/* 流动粒子 */}
          <div className="flow-particle p1" />
          <div className="flow-particle p2" />
          <div className="flow-particle p3" />
        </div>

        {/* 阶段指示器 */}
        <div className="stage-row">
          {stages.map((s, i) => (
            <div
              key={s.key}
              className={`stage-dot ${i < step ? 'done' : ''} ${i === step % stages.length ? 'active' : ''}`}
            >
              <span className="stage-icon">{s.icon}</span>
              <span className="stage-label">{s.label}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export default LoadingPipeline;
