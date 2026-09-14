import React from 'react';
import { Typography, Divider } from 'antd';
import {
  CheckCircleFilled,
  ExclamationCircleFilled,
  ArrowDownOutlined,
} from '@ant-design/icons';
import './ModelArchitecture.css';

const { Title, Text } = Typography;

/* ── helper: layer header bar ────────────────────────── */
const Hdr: React.FC<{ level: string; children: React.ReactNode }> = ({ level, children }) => (
  <div className={`ma-layer-header ${level}`}>{children}</div>
);

/* ── helper: down arrow ──────────────────────────────── */
const Down: React.FC = () => (
  <div className="ma-layer-arrow"><span><ArrowDownOutlined /></span></div>
);

/* ── Component ───────────────────────────────────────── */
const ModelArchitecture: React.FC = () => {
  return (
    <div className="ma-container">
      <Title level={4} style={{ marginBottom: 20 }}>🏭 模型全景架构</Title>

      {/* ═══════════ 1. INPUT ═══════════ */}
      <div className="ma-layer">
        <Hdr level="l0">📥 输入层</Hdr>
        <div className="ma-layer-body">
          <div className="ma-input-cols">
            <div className="ma-input-col">
              <h4>📋 附件2：钢板零件数据（必选）</h4>
              <ul>
                <li>Sheet「钢板数据」— 按附件自动统计</li>
                <li>N2 / N5 两台切割机</li>
                <li>Sheet「零件数据」— 按附件自动统计</li>
                <li>大件 / 小件按附件自动统计</li>
              </ul>
            </div>
            <div className="ma-input-col">
              <h4>⚡ 附件3：工艺用时计算表（可选）</h4>
              <ul>
                <li>54 档厚度 → 速度对照表 (6mm~50mm)</li>
                <li>直切速度 / V&Y坡速度 / X&K坡速度</li>
                <li>用于厚度相关切割速度查表 + 线性插值</li>
                <li>未上传时回退到 ModelConfig 默认值</li>
              </ul>
            </div>
          </div>
        </div>
      </div>
      <Down />

      {/* ═══════════ 2. VALIDATION ═══════════ */}
      <div className="ma-layer">
        <Hdr level="l1">🔍 数据校验层 (load_and_validate)</Hdr>
        <div className="ma-layer-body">
          <div className="ma-check-grid">
            {[
              ['必填列检查（16 个字段）', true],
              ['主键唯一性（套料图名、零件名）', true],
              ['零件 → 钢板关联完整性', true],
              ['数量汇总一致性（零件数 / 大小件数 / V坡长度）', true],
              ['打磨长度缺失标记（以 0 替代，不阻断）', false],
              ['零件类型归一化（小件→small, 大件→large）', true],
              ['人工坡口长度计算：Y坡 + X坡 + K坡（I坡不计入）', true],
              ['自动坡口长度计算：Y坡 + X坡 + K坡（I坡不计入）', true],
            ].map(([label, ok]) => (
              <div className="ma-check-item" key={label as string}>
                {ok
                  ? <CheckCircleFilled className="icon-ok" />
                  : <ExclamationCircleFilled className="icon-warn" />
                }
                <span>{label}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
      <Down />

      {/* ═══════════ 3. FEATURES ═══════════ */}
      <div className="ma-layer">
        <Hdr level="l2">⚙️ 特征工程层 (plate_features)</Hdr>
        <div className="ma-layer-body">
          <div className="ma-feature-grid">
            <div className="ma-feature-card">
              <div className="fc-icon">⏱️</div>
              <div className="fc-name">切割工时</div>
              <div className="fc-desc">f(直切, V坡, 空行程, 划线, 穿孔)<br />有附件3按厚度查表+线性插值</div>
            </div>
            <div className="ma-feature-card">
              <div className="fc-icon">📦</div>
              <div className="fc-name">覆盖齐套组数</div>
              <div className="fc-desc">该钢板零件涉及几个<br />「分段+优先级」组</div>
            </div>
            <div className="ma-feature-card">
              <div className="fc-icon">⚖️</div>
              <div className="fc-name">齐套权重</div>
              <div className="fc-desc">Σ(1/组零件数)<br />组越小权重越大</div>
            </div>
            <div className="ma-feature-card">
              <div className="fc-icon">🔢</div>
              <div className="fc-name">最低优先级</div>
              <div className="fc-desc">该钢板上零件中<br />最紧迫的优先级</div>
            </div>
          </div>
        </div>
      </div>
      <Down />

      {/* ═══════════ 4. SCHEDULING ═══════════ */}
      <div className="ma-layer">
        <Hdr level="l3">🧠 调度决策层</Hdr>
        <div className="ma-layer-body">
          <Text>决策变量：<strong>全部钢板的加工次序 + N2/N5 分配</strong></Text>
          <br />
          <Text type="secondary" style={{ fontSize: 12 }}>约束：每时刻每台切割机只能切一张钢板</Text>
          <div className="ma-decision-row">
            <div className="ma-strategy-card">
              <h4>策略 A：FIFO</h4>
              <p>按「序号」原始顺序依次加工</p>
            </div>
            <div className="ma-strategy-card">
              <h4>策略 B：Completeness（齐套感知）</h4>
              <p>按 最低优先级↑ → 齐套权重↓ → 覆盖齐套组数↓ → 切割工时↑ 排序<br />优先释放稀缺齐套组</p>
            </div>
          </div>
        </div>
      </div>
      <Down />

      {/* ═══════════ 5. SIMULATION ⭐ ═══════════ */}
      <div className="ma-layer">
        <Hdr level="l4">⭐ 离散事件仿真层 (simulate) — 核心</Hdr>
        <div className="ma-layer-body">

          <Text strong>ResourcePool（通用资源池）</Text>
          <div className="ma-buffer-box" style={{ background: 'var(--bg-sidebar, #FAFBFC)', border: '1px solid var(--border, #E2E8F0)', color: 'var(--text-primary, #0F172A)', marginTop: 8, marginBottom: 12 }}>
            reserve(ready_time, duration) → (资源名, 开始, 结束)<br />
            逻辑：选最早空闲的资源 → 取 max(ready, free) 开始
          </div>

          <Text strong>10 种资源池</Text>
          <table className="ma-res-table">
            <thead>
              <tr>
                <th>资源</th><th>数量</th><th>说明</th>
              </tr>
            </thead>
            <tbody>
              <tr><td>🔪 N2/N5 切割机</td><td>2 台</td><td className="dim">上层决策变量 · 钢板排序与分配</td></tr>
              <tr><td>📦 自动分拣</td><td>1 台</td><td className="dim">统一处理大小件</td></tr>
              <tr><td>🔧 自动打磨</td><td>1 台</td><td className="dim">仅小件，长×2/速 + 扫描 1min</td></tr>
              <tr><td>👷 人工打磨</td><td>2 工位</td><td className="dim">仅大件，长/速</td></tr>
              <tr><td>⚡ 自动坡口</td><td>1 台</td><td className="dim">仅小件（有坡口长度时）</td></tr>
              <tr><td>🔨 人工坡口</td><td>2 工位</td><td className="dim">仅大件（有坡口长度时）</td></tr>
              <tr><td>🚛 AGV</td><td>1 台</td><td className="dim">转运至齐套区</td></tr>
              <tr><td>🗄️ 成品缓存</td><td>50 件</td><td className="dim">满时阻塞上游</td></tr>
            </tbody>
          </table>

          <Text strong style={{ display: 'block', marginTop: 16 }}>每个零件的工序链（按切割完成时间排序后逐个推进）</Text>
          <div className="ma-routes">
            <div className="ma-route">
              <div className="ma-route-header small">🟢 小件路线（700 件）</div>
              <div className="ma-route-body">
                切割完成 → 分拣 → 自动打磨 → 自动坡口(如有) → AGV → 齐套区
              </div>
            </div>
            <div className="ma-route">
              <div className="ma-route-header large">🔵 大件路线</div>
              <div className="ma-route-body">
                切割完成 → 分拣 → 人工打磨 → 人工坡口(如有) → AGV → 齐套区
              </div>
            </div>
          </div>

          <div className="ma-buffer-box">
            <strong>有限缓存逻辑：</strong> if 齐套区缓存已满 (≥ 50 件) → 零件延迟到最早一次 AGV 取走完成后才可进入
          </div>
        </div>
      </div>
      <Down />

      {/* ═══════════ 6. OUTPUT ═══════════ */}
      <div className="ma-layer">
        <Hdr level="l5">📊 输出层</Hdr>
        <div className="ma-layer-body">
          <div className="ma-output-grid">
            <div className="ma-output-card">
              <h4>part_completion.csv</h4>
              <p>每个零件的释放时刻 + 到齐套区时刻<br /><Text type="secondary">按附件零件数</Text></p>
            </div>
            <div className="ma-output-card">
              <h4>kit_groups.csv</h4>
              <p>每「分段号+优先级」组的首件到达 / 齐套完成 / 齐套跨度<br /><Text type="secondary">按附件分段与优先级统计</Text></p>
            </div>
            <div className="ma-output-card">
              <h4>process_stages.csv</h4>
              <p>逐工序资源占用记录：零件名 / 工序 / 资源 / 开始 / 结束 / 时长<br /><Text type="secondary">按附件零件数与工序数统计</Text></p>
            </div>
            <div className="ma-output-card">
              <h4>comparison.csv + PNG×3</h4>
              <p>FIFO vs 优化对比表 + 切割甘特图 / 齐套跨度图 / 利用率图</p>
            </div>
          </div>

          <Text strong>7 项 KPI 指标</Text>
          <div className="ma-kpi-list" style={{ marginTop: 8 }}>
            <div><span className="kpi-name">总完工时间 (h)</span> <span className="kpi-formula">= max(到齐套区时刻) / 60</span></div>
            <div><span className="kpi-name">N2 利用率</span> <span className="kpi-formula">= N2 工时 / 总完工时间</span></div>
            <div><span className="kpi-name">加权平均齐套跨度 (h)</span> <span className="kpi-formula">= Σ(零件数×跨度) / Σ零件数 / 60</span></div>
            <div><span className="kpi-name">N5 利用率</span> <span className="kpi-formula">= N5 工时 / 总完工时间</span></div>
            <div><span className="kpi-name">最大齐套跨度 (h)</span> <span className="kpi-formula">= max(跨度) / 60</span></div>
            <div><span className="kpi-name">切割机平均利用率</span> <span className="kpi-formula">= (N2+N5) / 2 / 总完工时间</span></div>
            <div><span className="kpi-name">切割负载差 (h)</span> <span className="kpi-formula">= (N2 工时 − N5 工时) / 60</span></div>
          </div>
        </div>
      </div>

      <Divider style={{ margin: '28px 0' }}>
        <Text type="secondary" style={{ fontSize: 13, fontWeight: 600 }}>🧬 优化引擎</Text>
      </Divider>

      {/* ═══════════ 7. OPTIMIZER ═══════════ */}
      <div className="ma-layer">
        <Hdr level="l6">🧬 run_multi_strategy_inline()</Hdr>
        <div className="ma-layer-body">
          <div className="ma-opt-steps">
            <div className="ma-opt-step">
              <div className="ma-opt-num">1</div>
              <div className="ma-opt-body">
                <strong>生成 5 种构造策略的初始排序</strong>
                <div className="ma-strat-grid">
                  <div className="ma-strat-tag s0">FIFO<br /><span style={{fontSize:9,fontWeight:400}}>原始序号顺序</span></div>
                  <div className="ma-strat-tag s1">SegPriShortFirst<br /><span style={{fontSize:9,fontWeight:400}}>分段→优先级→短工时先</span></div>
                  <div className="ma-strat-tag s2">SegPriLongFirst<br /><span style={{fontSize:9,fontWeight:400}}>分段→优先级→长工时先</span></div>
                  <div className="ma-strat-tag s3">SegLongFirst<br /><span style={{fontSize:9,fontWeight:400}}>分段→长工时先</span></div>
                  <div className="ma-strat-tag s4">SegShortFirst<br /><span style={{fontSize:9,fontWeight:400}}>分段→短工时先</span></div>
                </div>
              </div>
            </div>
            <div className="ma-opt-step">
              <div className="ma-opt-num">2</div>
              <div className="ma-opt-body">
                <strong>每种策略评估后微调</strong><br />
                对每种排序运行完整仿真 → 计算目标函数值 → 邻位交换微调 (~13 次)：交换相邻钢板 → 若改善则采纳
              </div>
            </div>
            <div className="ma-opt-step">
              <div className="ma-opt-num">3</div>
              <div className="ma-opt-body">
                <strong>选最优目标函数值</strong>
                <div className="ma-formula-dark" style={{ marginTop: 8, padding: '10px 16px', fontSize: 13 }}>
                  <span className="hl">J</span> = <span className="ho">0.40</span> × C<span className="dim">max</span> + <span className="ho">0.40</span> × T̄<span className="dim">kit</span> + <span className="ho">0.20</span> × ΔL
                </div>
              </div>
            </div>
            <div className="ma-opt-step">
              <div className="ma-opt-num">4</div>
              <div className="ma-opt-body">
                <strong>防御性保证</strong><br />
                若优化结果差于 FIFO，退回 FIFO（确保不劣于基线）
              </div>
            </div>
          </div>
        </div>
      </div>

      <Divider style={{ margin: '28px 0' }}>
        <Text type="secondary" style={{ fontSize: 13, fontWeight: 600 }}>📐 关键参数体系</Text>
      </Divider>

      {/* ═══════════ 8. PARAMS ═══════════ */}
      <div className="ma-layer">
        <Hdr level="l7">📐 ModelConfig（默认值，可被附件3覆盖）</Hdr>
        <div className="ma-layer-body">
          <div className="ma-param-tree">
            <div><span className="branch">├─ 切割速度</span></div>
            <div style={{ paddingLeft: 16 }}>
              <span className="leaf">├─ cut_speed_mm_min:</span> <span className="val">1700</span> <span className="comment">直切速度 (mm/min)</span>
            </div>
            <div style={{ paddingLeft: 16 }}>
              <span className="leaf">├─ v_cut_speed_mm_min:</span> <span className="val">800</span> <span className="comment">V坡速度 (mm/min)</span>
            </div>
            <div style={{ paddingLeft: 16 }}>
              <span className="leaf">├─ rapid_speed_mm_min:</span> <span className="val">24000</span> <span className="comment">空行程快移 (mm/min)</span>
            </div>
            <div style={{ paddingLeft: 16 }}>
              <span className="leaf">├─ marking_speed_mm_min:</span> <span className="val">24000</span> <span className="comment">划线速度 (mm/min)</span>
            </div>
            <div style={{ paddingLeft: 16 }}>
              <span className="leaf">├─ pierce_minutes:</span> <span className="val">0.1</span> <span className="comment">单次穿孔 (min)</span>
            </div>
            <div style={{ paddingLeft: 16 }}>
              <span className="leaf">└─ plate_setup_minutes:</span> <span className="val">3</span> <span className="comment">换板准备 (min)</span>
            </div>

            <div style={{ marginTop: 4 }}><span className="branch">├─ 加工速度</span></div>
            <div style={{ paddingLeft: 16 }}>
              <span className="leaf">├─ small_grind_speed_mm_min:</span> <span className="val">1800</span> <span className="comment">小件打磨</span>
            </div>
            <div style={{ paddingLeft: 16 }}>
              <span className="leaf">├─ large_grind_speed_mm_min:</span> <span className="val">900</span> <span className="comment">大件打磨</span>
            </div>
            <div style={{ paddingLeft: 16 }}>
              <span className="leaf">├─ auto_bevel_speed_mm_min:</span> <span className="val">1000</span> <span className="comment">自动坡口</span>
            </div>
            <div style={{ paddingLeft: 16 }}>
              <span className="leaf">└─ manual_bevel_speed_mm_min:</span> <span className="val">700</span> <span className="comment">人工坡口</span>
            </div>

            <div style={{ marginTop: 4 }}><span className="branch">├─ 物流参数</span></div>
            <div style={{ paddingLeft: 16 }}>
              <span className="leaf">├─ small_transfer_minutes:</span> <span className="val">1</span> <span className="comment">小件转运 (min)</span>
            </div>
            <div style={{ paddingLeft: 16 }}>
              <span className="leaf">├─ large_transfer_minutes:</span> <span className="val">2</span> <span className="comment">大件转运 (min)</span>
            </div>
            <div style={{ paddingLeft: 16 }}>
              <span className="leaf">└─ small_sort_minutes:</span> <span className="val">0.3</span> <span className="comment">分拣时间 (min)</span>
            </div>

            <div style={{ marginTop: 4 }}><span className="branch">├─ 资源配置</span></div>
            <div style={{ paddingLeft: 16 }}>
              <span className="leaf">├─ small_grinders:</span> <span className="val">1</span> <span className="comment">小件打磨机数</span>
            </div>
            <div style={{ paddingLeft: 16 }}>
              <span className="leaf">├─ large_grinders:</span> <span className="val">2</span> <span className="comment">大件打磨工位数</span>
            </div>
            <div style={{ paddingLeft: 16 }}>
              <span className="leaf">├─ auto_bevel_machines:</span> <span className="val">1</span> <span className="comment">自动坡口机数</span>
            </div>
            <div style={{ paddingLeft: 16 }}>
              <span className="leaf">├─ manual_bevel_stations:</span> <span className="val">2</span> <span className="comment">人工坡口工位数</span>
            </div>
            <div style={{ paddingLeft: 16 }}>
              <span className="leaf">├─ agvs:</span> <span className="val">1</span> <span className="comment">AGV 数量</span>
            </div>
            <div style={{ paddingLeft: 16 }}>
              <span className="leaf">└─ finish_buffer_capacity:</span> <span className="val">50</span> <span className="comment">齐套区缓存容量</span>
            </div>

            <div style={{ marginTop: 4 }}><span className="branch">└─ 算法参数</span></div>
            <div style={{ paddingLeft: 16 }}>
              <span className="leaf">├─ local_search_iterations:</span> <span className="val">260</span> <span className="comment">搜索迭代次数</span>
            </div>
            <div style={{ paddingLeft: 16 }}>
              <span className="leaf">└─ random_seed:</span> <span className="val">20260723</span> <span className="comment">固定随机种子（可复现）</span>
            </div>
          </div>
        </div>
      </div>

      <Divider style={{ margin: '28px 0' }}>
        <Text type="secondary" style={{ fontSize: 13, fontWeight: 600 }}>🔗 核心公式</Text>
      </Divider>

      {/* ═══════════ 9. FORMULA ═══════════ */}
      <div className="ma-layer">
        <Hdr level="l8">🔗 切割工时公式</Hdr>
        <div className="ma-layer-body">
          <div className="ma-formula-dark">
            <span className="hl">T</span><span className="dim">cut</span> = L<span className="dim">straight</span> / v<span className="dim">straight</span><span className="hg">(厚度)</span> <span className="dim">← 直线切割</span>
            <br />
            &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;+ L<span className="dim">V坡</span> / v<span className="dim">V坡</span><span className="hg">(厚度)</span> <span className="dim">← V坡口切割</span>
            <br />
            &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;+ L<span className="dim">空行程</span> / v<span className="dim">快移</span> <span className="dim">← 空行程快移</span>
            <br />
            &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;+ L<span className="dim">划线</span> / v<span className="dim">划线</span> <span className="dim">← 划线</span>
            <br />
            &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;+ N<span className="dim">穿孔</span> × t<span className="dim">穿孔</span> <span className="dim">← 穿孔</span>
            <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid #334155' }}>
              <span className="dim">其中 v</span><span className="dim">straight</span><span className="hg">(厚度)</span> <span className="dim">和 v</span><span className="dim">V坡</span><span className="hg">(厚度)</span> <span className="dim">来自附件3的54档厚度速度表（6mm~50mm）</span>
              <br />
              <span className="dim">未覆盖厚度使用相邻档位线性插值</span>
            </div>
          </div>
        </div>
      </div>

      <div style={{ height: 40 }} />
    </div>
  );
};

export default ModelArchitecture;
