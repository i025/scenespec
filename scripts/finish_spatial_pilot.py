"""Finish heldout evaluation after an explicitly logged exploratory ceiling stop.

No proposals, no changed scores or selection rule; reconstruct K=1 from its ledger.
"""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from run_algorithm_experiment import git
from run_meta_hard import selection
from scene_rsi.benchmark.spatial_tasks import evaluate
from scene_rsi.world.generator_benchmark import save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--security', type=Path, required=True)
    args = parser.parse_args()
    run = args.run
    acceptance = json.loads((args.security/'acceptance.json').read_text())
    stop = json.loads((run/'early_ceiling_stop.json').read_text())
    calls = json.loads((run/'calls.json').read_text())
    if not stop['stop'] or len(calls) != 8 or any(c['round'] != 1 for c in calls):
        raise RuntimeError('requires a closed eight-call first wave')
    if len(acceptance) != 8 or not all(r['passed'] for r in acceptance):
        raise RuntimeError('isolation acceptance required')
    if (run/'finished.json').exists():
        raise RuntimeError('pilot already finalized')
    final = json.loads((run/'private_tasks.json').read_text())['test']
    save(run/'proposals_complete.json', {'at_unix':stop['at_unix'], 'calls_reserved':8, 'test_feedback_exposed':False,
                                       'reason':'exploratory algorithm-ceiling stop; see early_ceiling_stop.json'})
    scores = []
    for fixture in ('greedy','independent'):
        source = (ROOT/f'experiments/spatial_probe/initial_{fixture}.py').read_text()
        baseline_selection = json.loads((run/f'baseline_{fixture}'/'selection/summary.json').read_text())
        before, _ = evaluate(source, final, run/f'baseline_{fixture}'/'test', run)
        for call in [c for c in calls if c['fixture'] == fixture]:
            candidates = [{'index':0, 'source':source, 'selection':baseline_selection}]
            if call['status'] == 'evaluated':
                selected = json.loads((run/call['trial']/'r1/candidate_selection/summary.json').read_text())
                candidates.append({'index':1, 'slot':call['slot'], 'source':(run/f"c{call['slot']}"/'source.py').read_text(),
                                   'selection':selected})
            winner = selection(candidates)
            if winner['index']:
                after, _ = evaluate(winner['source'], final, run/call['trial']/'test_1', run)
            else:
                after = before
            scores.append({'fixture':fixture, 'arm':call['arm'], 'K':1, 'selected_index':winner['index'],
                           'slot':winner.get('slot'), 'before':before['metrics'], 'after':after['metrics'],
                           'MetaImp':after['metrics']['solvable_rate']-before['metrics']['solvable_rate']})
    save(run/'scores.json', scores)
    save(run/'finished.json', {'wall_seconds':time.time()-(run/'private_tasks.json').stat().st_mtime,
        'proposal_controller_git':calls[0]['controller_git'], 'evaluation_controller_git':git(ROOT, 'rev-parse','HEAD'),
        'calls':8, 'M2_started':False, 'scope':'K=1 exploratory pilot; no repeated sampling per fixture/arm',
        'protocol_deviation':stop['protocol_deviation']})
    print(json.dumps(scores, indent=2))


if __name__ == '__main__':
    main()
