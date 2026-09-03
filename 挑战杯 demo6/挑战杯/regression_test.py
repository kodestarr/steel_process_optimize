"""Loop 2 regression test — full optimization across all 9 datasets."""
from steel_schedule_model import *
from improved_optimizer import run_multi_strategy_inline
from pathlib import Path
import time, sys, json

all_ds = ['data_' + str(i) for i in range(9)]
results = {}
total_t0 = time.time()

for ds in all_ds:
    data_path = Path('data/' + ds + '/附件2：钢板零件数据.xlsx')
    speed_path = Path('data/' + ds + '/附件3：工艺用时计算表.xlsx')
    if not data_path.exists():
        continue

    t0 = time.time()
    plates, parts, checks = load_and_validate(data_path)
    cfg = ModelConfig()
    cfg.local_search_iterations = 600  # Real optimization
    pp = load_process_params(speed_path)
    cfg = cfg.adapt_to_data(plates, parts, pp)
    features = plate_features(plates, parts, cfg, pp.speed_table)

    result = run_multi_strategy_inline(plates, parts, features, cfg, pp, pp.speed_table, checks, iterations=600)
    elapsed = time.time() - t0
    opt = result['opt_metrics']
    base = result['base_metrics']

    cmax = opt['总完工时间(h)']
    kitspan = opt['加权平均齐套跨度(h)']
    oee = opt['切割机综合稼动率']*100
    thr = opt['整体产能(张板/班)']
    pure_oee = opt['切割机纯切割稼动率']*100
    deadlocks = opt.get('死锁警告数', 0)

    flags = []
    if cmax > 67.25: flags.append('CMAX')
    if kitspan > 12.7: flags.append('KIT')
    if oee < 61: flags.append('OEE')
    if thr < 13: flags.append('THR')

    results[ds] = {
        'plates': checks['钢板数'], 'parts': checks['零件数'],
        'cmax': cmax, 'kitspan': kitspan, 'oee': oee, 'thr': thr,
        'pure_oee': pure_oee, 'deadlocks': deadlocks,
        'flags': flags, 'time': elapsed,
        'auto_bev': cfg.auto_bevel_machines,
        'man_bev': cfg.manual_bevel_stations,
        'grinders': cfg.large_grinders,
        'strategy': result['strategy_name'],
    }

    status = 'PASS' if not flags else 'FAIL(' + ','.join(flags) + ')'
    resources = 'AB' + str(cfg.auto_bevel_machines) + '/MB' + str(cfg.manual_bevel_stations) + '/LG' + str(cfg.large_grinders)
    print(ds + ': ' + status + ' | ' + str(checks['钢板数']) + 'pl/' + str(checks['零件数']) + 'pt | ' +
          resources + ' | ' + result['strategy_name'] + ' | ' +
          'Cmax=' + str(round(cmax,1)) + 'h KitSpan=' + str(round(kitspan,1)) +
          'h OEE=' + str(round(oee,1)) + '% Thr=' + str(round(thr,1)) +
          ' PureOEE=' + str(round(pure_oee,1)) + '% t=' + str(round(elapsed)) + 's')
    sys.stdout.flush()

print()
total_elapsed = time.time() - total_t0
print('='* 60)
print('FULL REGRESSION COMPLETE: ' + str(round(total_elapsed)) + 's')
pass_count = sum(1 for r in results.values() if not r['flags'])
print('PASS: ' + str(pass_count) + '/' + str(len(results)))
print()
print('%-10s %6s %6s %8s %8s %8s %8s %8s %s' % ('Dataset', 'Plates', 'Parts', 'Cmax(h)', 'KitSpan', 'OEE%', 'Thr', 'PureOEE', 'Resources'))
print('-' * 90)
for ds, r in sorted(results.items()):
    flags_str = ' << FAIL:' + ','.join(r['flags']) if r['flags'] else ''
    print('%-10s %6d %6d %8.1f %8.1f %8.1f %8.1f %8.1f AB%d/MB%d/LG%d%s' % (
        ds, r['plates'], r['parts'], r['cmax'], r['kitspan'], r['oee'], r['thr'], r['pure_oee'],
        r['auto_bev'], r['man_bev'], r['grinders'], flags_str))

# Save results
with open('regression_results.json', 'w', encoding='utf-8') as f:
    json.dump({k: {kk: vv for kk, vv in v.items() if kk != 'flags'} for k, v in results.items()},
              f, ensure_ascii=False, indent=2, default=str)
print('\nResults saved to regression_results.json')
