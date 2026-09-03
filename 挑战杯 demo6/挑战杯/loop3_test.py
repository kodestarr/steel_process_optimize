"""Loop 3 comprehensive test script."""
import sys, io, time, hashlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from improved_optimizer import SATabuOptimizer
from steel_schedule_model import (ModelConfig, load_and_validate, load_process_params,
                                   plate_features, simulate, machine_names, make_greedy_schedule,
                                   build_joint_schedule)
from pathlib import Path
import pandas as pd, numpy as np

data_path = Path('question/产线场景描述/附件2：钢板零件数据.xlsx')
speed_path = Path('question/产线场景描述/附件3：工艺用时计算表.xlsx')
cfg = ModelConfig(); cfg.cutting_machines = 3
cfg.crane_overlap_minutes = 5.0  # Loop 3: max overlap (unload fully overlaps remainder)

plates, parts, checks = load_and_validate(data_path)
pp = load_process_params(speed_path)
features = plate_features(plates, parts, cfg, pp.speed_table)

name_col = features.columns[2]; seg_col = features.columns[1]
cut_cands = [c for c in features.columns if '工时' in c and 'min' in c]
if not cut_cands:
    raise KeyError(f"未找到切割工时列，可用列: {list(features.columns)}")
cut_col = cut_cands[0]
pri_cands = [c for c in features.columns if '优先' in c or '最低' in c]
if not pri_cands:
    raise KeyError(f"未找到优先级列，可用列: {list(features.columns)}")
pri_col = pri_cands[0]
seq_col = features.columns[0]

# 7 diverse initial solutions
init_solutions = {
    'FIFO': features.sort_values(seq_col)[name_col].tolist(),
    'Completeness': make_greedy_schedule(features, 'completeness', 20260723)['套料图名'].tolist(),
    'SegPriShort': features.sort_values([seg_col, pri_col, cut_col], ascending=[True,True,True])[name_col].tolist(),
    'SegPriLong': features.sort_values([seg_col, pri_col, cut_col], ascending=[True,True,False])[name_col].tolist(),
    'SPT': features.sort_values(cut_col, ascending=True)[name_col].tolist(),
    'LPT': features.sort_values(cut_col, ascending=False)[name_col].tolist(),
    'ThinFirst': features.sort_values('切割工时(min)', ascending=True)[name_col].tolist(),
}

# Get FIFO baseline with crane overlap
fidx = features.set_index(name_col)
machines_list = machine_names(cfg)
fifo_order = init_solutions['FIFO']
free = {m: 0.0 for m in machines_list}
crane_free = 0.0; crane_total = 8.0 - cfg.crane_overlap_minutes
rows = []
for seq, (_, r) in enumerate(fidx.loc[fifo_order].reset_index().iterrows(), 1):
    m = min(free, key=free.get)
    cs = max(free[m], crane_free); ce = cs + crane_total; crane_free = ce
    st = ce; en = st + r[cut_col]; free[m] = en
    rows.append({'套料图名': r[name_col], '分段号': r[seg_col], '切割机': m,
                 '切割开始(min)': st, '切割完成(min)': en,
                 '切割工时(min)': r[cut_col], '最低优先级': r[pri_col]})
sched_fifo = pd.DataFrame(rows)
_, _, fifo_met, _, _ = simulate(sched_fifo, parts, cfg, pp)

t0 = time.time()
all_candidates = []; all_stats = []

for name, init_order in init_solutions.items():
    # P1-5 修复：使用确定性 MD5 替代非确定性 Python hash()
    _name_hash = int(hashlib.md5(name.encode('utf-8')).hexdigest(), 16) % 100000
    opt = SATabuOptimizer(features, parts, cfg, pp, seed=20260723 + _name_hash)
    opt.fifo_cmax = fifo_met['总完工时间(h)']
    opt.fifo_kit_span = fifo_met['加权平均齐套跨度(h)']
    opt.fifo_load_diff = fifo_met['切割负载差(h)']
    order, metrics, stats = opt.optimize(init_order, max_iterations=350, time_limit_seconds=120)
    all_stats.append('[%s] %s' % (name, stats))
    all_candidates.append((name, order, metrics))
    for arch_order, arch_met in opt.pareto_archive:
        all_candidates.append(('%s-archive' % name, arch_order, arch_met))

# 3-tier selection
fifo_cmax = fifo_met['总完工时间(h)']; CMAX_TARGET = 67.25; KITSPAN_TARGET = 12.7
tier1 = [(n,o,m) for n,o,m in all_candidates
         if m['总完工时间(h)'] <= fifo_cmax and m['加权平均齐套跨度(h)'] <= KITSPAN_TARGET]
tier2 = [(n,o,m) for n,o,m in all_candidates
         if m['总完工时间(h)'] <= CMAX_TARGET and m['加权平均齐套跨度(h)'] <= KITSPAN_TARGET]
if tier1:
    best = min(tier1, key=lambda x: x[2]['加权平均齐套跨度(h)'])
    tier_label = 'Tier1(Cmax<=FIFO,KitSpan<=12.7)'
