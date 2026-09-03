"""Quick regression test - writes results to JSON file to avoid buffering issues"""
import sys, time, json, traceback
sys.path.insert(0, '.')
from steel_schedule_model import ModelConfig, load_and_validate, load_process_params, plate_features
from improved_optimizer import run_multi_strategy_inline
from pathlib import Path

results_file = Path(__file__).parent / '_regression_results.json'
results = []
errors = []

for i in range(9):
    ds = f'data/data_{i}'
    ds_path = Path(ds)
    data_file = ds_path / '附件2：钢板零件数据.xlsx'
    speed_file = ds_path / '附件3：工艺用时计算表.xlsx'

    if not data_file.exists():
        errors.append(f'{ds}: file not found')
        continue

    t0 = time.time()
    try:
        plates, parts, checks = load_and_validate(data_file)
        pp = load_process_params(speed_file)
        cfg = ModelConfig()
        cfg.local_search_iterations = 200
        cfg.random_seed = 20260723
        cfg = cfg.adapt_to_data(plates, parts, pp)
        features = plate_features(plates, parts, cfg, pp.speed_table)
        result = run_multi_strategy_inline(plates, parts, features, cfg, pp, pp.speed_table, checks, iterations=200)
        om = result['opt_metrics']
        elapsed = time.time() - t0
        r = {
            'dataset': ds, 'plates': int(checks['钢板数']), 'parts': int(checks['零件数']),
            'kit_groups': int(checks['齐套组数(分段+优先级)']), 'segments': int(parts['分段号'].nunique()),
            'cmax_h': round(float(om['总完工时间(h)']), 2),
            'kit_span_h': round(float(om['加权平均齐套跨度(h)']), 2),
            'max_kit_span_h': round(float(om.get('最大齐套跨度(h)', 0)), 2),
            'throughput': round(float(om['整体产能(张板/班)']), 2),
            'oee_comprehensive': round(float(om.get('切割机综合稼动率', 0))*100, 1),
            'oee_pure_cut': round(float(om.get('切割机纯切割稼动率', 0))*100, 1),
            'load_diff_h': round(float(om.get('切割负载差(h)', 0)), 2),
            'deadlock_count': int(om.get('死锁警告数', 0)),
            'agv_conflicts': int(om.get('AGV路径冲突次数', 0)),
            'bevel_peak_pct': round(float(om.get('坡口缓存区峰值占用率', 0))*100, 1),
            'kit_peak_pct': round(float(om.get('齐套缓存区峰值占用率', 0))*100, 1),
            'elapsed_s': round(elapsed, 1), 'error': None,
        }
        results.append(r)
        # Write incrementally
        with open(results_file, 'w', encoding='utf-8') as f:
            json.dump({'results': results, 'errors': errors}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        r = {
            'dataset': ds, 'error': str(e)[:200],
            'elapsed_s': round(time.time()-t0, 1),
        }
        results.append(r)
        errors.append(f'{ds}: {e}')
        traceback.print_exc()
        with open(results_file, 'w', encoding='utf-8') as f:
            json.dump({'results': results, 'errors': errors}, f, ensure_ascii=False, indent=2)

# Final summary
with open(results_file, 'w', encoding='utf-8') as f:
    json.dump({'results': results, 'errors': errors, 'complete': True}, f, ensure_ascii=False, indent=2)
print(f"Regression complete. {len([r for r in results if not r.get('error')])}/{len(results)} datasets passed.", flush=True)
