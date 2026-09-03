"""全量9数据集回归测试脚本"""
import sys, time, json, traceback
sys.path.insert(0, '.')
from steel_schedule_model import ModelConfig, load_and_validate, load_process_params, plate_features, simulate, machine_names
from improved_optimizer import run_multi_strategy_inline
from pathlib import Path

datasets = [(f'data/data_{i}', f'data/data_{i}/附件2：钢板零件数据.xlsx', f'data/data_{i}/附件3：工艺用时计算表.xlsx') for i in range(9) if Path(f'data/data_{i}').exists()]
results = []

SEP = "=" * 60
for ds, data_file, speed_file in datasets:
    print(f"\n{SEP}")
    print(f"Testing: {ds}")
    t0 = time.time()
    try:
        plates, parts, checks = load_and_validate(Path(data_file))
        pp = load_process_params(Path(speed_file))
        cfg = ModelConfig()
        cfg.local_search_iterations = 200
        cfg.random_seed = 20260723
        cfg = cfg.adapt_to_data(plates, parts, pp)

        features = plate_features(plates, parts, cfg, pp.speed_table)
        result = run_multi_strategy_inline(
            plates, parts, features, cfg, pp, pp.speed_table, checks, iterations=200
        )
        om = result['opt_metrics']
        elapsed = time.time() - t0

        r = {
            'dataset': ds,
            'plates': checks['钢板数'],
            'parts': checks['零件数'],
            'kit_groups': checks['齐套组数(分段+优先级)'],
            'segments': parts['分段号'].nunique(),
            'cmax_h': round(om['总完工时间(h)'], 2),
            'kit_span_h': round(om['加权平均齐套跨度(h)'], 2),
            'max_kit_span_h': round(om.get('最大齐套跨度(h)', 0), 2),
            'throughput': round(om['整体产能(张板/班)'], 2),
            'oee_comprehensive': round(om['切割机综合稼动率']*100, 1),
            'oee_pure_cut': round(om['切割机纯切割稼动率']*100, 1),
            'load_diff_h': round(om['切割负载差(h)'], 2),
            'deadlock_count': om.get('死锁警告数', 0),
            'agv_conflicts': om.get('AGV路径冲突次数', 0),
            'bevel_peak_pct': round(om.get('坡口缓存区峰值占用率', 0)*100, 1),
            'kit_peak_pct': round(om.get('齐套缓存区峰值占用率', 0)*100, 1),
            'elapsed_s': round(elapsed, 1),
            'error': None,
        }
        print(f"  Cmax={r['cmax_h']}h  KitSpan={r['kit_span_h']}h  Throughput={r['throughput']}  OEE={r['oee_comprehensive']}%  PureOEE={r['oee_pure_cut']}%  Deadlock={r['deadlock_count']}")
    except Exception as e:
        r = {
            'dataset': ds, 'plates': 0, 'parts': 0, 'kit_groups': 0, 'segments': 0,
            'cmax_h': 0, 'kit_span_h': 0, 'max_kit_span_h': 0,
            'throughput': 0, 'oee_comprehensive': 0, 'oee_pure_cut': 0,
            'load_diff_h': 0, 'deadlock_count': 0, 'agv_conflicts': 0,
            'bevel_peak_pct': 0, 'kit_peak_pct': 0,
            'elapsed_s': round(time.time()-t0, 1),
            'error': str(e)[:300],
        }
        print(f"  ERROR: {e}")
        traceback.print_exc()
    results.append(r)

print(f"\n{SEP}")
print("REGRESSION SUMMARY")
print(SEP)
header = f"| {'Dataset':<10} | {'Plates':>6} | {'Parts':>7} | {'Groups':>6} | {'Cmax(h)':>8} | {'KitSpan':>8} | {'OEE%':>6} | {'Pure%':>6} | {'Thruput':>8} | {'Deadlk':>6} | {'Status':<8} |"
sep = f"|{'-'*10}-|{'-'*6}-|{'-'*7}-|{'-'*6}-|{'-'*8}-|{'-'*8}-|{'-'*6}-|{'-'*6}-|{'-'*8}-|{'-'*6}-|{'-'*8}-|"
print(header)
print(sep)
for r in results:
    status = 'ERROR' if r['error'] else 'OK'
    print(f"| {r['dataset']:<10} | {r['plates']:>6} | {r['parts']:>7} | {r['kit_groups']:>6} | {r['cmax_h']:>8.1f} | {r['kit_span_h']:>8.1f} | {r['oee_comprehensive']:>5.1f}% | {r['oee_pure_cut']:>5.1f}% | {r['throughput']:>8.1f} | {r['deadlock_count']:>6} | {status:<8} |")

# Competition KPI validation for data_0
for r in results:
    if r['dataset'] == 'data/data_0' and not r['error']:
        print(f"\n=== Competition KPI Validation (data_0) ===")
        c1 = r['oee_comprehensive'] >= 61
        c2 = r['throughput'] >= 13
        c3 = r['kit_span_h'] <= 12.7
        c4 = r['cmax_h'] <= 67.25
        c5 = r['deadlock_count'] == 0
        print(f"  OEE >= 61%:       {r['oee_comprehensive']}% {'PASS' if c1 else 'FAIL'}")
        print(f"  Throughput >= 13:  {r['throughput']} 张/班 {'PASS' if c2 else 'FAIL'}")
        print(f"  KitSpan <= 12.7h:  {r['kit_span_h']}h {'PASS' if c3 else 'FAIL'}")
        print(f"  Cmax <= 67.25h:    {r['cmax_h']}h {'PASS' if c4 else 'FAIL'}")
        print(f"  Deadlock = 0:      {r['deadlock_count']} {'PASS' if c5 else 'FAIL'}")
        passed = sum([c1, c2, c3, c4, c5])
        print(f"  Total: {passed}/5 KPIs passed")
        break

print(f"\n{SEP}")
print("Regression complete.")
