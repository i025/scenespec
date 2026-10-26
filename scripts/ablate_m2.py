"""Fixed public DEV interventions; original scoring and isolated execution only."""
import argparse
import difflib
import json
import math
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from analyze_continuous_benchmark import distance, interval
from analyze_m2_failures import best_indices
from check_layout_isolation import CASES
from scene_rsi.continuous.baselines import state
from scene_rsi.continuous.geometry import MeshWorld
from scene_rsi.continuous.meta_experiment import audit, eligible, evaluate, summarize
from scene_rsi.world.generator_benchmark import save

OLD = ROOT / 'experiments/m2_failure/results/pilot'
VARIANTS = ('group_restart', 'full', 'no_initial_yaw', 'no_projection',
            'no_restarts', 'one_projection_pass', 'no_initial_yaw_no_projection')


def read(path):
    return json.loads(path.read_text())


def replace(source, before, after, count=1):
    if source.count(before) != count:
        raise ValueError('frozen intervention anchor changed: ' + before)
    return source.replace(before, after)


def variant_source(name):
    if name not in VARIANTS:
        raise ValueError(name)
    if name == 'group_restart':
        return (ROOT / 'experiments/m2_failure/initial.py').read_text()
    source = (OLD / 'c2/source.py').read_text()
    if name in ('no_initial_yaw', 'no_initial_yaw_no_projection'):
        # Keep these two random draws; change only the heuristic initial yaw.
        source = replace(source, 'rng.uniform(-math.pi,math.pi)] if heuristic',
                         '0.*rng.uniform(-math.pi,math.pi)] if heuristic', 2)
    if name in ('no_projection', 'no_initial_yaw_no_projection'):
        source = replace(source, '        refine_relations(task, values, rng)\n',
                         '        refine_relations(task, {n:list(v) for n,v in values.items()}, rng)\n')
    if name == 'no_restarts':
        source = replace(source, 'values[name]=fresh[name]', 'unused = fresh[name]')
        source = replace(source, "if name in task['zones']:clamp_floor(task,values,name)",
                         "if False:clamp_floor(task,values,name)")
    if name == 'one_projection_pass':
        source = replace(source, 'range(6)', 'range(1)')
    return source


def task_metrics(cases, rows):
    result = []
    for i, case in enumerate(cases):
        pair = sorted((r for r in rows if r['task_index'] == i), key=lambda r: r['repeat'])
        a, b = pair
        delta = distance(case['task'], a['state'], b['state']) if a['state'] and b['state'] else None
        result.append({'task_index': i,
                       'preference_error_m': statistics.mean(r['result']['quality']['preference_error_m'] for r in pair),
                       'visibility_mean': statistics.mean(r['result']['quality']['visibility_mean'] for r in pair),
                       'success_rate': statistics.mean(r['result']['success'] for r in pair),
                       'position_m': delta['position_rms_m'] if delta else 0.,
                       'rotation_deg': delta['rotation_mean_deg'] if delta else 0.,
                       'generation_seconds': statistics.mean(r['generation_wall_seconds'] for r in pair)})
    return result


def paired_difference(before, after):
    return {key: interval([b[key] - a[key] for a, b in zip(before, after)])
            for key in before[0] if key != 'task_index'}


def final_selector(trace, method):
    valid = [i for i, row in enumerate(trace) if not row['failures']]
    if not valid:
        return best_indices(trace)[-1]
    return valid[0] if method == 'first_query_valid' else valid[-1]


def rescore_selector(cases, rows, method, out, geometry):
    """Same proposals and incumbent history; independently score a different final index."""
    out.mkdir()
    rescored = []
    for row in rows:
        task = cases[row['task_index']]['task']
        index = final_selector(row['trace'], method)
        output = state(task, row['trace'][index]['values'])
        started = time.perf_counter()
        world = MeshWorld(task, geometry)
        try:
            result = world.evaluate(output, final=True)
        finally:
            world.close()
        seconds = time.perf_counter() - started
        changed = dict(row, state=output, result=result, selected_query=index,
                       extra_final_score_seconds=seconds)
        save(out / f'{row["task_index"]:02d}_{row["repeat"]}.json',
             {k: changed[k] for k in ('task_index', 'repeat', 'state', 'result', 'selected_query', 'extra_final_score_seconds')})
        rescored.append(changed)
    summary = summarize(cases, rescored)
    summary.update(extra_final_evaluations=len(rows),
                   extra_final_score_seconds_total=sum(r['extra_final_score_seconds'] for r in rescored),
                   resource_scope='generation costs reused from full; only extra final scoring is additional')
    save(out / 'summary.json', summary)
    return summary, rescored


