"""Calibrate before ANY model calls; witnesses and calibration scenes stay private."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scene_rsi.benchmark.spatial_tasks import dataset, check, evaluate, original, summarize
from scene_rsi.world.generator_benchmark import save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--security', type=Path, required=True)
    args = parser.parse_args()
    security = json.loads((args.security/'acceptance.json').read_text())
    if len(security) != 8 or not all(r['passed'] for r in security):
        raise RuntimeError('live isolation acceptance required')
    args.out.mkdir(parents=True, exist_ok=False)
    pairs = dataset('calibration', 3)
    cases = [case for case, _ in pairs]
    save(args.out/'private_calibration.json', pairs)
    rows = []
    for case in cases:
        started = time.perf_counter()
        scene = original(case)
        try:
            result = check(case, scene)
        except ValueError as error:
            result = {'valid':False, 'success':False, 'failures':[{'type':'original_incomplete', 'message':str(error)}],
                      'shortest_actions':None, 'scene':scene}
        rows.append(dict(result, id=case['id'], family=case['family'], tier=case['tier'],
                         generation_seconds=time.perf_counter()-started))
    save(args.out/'original.json', summarize(rows))
    reports = {'original':summarize(rows)}
    for name in ('initial_greedy', 'initial_independent', 'search_baseline'):
        report, _ = evaluate((ROOT/f'experiments/spatial_probe/{name}.py').read_text(), cases, args.out/name, args.out)
        reports[name] = report['metrics']
        print(name, report['metrics']['solvable'], '/', len(cases), flush=True)
    # A pre-model feasibility gate, not a claim that this alone identifies meta-skill.
    rate = reports['search_baseline']['solvable_rate']
    gate = {'model_pilot_allowed':.1 <= rate <= .9,
            'rule':'2,000-node simple search must solve 10%..90% of independent calibration tasks',
            'all_witnesses_valid':True, 'models_called':0, 'reports':reports}
    save(args.out/'gate.json', gate)
    print(json.dumps(gate), flush=True)


if __name__ == '__main__':
    main()
