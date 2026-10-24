"""Re-score saved SceneStates with the frozen checker; no model/candidate execution."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scene_rsi.benchmark.meta_tasks import check
from scene_rsi.world.generator_benchmark import save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True, help='private completed run directory')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    tasks = json.loads((args.run/'private_tasks.json').read_text())
    by_id = {case['id']:case for group in tasks.values() for case in group}
    compared, execution_failures = 0, 0
    for path in args.run.rglob('rows.jsonl'):
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if row['scene'] is None:
                assert row['success'] is False
                execution_failures += 1
                continue
            actual = check(by_id[row['id']], row['scene'])
            for field in ('valid', 'success', 'failures', 'shortest_actions', 'turns', 'physical_valid'):
                if actual[field] != row[field]:
                    raise AssertionError((str(path), row['id'], field))
            compared += 1
    save(args.out, {'saved_scene_scores_identical':True, 'scene_states_compared':compared,
                   'execution_failure_rows_without_state':execution_failures,
                   'model_calls':0, 'candidate_code_executed':False,
                   'note':'Checks deterministic scoring of recorded outputs, not stochastic generation replay.'})
    print(compared, 'saved states re-scored; no model or candidate code executed')


if __name__ == '__main__':
    main()
