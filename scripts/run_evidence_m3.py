"""One evidence-driven M3 edit; qualification; two fixed M2/M3 K=1 pairs."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from itertools import combinations
import json
from pathlib import Path
import secrets
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'scripts'))
from run_continuous_meta import CONTRACT, feedback, meta_summary
from run_metaimp import validate_decision
from run_native_continuous import candidate
from run_paired_repair import BUDGET
from analyze_continuous_benchmark import distance
from scene_rsi.continuous.meta_experiment import audit, eligible, run_case, summarize
from scene_rsi.continuous.tasks import FAMILIES, make_task
from scene_rsi.continuous.geometry import MeshWorld
from scene_rsi.meta.layout_process import LayoutProcess
from scene_rsi.meta.coding import git, improvement_task, run_codex, save
from scene_rsi.world.algorithm_experiment import audit as policy_audit

EXP = ROOT / 'experiments/evidence_m3'
PARENT = ROOT / 'experiments/m2_failure/M2.py'
OLD = ROOT / 'experiments/m2_failure/results/pilot'
INITIALS = ('coordinate_walk', 'whole_restart')
MENU = {'random': [2, 3], 'dense': [3, 4], 'barriers': [4, 5]}
CONSTRAINTS = {'runtime_errors_max': 0, 'hard_success_loss_max': 0,
               'visibility_loss_max': .01, 'position_retention_min': .9,
               'valid_position_retention_min': .9, 'rotation_retention_min': .8,
               'valid_pair_count_loss_max': 0, 'preference_improvement_min_m': .01}


def read(path):
    return json.loads(path.read_text())


def history():
    old = read(ROOT / 'experiments/m2_failure/analysis/training_feedback.json')
    return old['summary']['multiobjective']


def resolve(packet, path):
    value = packet
    for key in path.split('.'):
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def check_diagnosis(result, packet, required):
    diagnostic = result.get('diagnosis')
    validate_decision({k: v for k, v in result.items() if k != 'diagnosis'}, packet)
    if diagnostic is None and not required:
        return
    if (type(diagnostic) is not dict or set(diagnostic) != {'objective', 'status', 'evidence', 'probe', 'expected_observation'}
            or diagnostic['objective'] not in (None, 'runtime', 'reliability', 'position', 'valid_position', 'rotation', 'visibility', 'preference')
            or diagnostic['status'] not in ('observed', 'hypothesis', 'unknown')
            or type(diagnostic['evidence']) is not list or len(diagnostic['evidence']) > 12
            or type(diagnostic['expected_observation']) is not str or len(diagnostic['expected_observation']) > 600):
        raise ValueError('bounded structured diagnosis required')
    probe = result['experiment'] if packet['phase'] == 'choose' else packet['selected_probe']['name']
    if diagnostic['probe'] != probe:
        raise ValueError('diagnosis probe must match executed probe')
    paths = diagnostic['evidence']
    for path in paths:
        if type(path) is not str or len(path) > 200:
            raise ValueError('bounded resolvable evidence paths required')
        resolve(packet, path)
    if diagnostic['status'] == 'observed':
        if (not any(p.startswith(('summary.current.', 'examples.')) for p in paths)
                or any(not p.startswith(('summary.current.', 'examples.', 'summary.constraints.')) for p in paths)):
            raise ValueError('observed facts must cite current evidence, not history')


def decide(source, packet, out, run, required=False):
    policy_audit(source)
    payload = out / 'payload'; payload.mkdir(parents=True, exist_ok=False)
    (payload / 'layout.py').write_text(source); save(out / 'request.json', packet)
    process = LayoutProcess(payload, run)
    try:
        result = process.request(packet)
    finally:
        save(out / 'execution.json', process.close())
    save(out / 'response.json', result)  # Invalid output is evidence too.
    check_diagnosis(result, packet, required)
    return result


def summary4(cases, rows):
    """Same frozen distance/quality definitions, expanded to six seed pairs."""
    pairs = []
    for a, b in combinations(range(4), 2):
        pair_rows = [r for i in range(len(cases)) for r in rows if r['task_index'] == i and r['repeat'] in (a, b)]
        pairs.append(summarize(cases, pair_rows))
    result = dict(pairs[0])
    result.update(tasks=len(cases), runs=len(rows), successes=sum(r['result']['success'] for r in rows),
                  success_rate=statistics.mean(r['result']['success'] for r in rows),
                  preference_error_m=statistics.mean(r['result']['quality']['preference_error_m'] for r in rows),
                  visibility_mean=statistics.mean(r['result']['quality']['visibility_mean'] for r in rows),
                  diversity_position_rms_m=statistics.mean(p['diversity_position_rms_m'] for p in pairs),
                  diversity_rotation_deg=statistics.mean(p['diversity_rotation_deg'] for p in pairs),
                  valid_pair_count=sum(p['valid_pair_count'] for p in pairs),
                  failures=dict(Counter(f['type'] for r in rows for f in r['result']['failures'])),
                  generation_wall_seconds_mean=statistics.mean(r['generation_wall_seconds'] for r in rows),
                  controller_cpu_seconds_total=sum(r['controller_cpu_seconds'] for r in rows),
                  runtime_cpu_seconds_total=sum((r['execution'] or {}).get('runtime_cpu_seconds', 0) for r in rows),
                  runtime_errors=sum(r['error'] is not None for r in rows),
                  queries_min=min(r['queries'] for r in rows), queries_max=max(r['queries'] for r in rows))
    result['valid_pair_diversity_m'] = (sum(p['valid_pair_diversity_m'] * p['valid_pair_count'] for p in pairs) /
                                       result['valid_pair_count']) if result['valid_pair_count'] else 0.
    return result


def evaluate4(source, cases, out, run, geometry):
    audit_result = audit(source); out.mkdir(parents=True, exist_ok=False)
    payload = out / 'payload'; payload.mkdir(); (payload / 'layout.py').write_text(source)
    save(out / 'static_audit.json', audit_result)
    rows = []
    for i, case in enumerate(cases):
        for repeat, seed in enumerate(case['algorithm_seeds']):
            row = run_case(payload, case['task'], seed, run, geometry)
            row.update(task_index=i, repeat=repeat); save(out / f'{i:02d}_{repeat}.json', row); rows.append(row)
    summary = summary4(cases, rows); save(out / 'summary.json', summary)
    return summary, rows


def examples4(cases, rows):
    examples = feedback(cases, rows)
    for i, case in enumerate(cases):
        group = [r for r in rows if r['task_index'] == i]
        deltas = [distance(case['task'], a['state'], b['state']) for a, b in combinations(group, 2) if a['state'] and b['state']]
        metrics = {'position_rms_m': statistics.mean(d['position_rms_m'] for d in deltas) if deltas else 0.,
                   'rotation_mean_deg': statistics.mean(d['rotation_mean_deg'] for d in deltas) if deltas else 0.,
                   'preference_error_m_mean': statistics.mean(r['result']['quality']['preference_error_m'] for r in group),
                   'all_hard_success': all(r['result']['success'] for r in group)}
        for row in group:
            examples[rows.index(row)].update(condition_index=i, repeat=row['repeat'], pair_metrics=metrics,
                                              origin='common' if i < 2 else 'probe')
    return examples


def delivered_pairs(examples):
    return sorted((r['condition_index'], r['repeat']) for r in examples)


def check_delivery(examples, selected, probe, required):
    actual = delivered_pairs(selected)
    groups = {i: {repeat for j, repeat in actual if j == i} for i, _ in actual}
    complete = [i for i in probe['condition_indices'] if groups.get(i) == {0, 1, 2, 3}]
    if required and (not complete or any(repeats != {0, 1, 2, 3} for repeats in groups.values())):
        raise ValueError('policy did not select complete four-seed groups including its probe; no controller repair')
    return {'available': delivered_pairs(examples), 'selected': actual, 'delivered': actual,
            'complete_probe_conditions': complete, 'controller_added_examples': 0}


def request_for(source, cases, rows, policy, out, run, required=False):
    public = examples4(cases, rows); full = summary4(cases, rows)
    common = [r for r in public if r['condition_index'] < 2]
    summary = {'current': {'baseline': full, 'common': meta_summary(common), 'seeds_per_condition': 4},
               'history': history(), 'constraints': CONSTRAINTS}
    choice = decide(policy, {'phase': 'choose', 'summary': summary, 'probe_menu': MENU}, out / 'choose', run, required)
    probe = {'name': choice['experiment'], 'condition_indices': MENU[choice['experiment']], 'repeats': [0, 1, 2, 3]}
    examples = common + [r for r in public if r['condition_index'] in probe['condition_indices']]
    packet = {'phase': 'propose', 'source': source, 'summary': summary, 'examples': examples, 'selected_probe': probe}
    decision = decide(policy, packet, out / 'propose', run, required)
    selected = [examples[i] for i in decision['example_indices']]
    delivery = check_delivery(examples, selected, probe, required)
    contract = CONTRACT.replace('assets, two seeds, budgets', 'assets, four shared seeds, budgets')
    task = improvement_task({'generator_source': source, 'meta_analysis': decision['analysis'],
                             'proposal_instruction': decision['prompt'], 'dev_metrics': summary, 'dev_examples': selected}, contract)
    task['budget'] = dict(BUDGET)
    task['feedback'].update(diagnosis=decision.get('diagnosis'), constraints=CONSTRAINTS, selected_probe=probe, delivery=delivery)
    save(out / 'delivery.json', delivery)
    return task, choice


def fixture_packets(objective, old_risk):
    baseline = {'tasks': 6, 'runs': 24, 'runtime_errors': 0, 'successes': 24, 'success_rate': 1.,
                'preference_error_m': .4, 'visibility_mean': .98, 'diversity_position_rms_m': .6,
                'diversity_rotation_deg': 80., 'valid_pair_count': 36, 'valid_pair_diversity_m': .6}
    after = dict(baseline, preference_error_m=.2, diversity_position_rms_m=.58, valid_pair_diversity_m=.58, diversity_rotation_deg=72.)
    if objective == 'position': after.update(diversity_position_rms_m=.45, valid_pair_diversity_m=.45)
    elif objective == 'rotation': after['diversity_rotation_deg'] = 40.
    elif objective == 'visibility': after['visibility_mean'] = .95
    elif objective == 'preference': after['preference_error_m'] = .395
    elif objective == 'reliability': after.update(successes=20, success_rate=20/24, valid_pair_count=24)
    elif objective == 'runtime': after['runtime_errors'] = 1
    current = {'baseline': baseline, 'candidate': after, 'seeds_per_condition': 4}
    if objective == 'unknown': current.pop('candidate'); baseline['preference_error_m'] = 0.
    summary = {'current': current, 'history': {'position_retention_mean': .4 if old_risk == 'position' else 1.,
                                              'rotation_retention_mean': .4 if old_risk == 'rotation' else 1.},
               'constraints': CONSTRAINTS}
    return {'phase': 'choose', 'summary': summary, 'probe_menu': MENU}


def comparative_sources(diagnostic):
    metrics = {'runtime': ('runtime_errors',), 'reliability': ('successes', 'success_rate', 'valid_pair_count'),
               'position': ('diversity_position_rms_m',), 'valid_position': ('valid_pair_diversity_m',),
               'rotation': ('diversity_rotation_deg',), 'visibility': ('visibility_mean',),
               'preference': ('preference_error_m',)}[diagnostic['objective']]
    sides = ('candidate',) if diagnostic['objective'] == 'runtime' else ('baseline', 'candidate')
    return all(any('summary.current.' + side + '.' + metric in diagnostic['evidence'] for metric in metrics) for side in sides)


def qualify(source, out, run):
    out.mkdir(); records = []; errors = []
    for objective in ('position', 'rotation', 'visibility', 'preference', 'reliability', 'runtime', 'unknown'):
        for old_risk in ('none', 'position', 'rotation'):
            label = objective + '_' + old_risk
            try:
                packet = fixture_packets(objective, old_risk)
                choice = decide(source, packet, out / label / 'choose', run, True)
                probe = {'name': choice['experiment'], 'condition_indices': MENU[choice['experiment']], 'repeats': [0, 1, 2, 3]}
                examples = [{'condition_index': i, 'repeat': r, 'origin': 'common' if i < 2 else 'probe',
                    'packet': {'spec': {'room': {'width': 6.+i*.1, 'depth': 6.}}}, 'generated': None,
                    'valid': True, 'solvable': True, 'error': None, 'quality': {'preference_error_m': .2, 'visibility_mean': .98},
                    'pair_metrics': {'position_rms_m': .2+i*.08, 'rotation_mean_deg': 20.+i*10,
                                     'preference_error_m_mean': .2+i*.03, 'all_hard_success': True}}
                    for i in [0, 1] + probe['condition_indices'] for r in range(4)]
                propose_packet = dict(packet, phase='propose', source='def layout(packet): return {}', examples=examples, selected_probe=probe)
                decision = decide(source, propose_packet, out / label / 'propose', run, True)
                selected = [examples[i] for i in decision['example_indices']]
                delivery = check_delivery(examples, selected, probe, True)
                for response in (choice, decision):
                    diagnostic = response['diagnosis']; found = diagnostic['objective']
                    if objective == 'unknown':
                        if diagnostic['status'] == 'observed':
                            raise ValueError('history promoted into observed current comparative failure')
                    elif diagnostic['status'] != 'observed' or (found != objective and not (objective == 'position' and found == 'valid_position')):
                        raise ValueError('current sole violated objective was not diagnosed')
                    elif not comparative_sources(diagnostic):
                        raise ValueError('observed diagnosis needs objective-relevant current metric evidence')
                facts = [(r['diagnosis']['objective'], r['diagnosis']['status']) for r in (choice, decision)]
                records.append({'objective': objective, 'history': old_risk, 'facts': facts,
                                'probe': choice['experiment'], 'delivery': delivery})
            except Exception as error:
                errors.append({'fixture': label, 'error': type(error).__name__ + ': ' + str(error)[:600]})
    for objective in ('position', 'rotation', 'visibility', 'preference', 'reliability', 'runtime'):
        group = [r for r in records if r['objective'] == objective]
        if len(group) != 3 or len({json.dumps(r['facts']) for r in group}) != 1:
            errors.append({'fixture': objective, 'error': 'current observed facts changed with history or incomplete checks'})
    neutral = [r for r in records if r['history'] == 'none' and r['objective'] != 'unknown']
    substantive = len({(r['probe'], tuple(map(tuple, r['delivery']['delivered']))) for r in neutral}) > 1
    if not substantive: errors.append({'error': 'no nontext probe or actual delivered-membership response to current violations'})
    gate = {'passed': not errors, 'fixtures': 21, 'completed': len(records), 'policy_executions_max': 42,
            'substantive_decisions': substantive, 'records': records, 'errors': errors, 'capability_improvement_proven': False}
    save(out / 'gate.json', gate)
    return gate


def create(run, backend):
    run.mkdir(parents=True, exist_ok=False); run.chmod(0o700)
    save(run / 'reservation.json', {'policy_edits': 1, 'downstream_tasks_max': 4,
                                   'coding_tasks_max': 5, 'K': 1, 'budget': BUDGET})
    parent = PARENT.read_text(); cases = read(OLD / 'dev_cases.json')
    rows = [read(OLD / 'c2/dev' / f'{i:02d}_{r}.json') for i in range(8) for r in range(2)]
    public = examples4(cases, rows)
    summary = {'current': {'baseline': read(OLD / 'baseline_group/dev/summary.json'),
                           'candidate': read(OLD / 'c2/dev/summary.json'), 'seeds_per_condition': 2},
               'history': history(), 'constraints': CONSTRAINTS}
    menu = {'random': [4, 5], 'dense': [5, 6], 'barriers': [5, 7]}
    choice = decide(parent, {'phase': 'choose', 'summary': summary, 'probe_menu': menu}, run / 'parent/choose', run)
    probe = {'name': choice['experiment'], 'condition_indices': menu[choice['experiment']], 'repeats': [0, 1]}
    examples = [r for r in public if r['condition_index'] < 4 or r['condition_index'] in probe['condition_indices']]
    decision = decide(parent, {'phase': 'propose', 'summary': summary, 'source': parent,
                              'examples': examples, 'selected_probe': probe}, run / 'parent/propose', run)
    ablation = read(ROOT / 'experiments/m2_attribution/results/analysis.json')
    paths = read(ROOT / 'experiments/m2_attribution/path_evidence.json')
    task = {'objective': 'Produce M3 by modifying M2 evidence diagnosis, probe delivery and multiobjective research logic.',
            'analysis': decision['analysis'], 'strategy': decision['prompt'],
            'feedback': {'metrics': summary, 'examples': [examples[i] for i in decision['example_indices']],
              'dev_ablations': {k: ablation[k] for k in ('summaries', 'paired_change_from_full', 'trace_diagnostics', 'limitations')},
              'executed_paths': {'historical_deficits': paths['historical_deficits_not_current_measurements'],
                  'policies': {name: {k: record[k] for k in ('choice', 'choose_summary', 'probe_pairs_available', 'selected_pairs', 'probe_pairs_delivered')}
                               for name, record in paths['policies'].items()}}},
            'allowed_files': ['algorithm.py'], 'files': {'algorithm.py': parent},
            'contract': (EXP / 'META_CONTRACT.md').read_text(), 'budget': dict(BUDGET)}
    save(run / 'self_task.json', task); gate = {'passed': False}
    try:
        result, source = candidate(task, run / 'm3', backend)
        if source is None or result.get('usage') is None or result['usage']['total_tokens'] > BUDGET['total_tokens']:
            raise ValueError('no complete budget-compliant policy edit')
        policy_audit(source); (run / 'm3/raw_policy.py').write_text(source)
        gate = qualify(source, run / 'qualification', run)
        if gate['passed']:
            (run / 'm3/frozen_policy.py').write_text(source)
            (EXP / 'M3.py').write_text(source)
            save(run / 'freeze.json', {'candidate_git': result['candidate_git'],
                                       'new_conditions_generated': False, 'independent_capability_proven': False})
    except Exception as error:
        gate['passed'] = False; gate['error'] = type(error).__name__ + ': ' + str(error)[:1000]
    save(run / 'm3/gate.json', gate)
    print('M3 actual program qualification', gate['passed'], flush=True)


def conditions(folder, geometry):
    rng = secrets.SystemRandom(); cases = {}; checks = []
    for split, count in (('dev', 6), ('selection', 6), ('test', 12)):
        cases[split] = []
        for i in range(count):
            seed = rng.getrandbits(64)
            task, reference = make_task(seed, FAMILIES[i % 4], folder.name + '_' + split + '_' + str(i))
            world = MeshWorld(task, geometry)
            try:
                result = world.evaluate(reference, final=True)
            finally:
                world.close()
            checks.append({'split': split, 'success': result['success']})
            cases[split].append({'seed': seed, 'task': task, 'reference': reference,
                                 'algorithm_seeds': [rng.getrandbits(32) for _ in range(4)]})
    save(folder / 'private_tasks.json', cases); save(folder / 'reference_acceptance.json', checks)
    if not all(c['success'] for c in checks):
        raise RuntimeError('illegal reference; no resampling')
    return cases


def prepare(run, geometry):
    if not read(run / 'm3/gate.json')['passed']:
        raise RuntimeError('unqualified M3; no new conditions or downstream calls')
    if (EXP / 'M3.py').read_text() != (run / 'm3/frozen_policy.py').read_text():
        raise RuntimeError('frozen M3 changed')
    folder = run / 'comparison'; folder.mkdir(exist_ok=False)
    save(folder / 'freeze.json', {'project_git': git(ROOT, 'rev-parse', 'HEAD'), 'M3_frozen_before_tasks': True})
    calls = []
    for initial in INITIALS:
        dest = folder / initial; dest.mkdir()
        cases = conditions(dest, geometry)
        source = (EXP / ('initial_' + initial + '.py')).read_text()
        baseline = {}
        for split in ('dev', 'selection'):
            baseline[split], rows = evaluate4(source, cases[split], dest / 'baseline' / split, run, geometry)
            if split == 'dev': dev_rows = rows
        dev = baseline['dev']
        feasible = (dev['runtime_errors'] == 0 and dev['success_rate'] >= .5 and
                    dev['preference_error_m'] >= .02 and dev['diversity_position_rms_m'] > .05)
        save(dest / 'preflight.json', {'passed': feasible, 'baseline_dev': dev})
        if not feasible:
            raise RuntimeError('initial feasibility failed; no tuning or downstream calls')
        for name in ('M2', 'M3'):
            candidate_dir = dest / name; candidate_dir.mkdir()
            record = {'initial': initial, 'policy': name, 'K': 1, 'status': 'reserved', 'selected': False}
            try:
                policy = PARENT.read_text() if name == 'M2' else (run / 'm3/frozen_policy.py').read_text()
                task, choice = request_for(source, cases['dev'], dev_rows, policy, candidate_dir / 'policy', run, name == 'M3')
                save(candidate_dir / 'task.json', task); record['probe'] = choice['experiment']
            except Exception as error:
                record['policy_error'] = type(error).__name__ + ': ' + str(error)[:1000]
            calls.append(record)
    save(folder / 'calls.json', calls)
    print('four K=1 tasks reserved on two new generators', flush=True)


def execute(run, geometry, backend):
    folder = run / 'comparison'; calls = read(folder / 'calls.json')
    if (folder / 'started.json').exists(): raise RuntimeError('no implicit restart or extra proposals')
    if [(c['initial'], c['policy'], c['status']) for c in calls] != [(i, m, 'reserved') for i in INITIALS for m in ('M2', 'M3')]:
        raise ValueError('exactly four preregistered K=1 slots')
    started = time.perf_counter(); save(folder / 'started.json', {'coding_tasks_max': 4, 'K': 1})
    def propose(record):
        dest = folder / record['initial'] / record['policy']
        if record.get('policy_error'): return None, record['policy_error']
        try:
            result, source = candidate(read(dest / 'task.json'), dest, backend)
            if source is None or result.get('usage') is None or result['usage']['total_tokens'] > BUDGET['total_tokens']:
                return None, result['status'] + ': missing complete budget-compliant patch'
            return source, None
        except Exception as error:
            return None, type(error).__name__ + ': ' + str(error)[:1000]
    with ThreadPoolExecutor(max_workers=2) as pool:
        proposals = list(pool.map(propose, calls))
    for record, (source, error) in zip(calls, proposals):
        dest = folder / record['initial'] / record['policy']
        if source is None:
            record.update(status='candidate_failed', error=error)
        else:
            (dest / 'source.py').write_text(source)
            cases = read(dest.parent / 'private_tasks.json')
            for split in ('dev', 'selection'):
                record[split], _ = evaluate4(source, cases[split], dest / split, run, geometry)
            record.update(status='evaluated', static_audit=True,
                          selected=eligible(read(dest.parent / 'baseline/selection/summary.json'), record['selection']))
        save(dest / 'record.json', record); save(folder / 'calls.json', calls)
        print(record['initial'], record['policy'], record['status'], 'selected', record['selected'], flush=True)
    save(folder / 'proposals_complete.json', {'all_proposals_closed': True, 'selection_fixed': True, 'test_feedback_exposed': False})
    scores = []
    for initial in INITIALS:
        dest = folder / initial; cases = read(dest / 'private_tasks.json')
        before, _ = evaluate4((EXP / ('initial_' + initial + '.py')).read_text(), cases['test'], dest / 'baseline/test', run, geometry)
        for record in [c for c in calls if c['initial'] == initial]:
            path = dest / record['policy']; raw = None
            if record['status'] == 'evaluated':
                raw, _ = evaluate4((path / 'source.py').read_text(), cases['test'], path / 'test', run, geometry)
            score = {'initial': initial, 'policy': record['policy'], 'selected': record['selected'],
                     'before': before, 'raw_candidate': raw, 'selected_after': raw if record['selected'] else before,
                     'heldout_multiobjective_guard': eligible(before, raw) if raw else False}
            score['selected_preference_improvement_m'] = before['preference_error_m'] - score['selected_after']['preference_error_m']
            scores.append(score)
    save(folder / 'scores.json', scores)
    save(folder / 'finished.json', {'wall_seconds': time.perf_counter()-started, 'K': 1, 'extra_trials': 0,
                                    'MetaImp@1_preference_m': {m: statistics.mean(s['selected_preference_improvement_m'] for s in scores if s['policy'] == m) for m in ('M2', 'M3')}})
    print('two independent comparisons complete; no M4 or extra samples', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('create', 'prepare', 'execute'), required=True)
    for key in ('run', 'geometry', 'guard', 'security', 'editor-security'):
        parser.add_argument('--' + key, type=Path, required=True)
    args = parser.parse_args()
    safety = read(args.security / 'acceptance.json'); editor = read(args.editor_security / 'acceptance.json')
    if len(safety) != 8 or not all(r['passed'] for r in safety) or not editor['native_patch_applied']:
        raise RuntimeError('existing execution and editor isolation acceptance required')
    backend = partial(run_codex, runtime=args.run.parent / 'runtime', guard=args.guard, parent_git=git(ROOT, 'rev-parse', 'HEAD'))
    if args.phase == 'create': create(args.run, backend)
    elif args.phase == 'prepare': prepare(args.run, args.geometry)
    else: execute(args.run, args.geometry, backend)


if __name__ == '__main__':
    main()
