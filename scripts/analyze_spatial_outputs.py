"""Post-run geometric diagnostics only; never changes scores or sends model feedback."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scene_rsi.benchmark.spatial_tasks import bounds, check, original, unpack, visible_fraction
from scene_rsi.schema import SceneSpec
from scene_rsi.world.generator_benchmark import save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if not (args.run/'finished.json').exists():
        raise RuntimeError('analysis after the experiment is closed')
    tasks = json.loads((args.run/'private_tasks.json').read_text())['test']
    lookup = {c['id']:c for c in tasks}
    signatures, framing, hidden = defaultdict(set), [], []
    counts = defaultdict(int)
    for path in args.run.glob('*/test_1/rows.jsonl'):
        for row in map(json.loads, path.read_text().splitlines()):
            if not row['success']:
                continue
            case = lookup[row['id']]
            packet = case['packet']
            spec = SceneSpec.from_dict(packet['spec'])
            _, poses = unpack(row['scene'])
            boxes = {k:bounds(spec, p) for k, p in poses.items()}
            key = case['family']+'/'+case['tier']
            counts[key] += 1
            signature = []
            for p in poses.values():
                a = spec.assets[p['asset']]
                signature.append((a.width,a.depth,a.height,p['x'],p['y'],p['z'],
                                  p['yaw_deg'] if a.width != a.depth else 0))
            signatures[key].add(tuple(sorted(signature)))
            for name, limits in packet['visibility'].items():
                if limits[1] < 1:
                    framing.append(visible_fraction(packet['view'], name, {name:boxes[name]}))
                    hidden.append(visible_fraction(packet['view'], name, boxes))
    original_results = [check(case, original(case)) for case in tasks]
    search = json.loads((args.run/'search_baseline_test/summary.json').read_text())
    resources = search['execution']
    result = {'scope':'successful final states of all six audited candidates; candidates correlated',
              'geometry_diversity':{k:{'outputs':counts[k], 'unique_geometry_ignoring_ids_and_square_yaw':len(v)}
                                    for k, v in signatures.items()},
              'hidden_target_rays':{'targets':len(hidden), 'frustum_fraction_min':min(framing),
                                    'frustum_fraction_mean':statistics.mean(framing),
                                    'visible_fraction_max':max(hidden), 'visible_fraction_mean':statistics.mean(hidden),
                                    'occluded_fraction_mean':statistics.mean(a-b for a, b in zip(framing, hidden))},
              'original_fixed_agent_test':{'tasks':len(tasks),'successes':sum(r['success'] for r in original_results)},
              'simple_search_test':{'metrics':search['metrics'],
                                    'runtime_cpu_seconds':sum(r['runtime_cpu_seconds'] for r in resources),
                                    'nonzero_exits':sum(r['exit_code'] != 0 for r in resources)},
              'score_changes':False, 'model_calls':0}
    save(args.out, result)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
