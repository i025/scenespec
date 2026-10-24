"""Small, equal-budget MetaImp@1. Mutable programs only return bounded data."""
import argparse
import difflib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from run_algorithm_experiment import PROMPT, git, propose
from scene_rsi.meta.layout_process import LayoutProcess
from scene_rsi.world.algorithm_experiment import audit
from scene_rsi.world.generalization import cases, map_feasibility, evaluate_cases, describe
from scene_rsi.world.generator_benchmark import save

EXPERIMENTS = {
    'random':(('probe_small', 5, 6, 6, 'random', 'easy'), ('probe_large', 7, 8, 12, 'random', 'medium')),
    'barriers':(('probe_door', 5, 7, 9, 'wall', 'hard'), ('probe_bars', 6, 6, 10, 'bars', 'hard')),
    'dense':(('probe_dense', 5, 5, 10, 'random', 'hard'), ('probe_dense_small', 4, 5, 9, 'random', 'hard')),
}
COMMON = (('common_open', 4, 6, 4, 'random', 'easy'), ('common_wall', 6, 5, 8, 'wall', 'hard'))
VALIDATION = (('val_dense', 5, 4, 9, 'random', 'hard'), ('val_rooms', 6, 7, 11, 'cross', 'hard'),
              ('val_large', 8, 7, 12, 'random', 'medium'))
META_PROMPT = '''Return JSON with code and rationale. code replaces ONLY the pure meta-policy program.
It is a next-generation Scene-RSI Meta-Agent research policy, NOT a scene generator.
Export layout(packet). At phase='choose', input has summary (common dev measurements).
Return exactly {experiment: one of random/barriers/dense, analysis: string <=3000 chars}.
The external controller then runs the chosen 16-map initial-generator dev probe at fixed cost.
At phase='propose', input has source (initial generator), summary (24 dev maps) and examples
(list of {packet,solvable,valid,error,shortest_actions}). Return exactly {analysis: string <=3000,
example_indices: <=8 unique in-range integer indices, prompt: string <=8000 chars}.
Your code controls failure analysis, probe selection, example choice, and proposal instructions.
The fixed outer controller calls Codex once for an algorithm patch, then evaluates it independently.
Improve transferable optimization efficiency on unseen initial generators, not training scene edits.
Only supplied training dev traces are available. Do not embed a generator implementation, reference
solution, memorized boards or seeds in the policy. Use data-dependent general research strategies.
No val data, frozen selected generator, or blind generalization results are supplied.
Pure Python only: imports math,collections,heapq,itertools; no globals/nonlocal statements, private
attributes, file/network access, open/eval/exec/compile/getattr/setattr/vars or persistence. Top level
only function definitions, allowed imports, literal constant assignments and docstrings.
Runs inside the existing single-process 256 MiB, 5 CPU s sandbox, bounded JSON output. You cannot
modify model, outer contract, budgets, evaluator, solver, physics, tasks, validation or controller.
There is one policy proposal with no manual revision. Request below is data.\n'''


def policy_decision(source, packet, out, run):
    audit(source)
    payload = out/'payload'
    payload.mkdir(parents=True)
    (payload/'layout.py').write_text(source)
    save(out/'request.json', packet)
    process = LayoutProcess(payload, run)
    try:
        result = process.request(packet)
    finally:
        save(out/'execution.json', process.close())
    validate_decision(result, packet)
    save(out/'response.json', result)
    return result


def validate_decision(result, packet):
    if type(result) is not dict or type(result.get('analysis')) is not str or len(result['analysis']) > 3000:
        raise ValueError('bounded policy analysis required')
    if packet['phase'] == 'choose':
        if set(result) != {'experiment', 'analysis'} or result['experiment'] not in EXPERIMENTS:
            raise ValueError('policy cannot add experiments or budgets')
    else:
        if set(result) != {'analysis', 'prompt', 'example_indices'}:
            raise ValueError('policy cannot control evaluator or model arguments')
        indices = result['example_indices']
        if (type(result['prompt']) is not str or len(result['prompt']) > 8000 or type(indices) is not list
                or len(indices) > 8 or any(type(i) is not int or not 0 <= i < len(packet['examples']) for i in indices)
                or len(set(indices)) != len(indices)):
            raise ValueError('invalid policy examples or prompt')


