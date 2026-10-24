"""Frozen M0/M1, multiple samples, K=2 then predeclared K=3 extension."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import difflib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from run_algorithm_experiment import git, propose
from run_metaimp import policy_decision
from scene_rsi.benchmark.meta_tasks import dataset, evaluate, summarize
from scene_rsi.world.algorithm_experiment import audit
from scene_rsi.world.generator_benchmark import save

FIXTURES = ('first_fit', 'wall_priority', 'local_relations')
POLICIES = {'M0':'scene_rsi/agent/meta_policy.py',
            'M1':'experiments/generalization_meta/results/meta/frozen_policy.py'}
MAX_CALLS = 36  # 3 initial generators x 2 policies x 2 independent samples x K<=3.
CONTRACT = '''Return JSON with exactly code and rationale. code is the complete pure Python
layout(packet) replacement for the supplied INDOOR FURNITURE GENERATOR. This is NOT the old
push-box placement API. Optimize a general algorithm, not individual displayed scenes.
packet contains width, depth, obstacles (fixed-object count), spec (original SceneSpec JSON),
fixed (immutable PlacedObject dicts), domains (object id -> allowed PlacedObject dicts),
start and goal (immutable integer navigation cells), and minimum_steps (hard lower bound).
Return ONLY SceneState JSON: {scene_id: spec.scene_id, objects: [PlacedObject dicts]}.
Return every required object once with the correct asset. Preserve all fixed objects exactly.
Every non-fixed object must use one of its supplied poses, including id, asset, x, y, yaw_deg.
Room sizes, assets, destination, entrance and requirements cannot change.

The original SceneVerifier is authoritative: object completeness, room bounds, collision,
ALL near_wall/in_front_of/keep_clear constraints and access_paths must pass. Coordinates
are metres, footprint centres in XY, +Z up, native forward +Y. Yaw is CCW 0/90/180/270.
For yaw 90/270 swap asset width/depth. Positive-area AABB overlap >1e-8 is a collision.
near_wall means footprint edge-to-wall gap in [0,max_gap]. in_front_of uses REFERENCE yaw:
f=(0,1),(-1,0),(0,-1),(1,0) respectively. Dot(subject-reference,f) minus both projected
half-extents must be in [0,max_gap]; lateral offset must not exceed summed lateral extents.
keep_clear forbids any object footprint overlapping the indicated rectangular region.

Additional fixed task checks: .15 m clearance at cell centres (x+.5,y+.5); a cell is blocked
if rect.xmin-.15 < x+.5 < rect.xmax+.15 AND rect.ymin-.15 < y+.5 < rect.ymax+.15 for ANY
object. From packet.start to packet.goal, original four-neighbor walk BFS must find a path
whose SHORTEST action count is >= minimum_steps. A longer supplied trajectory cannot fake
this. Both start and goal cells must remain free. PyBullet independently checks collisions.
This is static indoor layout/navigation difficulty, not a new pushing task. All tasks have
private feasible witnesses, never supplied. Do not infer a pose's correctness from ordering,
object labels or any claimed witness. Domains are shuffled, ids anonymized, rooms rotated.

Ordinary single-agent BFS and bounded finite-domain/constraint search in YOUR algorithm are
allowed and charged to its resource budget. No trusted evaluator imports, external oracle,
filesystem/network, persistent cross-task state, shell or model calls. Exactly one output
per task, no controller solvability rejection. Each task uses one fresh restricted process:
256 MiB, 5 CPU seconds, 7 seconds response deadline and bounded JSON output.
Only math,collections,heapq,itertools imports. Top level only function definitions, allowed
imports, docstrings and literal constant assignments. NO Global OR Nonlocal statements,
even for function-local closures. No private attributes or forbidden builtins such as open,
eval,exec,compile,getattr,setattr,vars,__import__. Static rejection consumes the proposal slot.

Use supplied meta_analysis/proposal_instruction as the research strategy within this contract.
Development examples include ACTUAL generated_scene and trusted dev failures, not reference
solutions. Previous attempt feedback is dev/static only. Selection and final-test tasks,
scores, controller, physics, and verifier code are inaccessible and unmodifiable. Never
hardcode displayed scene ids/poses or claim unrun tests, unseen gains or RSI.\n'''


def feedback(cases, rows):
    return [{'packet':case['packet'], 'solvable':row['success'], 'valid':row['valid'],
             'error':','.join(sorted({f['type'] for f in row['failures']})),
             'shortest_actions':row['shortest_actions'], 'generated_scene':row['scene'],
             'failures':row['failures']} for case, row in zip(cases, rows)]


def ranking(metrics):
    # Hard task success first; geometric validity only breaks an exact success tie.
    return metrics['solvable'], metrics['valid']


def selection(candidates):
    # Stable sort preserves earlier source, including baseline, on an exact tie.
    return max(candidates, key=lambda row:ranking(row['selection']['metrics']))


def saturated(trials):
    return all(selection(t['candidates'])['selection']['metrics']['solvable_rate'] == 1 for t in trials)


def distribution(values):
    return {'n':len(values), 'mean':statistics.mean(values),
            'sample_sd':statistics.stdev(values) if len(values) > 1 else None,
            'min':min(values), 'max':max(values), 'values':values}


def fetch(job, run, parent, replay):
    directory = run/f"c{job['slot']}"
    if replay:
        directory.mkdir()
        result = json.loads((replay/f"c{job['slot']}"/'output/proposal.json').read_text())
        save(directory/'replayed_proposal.json', result)
        return result
    try:
        return propose(directory, run/f"transport_{job['slot']}", job['request'], job['slot']+1,
                       parent, CONTRACT, MAX_CALLS)
    except Exception as error:
        call_file = directory/'model_call.json'
        if call_file.exists() and json.loads(call_file.read_text()).get('http_status') == 200:
            return {'error':type(error).__name__+': '+str(error)[:1000]}
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--security', type=Path, required=True)
    parser.add_argument('--replay', type=Path, help='archived calls, zero model calls')
    args = parser.parse_args()
    acceptance = json.loads((args.security/'acceptance.json').read_text())
    if len(acceptance) != 8 or not all(row['passed'] for row in acceptance):
        raise RuntimeError('live isolation acceptance required')
    run = args.run.resolve()
    run.mkdir(parents=True, exist_ok=False)
    parent = git(ROOT, 'rev-parse', 'HEAD')
    started = time.time()
    dev = [case for case, _ in dataset('dev', 13)]
    common = [c for c in dev if int(c['id'].rsplit('_', 1)[1]) < 2]
    probes = {'random':[c for c in dev if 2 <= int(c['id'].rsplit('_', 1)[1]) < 4],
              'barriers':[c for c in dev if c['tier'] != 'easy' and 4 <= int(c['id'].rsplit('_', 1)[1]) < 7],
              'dense':[c for c in dev if c['tier'] == 'hard' and 7 <= int(c['id'].rsplit('_', 1)[1]) < 13]}
    assert len(common) == 6 and all(len(pool) == 6 for pool in probes.values())
    choose_set = [case for case, _ in dataset('selection', 4)]
    # Build and check final tasks now, but never read their feedback until all calls finish.
    final_set = [case for case, _ in dataset('test', 8)]
    save(run/'private_tasks.json', {'dev':dev, 'selection':choose_set, 'test':final_set})
    baselines = {}
    trials = []
    policies = {name:(ROOT/path).read_text() for name, path in POLICIES.items()}
    for fixture in FIXTURES:
        source = (ROOT/f'experiments/meta_hard/initial_{fixture}.py').read_text()
        before, _ = evaluate(source, choose_set, run/f'baseline_{fixture}'/'selection', run)
        baselines[fixture] = {'index':0, 'source':source, 'selection':before}
        for repeat in range(2):
            # Counterbalance arm order across repetitions; samples use separate CLI sessions.
            for arm in (('M0', 'M1') if repeat == 0 else ('M1', 'M0')):
                label = f'{fixture}_{repeat}_{arm}'
                trials.append({'label':label, 'fixture':fixture, 'repeat':repeat, 'arm':arm,
                               'parent_source':source, 'candidates':[baselines[fixture]],
                               'attempts':[], 'snapshots':{}})
    calls = []
    for round_number in range(1, 4):
        if round_number == 3:
            decision = {'K2_saturated':saturated(trials),
                        'rule':'stop iff every trial selected 12/12 selection tasks', 'at_unix':time.time()}
            save(run/'extension.json', decision)
            if decision['K2_saturated']:
                break
        jobs = []
        for trial in trials:
            folder = run/trial['label']/f'r{round_number}'
            source = trial['parent_source']
            common_report, common_rows = evaluate(source, common, folder/'parent_common', run)
            choice = policy_decision(policies[trial['arm']],
                {'phase':'choose', 'summary':common_report['metrics']}, folder/'choose', run)
            probe = probes[choice['experiment']]
            _, probe_rows = evaluate(source, probe, folder/'parent_probe', run)
            cases, rows = common+probe, common_rows+probe_rows
            public = feedback(cases, rows)
            decision = policy_decision(policies[trial['arm']],
                {'phase':'propose', 'source':source, 'summary':summarize(rows), 'examples':public},
                folder/'propose', run)
            request = {'generator_source':source, 'dev_metrics':summarize(rows),
                       'dev_examples':[public[i] for i in decision['example_indices']],
                       'meta_analysis':decision['analysis'], 'proposal_instruction':decision['prompt'],
                       'previous_dev_attempts':trial['attempts']}
            slot = len(calls)
            if slot >= MAX_CALLS:
                raise RuntimeError('total proposal quota exhausted')
            record = {'slot':slot, 'trial':trial['label'], 'round':round_number, 'status':'reserved',
                      'controller_git':parent, 'reserved_at_unix':time.time(), 'probe':choice['experiment']}
            calls.append(record)
            jobs.append({'slot':slot, 'request':request, 'record':record, 'trial':trial,
                         'cases':cases, 'parent_metrics':summarize(rows), 'folder':folder})
        # All slots are reserved BEFORE any model call. Only proposal calls overlap;
        # candidate execution remains serial so child CPU measurements do not mix.
        save(run/'calls.json', calls)
        proposals = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(fetch, job, run, parent, args.replay) for job in jobs]
            for job, future in zip(jobs, futures):
                try:
                    proposals.append((job, future.result()))
                except Exception as error:
                    for pending in futures:
                        pending.cancel()
                    job['record'].update(status='transport_failed', error=str(error)[:1000])
                    save(run/'calls.json', calls)
                    raise RuntimeError('transport failure; no additional proposal round') from error
        for job, proposal in proposals:
            record, trial, folder = job['record'], job['trial'], job['folder']
            call_dir = run/f"c{job['slot']}"
            if 'error' in proposal:
                record.update(status='generation_failed', error=proposal['error'])
                trial['attempts'].append({'round':round_number, 'slot':job['slot'], 'proposal_error':proposal['error']})
                save(run/'calls.json', calls)
                continue
            source = proposal['code']
            (call_dir/'source.py').write_text(source)
            (call_dir/'source.diff').write_text(''.join(difflib.unified_diff(
                trial['parent_source'].splitlines(True), source.splitlines(True),
                fromfile='a/indoor_generator.py', tofile='b/indoor_generator.py')))
            attempt = {'round':round_number, 'slot':job['slot'], 'rationale':proposal['rationale']}
            try:
                record['static_audit'] = audit(source)
                report, _ = evaluate(source, job['cases'], folder/'candidate_dev', run)
                selected, _ = evaluate(source, choose_set, folder/'candidate_selection', run)
                record.update(status='evaluated', dev=report['metrics'], selection=selected['metrics'])
                attempt['dev'] = report['metrics']
                trial['candidates'].append({'index':round_number, 'slot':job['slot'], 'source':source,
                                            'selection':selected})
                if ranking(report['metrics']) > ranking(job['parent_metrics']):
                    trial['parent_source'] = source
            except (ValueError, SyntaxError) as error:
                record.update(status='static_rejected', error=str(error)[:1000])
                attempt['static_error'] = record['error']
            trial['attempts'].append(attempt)
            save(run/'calls.json', calls)
            print(trial['label'], round_number, record['status'],
                  record.get('selection', {}).get('solvable'), flush=True)
        if round_number >= 2:
            for trial in trials:
                trial['snapshots'][str(round_number)] = selection(trial['candidates'])['index']
            save(run/f'selection_K{round_number}.json',
                 [{'trial':t['label'], 'selected_index':t['snapshots'][str(round_number)],
                   'selection_rate':selection(t['candidates'])['selection']['metrics']['solvable_rate']} for t in trials])
    save(run/'proposals_complete.json', {'at_unix':time.time(), 'calls_reserved':len(calls),
                                        'final_feedback_exposed':False})
    # Final test phase: no policy/model invocation is possible below this point.
    for fixture, baseline in baselines.items():
        baseline['test'], _ = evaluate(baseline['source'], final_set, run/f'baseline_{fixture}'/'test', run)
    results = []
    for trial in trials:
        tested = {0:baselines[trial['fixture']]['test']}
        for budget, index in trial['snapshots'].items():
            candidate = next(c for c in trial['candidates'] if c['index'] == index)
            if index not in tested:
                tested[index], _ = evaluate(candidate['source'], final_set,
                                             run/trial['label']/f'test_c{index}', run)
            initial_rate = tested[0]['metrics']['solvable_rate']
            results.append({'trial':trial['label'], 'fixture':trial['fixture'], 'repeat':trial['repeat'],
                            'arm':trial['arm'], 'K':int(budget), 'selected_index':index,
                            'initial_test':tested[0]['metrics'], 'selected_test':tested[index]['metrics'],
                            'gain':tested[index]['metrics']['solvable_rate']-initial_rate})
    summary = {'controller_git':parent, 'calls_reserved':len(calls), 'samples_per_fixture':2,
               'results':results, 'elapsed_seconds':time.time()-started,
               'actual_model_calls':0 if args.replay else len(calls), 'scores':{}}
    for budget in sorted({r['K'] for r in results}):
        scores = {arm:distribution([r['gain'] for r in results if r['K'] == budget and r['arm'] == arm])
                  for arm in POLICIES}
        paired = []
        for fixture in FIXTURES:
            for repeat in range(2):
                pair = {r['arm']:r['gain'] for r in results
                        if r['K'] == budget and r['fixture'] == fixture and r['repeat'] == repeat}
                paired.append(pair['M1']-pair['M0'])
        scores['M1_minus_M0'] = distribution(paired)
        for arm in POLICIES:
            records = [c for c in calls if c['round'] <= budget and c['trial'].endswith('_'+arm)]
            passed = [c for c in records if c['status'] == 'evaluated']
            scores[arm]['static_passes'] = len(passed)
            scores[arm]['proposals'] = len(records)
            scores[arm]['quality_given_static_pass_selection'] = (
                distribution([c['selection']['solvable_rate'] for c in passed]) if passed else None)
        summary['scores'][str(budget)] = scores
    save(run/'summary.json', summary)
    print(json.dumps(summary['scores']), flush=True)


if __name__ == '__main__':
    main()
