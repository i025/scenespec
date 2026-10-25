"""Four frozen-policy arms; at most two proposals each, with a ceiling stop."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import difflib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from run_algorithm_experiment import git, propose
from run_metaimp import policy_decision
from run_meta_hard import feedback, ranking, selection
from scene_rsi.benchmark.spatial_tasks import dataset, evaluate, summarize
from scene_rsi.world.algorithm_experiment import audit
from scene_rsi.world.generator_benchmark import save

POLICIES = {'M0':'scene_rsi/agent/meta_policy.py',
            'M1':'experiments/generalization_meta/results/meta/frozen_policy.py'}
# (unchanged proposal strategy, unchanged choice strategy). Only choice is swapped.
ARMS = {'M0':('M0', 'M0'), 'M0_M1probe':('M0', 'M1'),
        'M1_random':('M1', 'M0'), 'M1':('M1', 'M1')}
FIXTURES = ('greedy', 'independent')
MAX_CALLS = 16
CONTRACT = '''Return structured JSON with exactly code and rationale. code is a complete pure
Python layout(packet) replacement for the supplied general 3D indoor layout generator.
Optimize the algorithm across tasks, not individual shown scenes. Input is public task DATA:
spec is the original SceneSpec (metres, XY footprint centres, Z up, yaw CCW 0/90/180/270).
fixed is immutable poses {id,asset,x,y,yaw_deg,z}; domains maps every movable id to allowed
poses with those same fields. requirements maps id to {support,height:[min_z,max_z],room}.
rooms maps room id to [xmin,ymin,xmax,ymax]. support is either 'floor' or another object's id.
distances are {a,b,min,max}: Euclidean 3D asset-centre distances between named objects.
view is a fixed square pinhole camera {position,target,fov} in degrees; world up is +Z.
visibility maps named objects to [minimum,maximum] visible fraction from that view.
start/goal are immutable integer navigation cells. width/depth are room dimensions;
obstacles is the fixed object count; visual_assets only maps to existing display meshes.

Return exactly {state:{scene_id:spec.scene_id,objects:[{id,asset,x,y,yaw_deg},...]},
elevations:{id:z,...}}. One scene only. Every required object exactly once, correct asset;
one elevation per object. Preserve fixed poses exactly and choose each movable pose from
its domain. Ordering does NOT identify a correct pose. Object ids are anonymous. Do not
modify the task, dimensions, assets, references or any constraint. Never omit difficult objects.

Trusted checks OUTSIDE your process: original ground-plane SceneVerifier, plus 3D AABB
room/ceiling bounds and zero positive-volume overlap (1e-7 tolerance), PyBullet collision
checks, room assignment containing the full footprint, and all required height intervals.
Support requires bottom z = support top z, with full XY footprint inside support by .05m
on each edge. Floor support means z=0. Distances use true 3D bounding-box centres.
Visibility uses 27 rays to the target envelope at fractional coordinates .15,.5,.85 on
each axis; target is excluded as occluder; all other solid asset envelopes occlude. A
sample counts only inside the square pinhole frustum and with no segment/AABB hit before
it. This is proxy geometry, not a VLM or exact textured-mesh visibility. Room boundaries
are cut away; internal dividers/screens are fixed objects. Navigation reuses four-neighbor
BFS on a .5m grid at (.5+.5*i,.5+.5*j), .2m horizontal radius and 1.8m headroom:
input start/goal retain metre-cell centres (x+.5,y+.5). A grid point is blocked iff an object's z
interval intersects (0,1.8) and its footprint inflated .2m strictly contains the cell centre.
Both endpoints and a route must stay free. All checks are HARD, no diagnostic-score tradeoff.

You may use bounded constraint search/BFS inside your algorithm. No trusted oracle, output
rejection controller or reference scene is available. Exactly one new restricted process
per task: 256MiB, 5 CPU seconds, 7s response deadline, bounded JSON output. No persistence,
files/network, subprocesses or model calls. Only math,collections,heapq,itertools imports.
Top level: functions, allowed imports, docstrings or literal constants only. No Global or
Nonlocal statements even in nested closures, no private attributes, open/eval/exec/compile/
getattr/setattr/vars/__import__. Static rejection consumes a slot; no manual correction.

Use the supplied frozen meta_analysis/proposal_instruction as the research strategy.
Examples contain ACTUAL generated scenes and dev failures, NEVER private feasible witnesses.
Only dev data is supplied. Selection/test data, checker, physics and controller are private
and fixed. Do not hardcode shown scene ids/poses or claim unrun gains, unseen tests or RSI.
'''


def pools():
    dev = [c for c, _ in dataset('dev', 6)]
    common = [c for c in dev if c['id'].endswith('_0') and
              (c['tier'] != 'hard' if c['family'] == 'support' else
               c['tier'] != 'easy' if c['family'] == 'rooms' else c['tier'] != 'medium')]
    probes = {
        'random':[c for c in dev if c['id'].endswith('_1') and c['tier'] != 'medium'],
        'barriers':[c for c in dev if c['id'].endswith('_2') and c['family'] != 'support'],
        'dense':[c for c in dev if c['tier'] == 'hard' and c['id'].rsplit('_', 1)[1] in ('3', '4')]}
    assert len(common) == 6 and all(len(v) == 6 for v in probes.values())
    return common, probes, [c for c, _ in dataset('selection', 2)], [c for c, _ in dataset('test', 3)]


def ceiling(trials):
    return all(selection(t['candidates'])['selection']['metrics']['solvable_rate'] >= .9 for t in trials)


def fetch(job, run, parent):
    try:
        return propose(run/f"c{job['slot']}", run/f"t{job['slot']}", job['request'],
                       job['slot']+1, parent, CONTRACT, MAX_CALLS)
    except Exception as error:
        path = run/f"c{job['slot']}"/'model_call.json'
        if path.exists() and json.loads(path.read_text()).get('http_status') == 200:
            return {'error':type(error).__name__+': '+str(error)[:800]}
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--security', type=Path, required=True)
    parser.add_argument('--calibration', type=Path, required=True)
    parser.add_argument('--pilot-only', action='store_true', help='reproduce a fresh eight-call K=1 pilot, no K=2 preparation')
    args = parser.parse_args()
    security = json.loads((args.security/'acceptance.json').read_text())
    gate = json.loads((args.calibration/'gate.json').read_text())
    if len(security) != 8 or not all(r['passed'] for r in security) or not gate['model_pilot_allowed']:
        raise RuntimeError('isolation and pre-model calibration gates must pass')
    run = args.run.resolve()
    run.mkdir(parents=True, exist_ok=False)
    parent = git(ROOT, 'rev-parse', 'HEAD')
    common, probes, choose, final = pools()
    save(run/'private_tasks.json', {'common':common, 'probes':probes, 'selection':choose, 'test':final})
    policies = {k:(ROOT/v).read_text() for k, v in POLICIES.items()}
    trials, baseline = [], {}
    started = time.perf_counter()
    for fixture in FIXTURES:
        source = (ROOT/f'experiments/spatial_probe/initial_{fixture}.py').read_text()
        report, _ = evaluate(source, choose, run/f'baseline_{fixture}'/'selection', run)
        baseline[fixture] = {'index':0, 'source':source, 'selection':report}
        order = list(ARMS) if fixture == 'greedy' else list(reversed(ARMS))
        for arm in order:
            trials.append({'label':fixture+'_'+arm, 'fixture':fixture, 'arm':arm,
                           'parent_source':source, 'attempts':[], 'candidates':[baseline[fixture]], 'snapshots':{}})
    calls = []
    for round_number in ((1,) if args.pilot_only else (1, 2)):
        if round_number == 2:
            stop = ceiling(trials)
            save(run/'ceiling_stop.json', {'stop':stop, 'rule':'all eight selected selection rates >= .90 after K=1',
                                          'at_unix':time.time()})
            if stop:
                break
        jobs = []
        for trial in trials:
            folder = run/trial['label']/f'r{round_number}'
            source = trial['parent_source']
            report, rows = evaluate(source, common, folder/'parent_common', run)
            proposal_policy, choice_policy = ARMS[trial['arm']]
            choice = policy_decision(policies[choice_policy], {'phase':'choose', 'summary':report['metrics']}, folder/'choose', run)
            probe = probes[choice['experiment']]
            _, probe_rows = evaluate(source, probe, folder/'parent_probe', run)
            cases, rows = common+probe, rows+probe_rows
            public = feedback(cases, rows)
            decision = policy_decision(policies[proposal_policy],
                {'phase':'propose', 'source':source, 'summary':summarize(rows), 'examples':public}, folder/'propose', run)
            request = {'generator_source':source, 'dev_metrics':summarize(rows),
                       'dev_examples':[public[i] for i in decision['example_indices']],
                       'meta_analysis':decision['analysis'], 'proposal_instruction':decision['prompt'],
                       'previous_dev_attempts':trial['attempts']}
            slot = len(calls)
            if slot >= MAX_CALLS:
                raise RuntimeError('proposal quota exhausted')
            record = {'slot':slot, 'trial':trial['label'], 'fixture':trial['fixture'], 'arm':trial['arm'],
                      'round':round_number, 'probe':choice['experiment'], 'status':'reserved',
                      'controller_git':parent, 'reserved_at_unix':time.time()}
            calls.append(record)
            jobs.append({'slot':slot, 'record':record, 'request':request, 'trial':trial, 'folder':folder,
                         'cases':cases, 'parent_metrics':summarize(rows)})
        save(run/'calls.json', calls)  # All reservations precede upstream requests.
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(fetch, job, run, parent) for job in jobs]
            proposals = []
            for job, future in zip(jobs, futures):
                try:
                    proposals.append((job, future.result()))
                except Exception as error:
                    job['record'].update(status='transport_failed', error=str(error)[:800])
                    save(run/'calls.json', calls)
                    for pending in futures:
                        pending.cancel()
                    raise
        # Models have all finished; candidate executions are serial for CPU accounting.
        for job, proposal in proposals:
            record, trial = job['record'], job['trial']
            if 'error' in proposal:
                record.update(status='generation_failed', error=proposal['error'])
                trial['attempts'].append({'round':round_number, 'proposal_error':proposal['error']})
            else:
                directory = run/f"c{job['slot']}"
                source = proposal['code']
                (directory/'source.py').write_text(source)
                (directory/'source.diff').write_text(''.join(difflib.unified_diff(
                    trial['parent_source'].splitlines(True), source.splitlines(True),
                    fromfile='a/spatial_generator.py', tofile='b/spatial_generator.py')))
                attempt = {'round':round_number, 'rationale':proposal['rationale']}
                try:
                    record['static_audit'] = audit(source)
                    dev, _ = evaluate(source, job['cases'], job['folder']/'candidate_dev', run)
                    selected, _ = evaluate(source, choose, job['folder']/'candidate_selection', run)
                    record.update(status='evaluated', dev=dev['metrics'], selection=selected['metrics'])
                    attempt['dev'] = dev['metrics']
                    trial['candidates'].append({'index':round_number, 'slot':job['slot'], 'source':source, 'selection':selected})
                    if ranking(dev['metrics']) > ranking(job['parent_metrics']):
                        trial['parent_source'] = source
                except (ValueError, SyntaxError) as error:
                    record.update(status='static_rejected', error=str(error)[:800])
                    attempt['static_error'] = record['error']
                trial['attempts'].append(attempt)
            save(run/'calls.json', calls)
            print(trial['label'], round_number, record['status'], record.get('selection', {}).get('solvable'), flush=True)
        for trial in trials:
            trial['snapshots'][str(round_number)] = selection(trial['candidates'])['index']
        save(run/f'selection_K{round_number}.json',
             [{'trial':t['label'], 'selected_index':t['snapshots'][str(round_number)],
               'selection':selection(t['candidates'])['selection']['metrics']} for t in trials])
    if args.pilot_only:
        save(run/'ceiling_stop.json', {'stop':True, 'rule':'explicit --pilot-only budget; no K=2 requested',
                                      'at_unix':time.time()})
    save(run/'proposals_complete.json', {'at_unix':time.time(), 'calls_reserved':len(calls), 'test_feedback_exposed':False})
    scores = []
    for fixture in FIXTURES:
        before, _ = evaluate(baseline[fixture]['source'], final, run/f'baseline_{fixture}'/'test', run)
        for trial in [t for t in trials if t['fixture'] == fixture]:
            cached = {0:before}
            for k, index in trial['snapshots'].items():
                candidate = next(c for c in trial['candidates'] if c['index'] == index)
                if index not in cached:
                    cached[index], _ = evaluate(candidate['source'], final, run/trial['label']/f'test_{index}', run)
                after = cached[index]
                scores.append({'fixture':fixture, 'arm':trial['arm'], 'K':int(k), 'selected_index':index,
                               'slot':candidate.get('slot'), 'before':before['metrics'], 'after':after['metrics'],
                               'MetaImp':after['metrics']['solvable_rate']-before['metrics']['solvable_rate']})
    save(run/'scores.json', scores)
    save(run/'finished.json', {'wall_seconds':time.perf_counter()-started, 'controller_git':parent,
                              'calls':len(calls), 'M2_started':False, 'scope':'pilot: one sample per initial generator and arm'})


if __name__ == '__main__':
    main()
