"""Rescore saved JSON states, with zero model calls and zero candidate execution."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scene_rsi.benchmark.spatial_tasks import check
from scene_rsi.world.generator_benchmark import save


def replay(run):
    if not (run/'finished.json').exists():
        raise RuntimeError('wait for all proposals and heldout evaluation to finish')
    tasks = json.loads((run/'private_tasks.json').read_text())
    cases = tasks['common']+sum(tasks['probes'].values(), [])+tasks['selection']+tasks['test']
    lookup = {c['id']:c for c in cases}
    checked, no_state = 0, 0
    for file in run.glob('**/rows.jsonl'):
        for line in file.read_text().splitlines():
            row = json.loads(line)
            if row['scene'] is None:
                no_state += 1
                continue
            result = check(lookup[row['id']], row['scene'])
            for key in ('valid','success','failures','shortest_actions','turns','physical_valid','visibility'):
                if result.get(key) != row.get(key):
                    raise AssertionError((str(file.relative_to(run)), row['id'], key))
            checked += 1
    return {'saved_states_rescored':checked, 'execution_failures_without_state':no_state,
            'all_saved_scores_match':True, 'model_calls':0, 'candidate_executions':0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = replay(args.run)
    save(args.out, result)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