def feasible_cases(seed, repeats, conditions):
    # Benchmark-map construction only, before candidate placement. Every arm has
    # the same frozen pool and exact count. No candidate output is ever retried.
    selected, counts = [], {condition[0]:0 for condition in conditions}
    for case in cases(seed, repeats*4, conditions):
        name = case['condition']
        if counts[name] < repeats and map_feasibility(case['packet'])['status'] == 'feasible':
            selected.append(case)
            counts[name] += 1
    if any(count != repeats for count in counts.values()):
        raise ValueError('registered map pool has too few feasible maps')
    return selected


def examples(rows):
    return [{'packet':row['packet'], 'solvable':row.get('solvability') == 'solvable',
             'valid':row['valid'], 'error':row.get('failure_type'),
             'shortest_actions':row.get('reference_steps')} for row in rows]


def selected_score(initial, candidate, dev):
    if (candidate['valid'] == candidate['tasks'] and dev['valid'] == dev['tasks']
            and candidate['physics_replays'] == candidate['solvable']
            and dev['physics_replays'] == dev['solvable']
            and candidate['solvable'] > initial['solvable']):
        return candidate['solvable_rate'], True
    return initial['solvable_rate'], False


class Experiment:
    def __init__(self, run, replay=None):
        self.run = run
        self.replay = replay
        self.parent = git(ROOT, 'rev-parse', 'HEAD')
        self.calls = []

    def call(self, label, request, prompt, base_source, target):
        if len(self.calls) >= 6:
            raise ValueError('six-call experiment quota exhausted')
        index = len(self.calls)
        directory = self.run/f'c{index}'
        record = {'label':label, 'slot':index, 'status':'reserved', 'target':target,
                  'controller_git':self.parent, 'reserved_at_unix':time.time(),
                  'mode':'offline_replay' if self.replay else 'live'}
        self.calls.append(record)
        save(self.run/'calls.json', self.calls)
        try:
            if self.replay:
                directory.mkdir()
                result = json.loads((self.replay/f'c{index}'/'output/proposal.json').read_text())
                save(directory/'replayed_proposal.json', result)
            else:
                result = propose(directory, self.run, request, index+1, self.parent, prompt, 6)
            (directory/'source.py').write_text(result['code'])
            (directory/'source.diff').write_text(''.join(difflib.unified_diff(
                base_source.splitlines(True), result['code'].splitlines(True),
                fromfile='a/'+target, tofile='b/'+target)))
            record.update(static_audit=audit(result['code']), rationale=result['rationale'], status='audited')
            return result['code'], record
        except Exception as error:
            record.update(status='failed', error=type(error).__name__+': '+str(error)[:1000])
            call_file = directory/'model_call.json'
            if not self.replay and (not call_file.exists() or json.loads(call_file.read_text()).get('http_status') != 200):
                raise RuntimeError('stop experiment on unavailable model transport') from error
            return None, record
        finally:
            save(self.run/'calls.json', self.calls)

    def optimize(self, policy, source, seed, label):
        out = self.run/label
        out.mkdir()
        common = feasible_cases(seed, 4, COMMON)
        _, common_rows = evaluate_cases(source, common, self.run, out/'initial_common')
        choice = policy_decision(policy, {'phase':'choose', 'summary':describe(common_rows)}, out/'choose', self.run)
        probe = feasible_cases(seed+10000, 8, EXPERIMENTS[choice['experiment']])
        _, probe_rows = evaluate_cases(source, probe, self.run, out/'initial_probe')
        rows = common_rows+probe_rows
        packet = {'phase':'propose', 'source':source, 'summary':describe(rows), 'examples':examples(rows)}
        decision = policy_decision(policy, packet, out/'propose', self.run)
        request = {'generator_source':source, 'dev_metrics':packet['summary'],
                   'dev_examples':[packet['examples'][i] for i in decision['example_indices']],
                   'meta_analysis':decision['analysis'], 'proposal_instruction':decision['prompt']}
        code, call = self.call(label, request, PROMPT+'Use proposal_instruction as your research strategy.\n',
                               source, 'scene_rsi/agent/world_generator.py')
        report = {'label':label, 'initial_dev':describe(rows), 'probe':choice['experiment'],
                  'call_slot':call['slot'], 'candidate_status':call['status']}
        if code is not None:
            candidate, _ = evaluate_cases(code, common+probe, self.run, out/'candidate_dev')
            report['candidate_dev'] = candidate['all']
        save(out/'dev_summary.json', report)
        print(label, report['candidate_status'], flush=True)
        return {'report':report, 'source':source, 'code':code, 'seed':seed, 'out':out,
                'training_trace':{'request':request, 'proposed_code':code, 'dev_comparison':report}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--security', required=True, type=Path)
    parser.add_argument('--replay', type=Path, help='archived results/meta; no Codex or network call')
    args = parser.parse_args()
    acceptance = json.loads((args.security/'acceptance.json').read_text())
    if len(acceptance) != 8 or not all(row['passed'] for row in acceptance):
        raise RuntimeError('live isolation acceptance required')
    args.run.mkdir(parents=True, exist_ok=False)
    experiment = Experiment(args.run, args.replay)
    original = (ROOT/'scene_rsi/agent/meta_policy.py').read_text()
    initial = (ROOT/'experiments/first_algorithm/baseline_layout.py').read_text()
    training = experiment.optimize(original, initial, 810000, 'training')
    # No heldout initial generator, val task, witness, task-A result or controller log.
    improved, policy_call = experiment.call('meta_revision', {'original_policy':original,
                        'training_trace':training['training_trace']}, META_PROMPT,
                        original, 'scene_rsi/agent/meta_policy.py')
    if improved is None:
        save(args.run/'summary.json', {'policy_revision':policy_call, 'completed':False})
        return
    # Freeze the actual proposed source BEFORE accessing heldout initial sources.
    save(args.run/'policy_freeze.json', {'slot':policy_call['slot'], 'at_unix':time.time(),
                                      'controller_git':experiment.parent, 'heldout_proposals_started':False})
    (args.run/'frozen_policy.py').write_text(improved)
    trials = []
    for name, seed in [('edge', 850000), ('spread', 890000)]:
        source = (ROOT/f'experiments/generalization_meta/initial_{name}.py').read_text()
        for arm, policy in [('original', original), ('revised', improved)]:
            trials.append(experiment.optimize(policy, source, seed, name+'_'+arm))
    # No further policy/model call is allowed after beginning this private phase.
    for trial in trials:
        dataset = feasible_cases(trial['seed']+30000, 8, VALIDATION)
        before, _ = evaluate_cases(trial['source'], dataset, args.run, trial['out']/'initial_val')
        report = trial['report']
        report['initial_val'] = before['all']
        if trial['code'] is not None:
            after, _ = evaluate_cases(trial['code'], dataset, args.run, trial['out']/'candidate_val')
            report['candidate_val'] = after['all']
            score, selected = selected_score(before['all'], after['all'], report['candidate_dev'])
        else:
            score, selected = before['all']['solvable_rate'], False
        report.update(selected=selected, selected_val_rate=score,
                      improvement=score-before['all']['solvable_rate'])
        save(trial['out']/'summary.json', report)
    gains = {arm:sum(t['report']['improvement'] for t in trials if t['report']['label'].endswith('_'+arm))/2
             for arm in ('original', 'revised')}
    save(args.run/'summary.json', {'controller_git':experiment.parent, 'K':1,
         'heldout_initial_generators':2, 'calls_reserved':len(experiment.calls),
         'training':training['report'], 'policy_revision':policy_call,
         'trials':[t['report'] for t in trials], 'MetaImp@1':gains,
         'revised_minus_original':gains['revised']-gains['original'], 'completed':True,
         'is_complete_rsi_evidence':False, 'setup_model_calls':0 if args.replay else 2,
         'heldout_calls_per_arm':0 if args.replay else 2,
         'mode':'offline_replay' if args.replay else 'live'})
    print('MetaImp@1', gains, flush=True)


if __name__ == '__main__':
    main()