elif tier2:
    best = min(tier2, key=lambda x: x[2]['总完工时间(h)'])
    tier_label = 'Tier2(Cmax<=67.25,KitSpan<=12.7)'
else:
    best = min(all_candidates, key=lambda x:
        (x[2]['总完工时间(h)']-CMAX_TARGET)/CMAX_TARGET*10 +
        (x[2]['加权平均齐套跨度(h)']-KITSPAN_TARGET)/KITSPAN_TARGET*10)
    tier_label = 'Tier3(fallback)'
best_name, best_order, best_metrics = best
elapsed = time.time() - t0

# Build final schedule
def build_sched(order_names):
    ordered = fidx.loc[order_names].reset_index()
    free2 = {m: 0.0 for m in machines_list}; crane_free2 = 0.0
    ct = 8.0 - cfg.crane_overlap_minutes
    rows2 = []
    for seq_i, (_, r) in enumerate(ordered.iterrows(), 1):
        m = min(free2, key=free2.get)
        cs = max(free2[m], crane_free2); ce = cs + ct; crane_free2 = ce
        st = ce; en = st + r[cut_col]; free2[m] = en
        rows2.append({'套料图名': r[name_col], '分段号': r[seg_col], '切割机': m,
                       '切割开始(min)': st, '切割完成(min)': en,
                       '切割工时(min)': r[cut_col], '最低优先级': r[pri_col]})
    sched2 = pd.DataFrame(rows2)
    new_sched2, _, _, met2, stg2, _ = build_joint_schedule(sched2, parts, cfg, pp)
    return new_sched2, met2, stg2

opt_sched, opt_metrics, opt_stages = build_sched(best_order)
base_sched, base_metrics, base_stages = build_sched(fifo_order)

print()
print('='*70)
print('  LOOP 3 FINAL RESULTS')
print('  Selection: %s | %.1fs' % (tier_label, elapsed))
print('  Best source: %s' % best_name)
print('  Candidates: %d total (Tier1=%d, Tier2=%d)' % (
    len(all_candidates), len(tier1), len(tier2)))
print('='*70)

cmax_d = (opt_metrics['总完工时间(h)'] - base_metrics['总完工时间(h)']) / base_metrics['总完工时间(h)'] * 100
kit_d = (base_metrics['加权平均齐套跨度(h)'] - opt_metrics['加权平均齐套跨度(h)']) / base_metrics['加权平均齐套跨度(h)'] * 100

print('FIFO Cmax:       %.2fh  |  OPT: %.2fh  |  Delta: %+.1f%%' % (
    base_metrics['总完工时间(h)'], opt_metrics['总完工时间(h)'], cmax_d))
print('FIFO KitSpan:    %.2fh  |  OPT: %.2fh  |  Delta: %+.1f%%' % (
    base_metrics['加权平均齐套跨度(h)'], opt_metrics['加权平均齐套跨度(h)'], kit_d))
print('FIFO Throughput: %.2f  |  OPT: %.2f' % (
    base_metrics['整体产能(张板/班)'], opt_metrics['整体产能(张板/班)']))
print('FIFO Pure OEE:   %.1f%%  |  OPT: %.1f%%' % (
    base_metrics['切割机纯切割稼动率']*100, opt_metrics['切割机纯切割稼动率']*100))
print('FIFO OEE:        %.1f%%  |  OPT: %.1f%%' % (
    base_metrics['切割机综合稼动率']*100, opt_metrics['切割机综合稼动率']*100))
print('FIFO LoadDiff:   %.2fh  |  OPT: %.2fh' % (
    base_metrics['切割负载差(h)'], opt_metrics['切割负载差(h)']))

print()
print('--- KPI CHECKS ---')
checks_list = [
    ('Throughput >=13', opt_metrics['整体产能(张板/班)'], 13, '>='),
    ('Cmax <=67.25h', opt_metrics['总完工时间(h)'], 67.25, '<='),
    ('OEE >=61%', opt_metrics['切割机综合稼动率']*100, 61, '>='),
    ('KitSpan <=12.7h', opt_metrics['加权平均齐套跨度(h)'], 12.7, '<='),
    ('Pure OEE >=50%', opt_metrics['切割机纯切割稼动率']*100, 50, '>='),
    ('TIER 1 (Cmax <= FIFO)', opt_metrics['总完工时间(h)'], base_metrics['总完工时间(h)'], '<='),
]
for label, val, target, op in checks_list:
    ok = val >= target if op == '>=' else val <= target
    print('[%s] %s: %.2f (target %s)' % ('PASS' if ok else 'FAIL', label, val, target))

print()
print('--- LOOP 3 NEW FEATURES ---')
print('[v] Tier 1 focused descent (80 iterations post-SA)')
print('[v] Crane overlap optimization (%d min/plate)' % cfg.crane_overlap_minutes)
print('[v] 7 initial strategies (added LPT)')
print('[v] 350 SA iterations per start (was 250)')
print('[v] DRL Pareto-guided operator selection')
print('[v] Expanded Pareto archive (50 sols)')