def trace_diagnostics(cases, rows):
    proposal, incumbent = [], []
    steps, indices, rotation_initial = [], [], []
    for i, case in enumerate(cases):
        pair = [r for r in rows if r['task_index'] == i]
        traces = [r['trace'] for r in pair]
        winners = [best_indices(t) for t in traces]
        states = [[state(case['task'], q['values']) for q in t] for t in traces]
        for q in range(24):
            proposal.append(distance(case['task'], states[0][q], states[1][q])['position_rms_m'])
            incumbent.append(distance(case['task'], states[0][winners[0][q]], states[1][winners[1][q]])['position_rms_m'])
        for t, w in zip(traces, winners):
            indices.append(w[-1])
            rotation_initial.extend(v[2] for v in t[0]['values'].values())
            steps.extend(math.dist(t[q]['values'][name][:2], t[q-1]['values'][name][:2])
                         for q in range(2, 24, 2) for name in case['task']['zones'])
    return {'proposal_position_mean_m': statistics.mean(proposal),
            'incumbent_position_mean_m': statistics.mean(incumbent),
            'selected_queries': indices, 'selected_even_queries': sum(q > 0 and q % 2 == 0 for q in indices),
            'even_floor_update_max_m': max(steps), 'even_floor_update_mean_m': statistics.mean(steps),
            'initial_yaw_abs_mean_rad': statistics.mean(abs(v) for v in rotation_initial)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--geometry', type=Path, required=True)
    parser.add_argument('--security', type=Path, required=True)
    args = parser.parse_args()
    safety = read(args.security / 'acceptance.json')
    if {r['case'] for r in safety} != set(CASES) or not all(r['passed'] for r in safety):
        raise RuntimeError('all original isolation checks must pass; no host fallback')
    cases = read(OLD / 'dev_cases.json')
    args.out.mkdir(parents=True, exist_ok=False)
    save(args.out / 'inputs.json', {'scope': 'published DEV only', 'model_calls': 0,
                                  'dev_cases': cases, 'variants': list(VARIANTS), 'queries_per_generation': 24})
    summaries, metrics, diagnostics, rows_by_name = {}, {}, {}, {}
    started = time.perf_counter()
    for name in VARIANTS:
        source = variant_source(name)
        audit_result = audit(source)
        summary, rows = evaluate(source, cases, args.out / name, args.runtime, args.geometry)
        if summary['runtime_errors'] or summary['queries_min'] != 24 or summary['queries_max'] != 24:
            raise RuntimeError('incomplete isolated execution: ' + name)
        summaries[name], metrics[name], rows_by_name[name] = summary, task_metrics(cases, rows), rows
        save(args.out / name / 'static_audit.json', audit_result)
        diagnostics[name] = trace_diagnostics(cases, rows)
        patch = ''.join(difflib.unified_diff((OLD / 'c2/source.py').read_text().splitlines(True),
                                            source.splitlines(True), fromfile='frozen_M2_generator.py', tofile=name + '.py'))
        (args.out / name / 'intervention.diff').write_text(patch)
        print(name, json.dumps(summary), flush=True)
    exact = {name: all(row['state'] == read(OLD / folder / 'dev' / f'{row["task_index"]:02d}_{row["repeat"]}.json')['state']
                       for row in rows_by_name[name]) for name, folder in (('full', 'c2'), ('group_restart', 'baseline_group'))}
    if not all(exact.values()):
        raise RuntimeError('frozen DEV replay changed')
    for method in ('first_query_valid', 'last_query_valid'):
        name = 'full_' + method
        summary, rows = rescore_selector(cases, rows_by_name['full'], method, args.out / name, args.geometry)
        summaries[name], metrics[name] = summary, task_metrics(cases, rows)
    comparisons = {name: paired_difference(metrics['full'], value) for name, value in metrics.items() if name != 'full'}
    interaction = {key: interval([both[key] - yaw[key] - projection[key] + full[key]
                                 for full, yaw, projection, both in zip(metrics['full'], metrics['no_initial_yaw'],
                                                                       metrics['no_projection'], metrics['no_initial_yaw_no_projection'])])
                   for key in metrics['full'][0] if key != 'task_index'}
    base = summaries['group_restart']
    save(args.out / 'analysis.json', {'scope': 'DEV only; no model calls or selection/deployment',
          'frozen_replay_exact_states': exact, 'summaries': summaries, 'per_task': metrics,
          'paired_change_from_full': comparisons, 'yaw_projection_interaction': interaction,
          'trace_diagnostics': diagnostics,
          'descriptive_original_dev_eligible': {n: eligible(base, s) for n, s in summaries.items()},
          'candidate_generations': sum(len(r) for r in rows_by_name.values()),
          'generation_queries': 24 * sum(len(r) for r in rows_by_name.values()),
          'extra_selector_final_scores': 2 * len(rows_by_name['full']),
          'wall_seconds_including_final_scores': time.perf_counter() - started,
          'limitations': ['8 DEV conditions, original two seeds; not a new heldout trial',
                         'cluster intervals describe tasks, not model sampling; no multiplicity claims',
                         'shadow proposals preserve some RNG work, so no speedup inference',
                         'final selector replay does not change adaptive incumbent history',
                         'RSS uses existing controller-lifetime child high-water semantics']})


if __name__ == '__main__':
    main()
