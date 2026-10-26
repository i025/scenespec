"""Publish paired M2/M3 evidence and costs without heldout packets or seeds."""
import argparse
import json
from pathlib import Path
import re
import shutil
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'scripts'))
from analyze_continuous_benchmark import task_packets
from scene_rsi.meta.coding import save
from summarize_native_continuous import copy_coding


def read(path):
    return json.loads(path.read_text())


def spread(values):
    return {'count': len(values), 'mean': statistics.mean(values) if values else None,
            'sample_stdev': statistics.stdev(values) if len(values) > 1 else None,
            'min': min(values) if values else None, 'max': max(values) if values else None}


def cost(folder, record, scope):
    result = read(folder / 'backend_result.json') if (folder / 'backend_result.json').exists() else {}
    requests = read(folder / 'model_calls.json')['requests'] if (folder / 'model_calls.json').exists() else []
    return {**{k: record.get(k) for k in ('initial', 'policy', 'repeat')}, 'scope': scope,
            'candidate_status': record.get('status'), 'backend_status': result.get('status'),
            'model': result.get('model'), 'service_requests': len(requests), 'usage': result.get('usage'),
            'missing_usage_requests': sum(r.get('usage') is None for r in requests),
            'coding_wall_seconds': result.get('wall_seconds'),
            'evaluation_wall_seconds_sum': sum(record.get('evaluation_wall_seconds', {}).values()),
            'resources': result.get('resources')}


def cost_group(rows):
    # A slot rejected before a service request costs zero; missing actual usage stays unknown.
    known = all(r['usage'] is not None or r['service_requests'] == 0 for r in rows)
    usage = ({k: sum((r['usage'] or {}).get(k, 0) for r in rows)
              for k in ('input_tokens', 'output_tokens', 'total_tokens')} if known else None)
    return {'slots': len(rows), 'coding_tasks_started': sum(r['backend_status'] is not None for r in rows),
            'completed': sum(r['backend_status'] == 'completed' for r in rows),
            'service_requests': sum(r['service_requests'] for r in rows), 'usage': usage,
            'missing_usage_requests': sum(r['missing_usage_requests'] for r in rows),
            'coding_wall_seconds_sum': sum(r['coding_wall_seconds'] or 0 for r in rows),
            'evaluation_wall_seconds_sum': sum(r['evaluation_wall_seconds_sum'] for r in rows)}


def changes(before, after):
    if after is None:
        return None
    result = {'preference_improvement_m': before['preference_error_m'] - after['preference_error_m'],
              'hard_success_change': after['success_rate'] - before['success_rate'],
              'visibility_change': after['visibility_mean'] - before['visibility_mean'],
              'generation_wall_seconds_change': after['generation_wall_seconds_mean'] - before['generation_wall_seconds_mean']}
    for label, metric in (('position', 'diversity_position_rms_m'), ('valid_position', 'valid_pair_diversity_m'),
                          ('rotation', 'diversity_rotation_deg')):
        result[label + '_retention'] = after[metric] / before[metric] if before[metric] else None
    return result


def publish_candidate(source, dest):
    dest.mkdir(parents=True)
    for name in ('task.json', 'record.json', 'source.py'):
        if (source / name).exists():
            shutil.copyfile(source / name, dest / name)
    if (source / 'policy').exists():
        shutil.copytree(source / 'policy', dest / 'policy')
    if (source / 'coding').exists():
        copy_coding(source / 'coding', dest / 'coding')
        if (source / 'coding/record.json').exists():
            shutil.copyfile(source / 'coding/record.json', dest / 'coding/record.json')
    if (source / 'dev').exists():
        shutil.copytree(source / 'dev', dest / 'dev')
    for split in ('dev', 'selection', 'test'):
        if (source / split / 'summary.json').exists():
            shutil.copyfile(source / split / 'summary.json', dest / (split + '_summary.json'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(); run, out = args.run, args.out
    calls, scores = read(run / 'calls.json'), read(run / 'scores.json')
    assert len(calls) == len(scores) == 8
    keys = lambda r: (r['initial'], r['policy'], r['repeat'])
    initials = sorted({r['initial'] for r in calls})
    assert len(initials) == 2
    assert {keys(r) for r in calls} == {(i, p, n) for i in initials for p in ('M2', 'M3') for n in (0, 1)}
    assert {keys(r) for r in scores} == {keys(r) for r in calls}
    assert all(r['K'] == 1 for r in calls)
    out.mkdir(parents=True, exist_ok=False)
    for name in ('calls.json', 'scores.json', 'finished.json', 'reservation.json', 'freeze.json',
                 'proposals_complete.json', 'evidence_budget.json', 'ablation_plan.json'):
        if (run / name).exists():
            shutil.copyfile(run / name, out / name)
    allowed, hidden, hidden_seeds = [], [], []
    for initial in initials:
        source, dest = run / initial, out / initial; dest.mkdir()
        cases = read(source / 'private_tasks.json')
        allowed.extend(c['task'] for c in cases['dev'])
        hidden.extend(c['task'] for split in ('selection', 'test') for c in cases[split])
        hidden_seeds.extend(s for split in ('selection', 'test') for c in cases[split]
                            for s in (c['seed'], *c['algorithm_seeds']))
        save(dest / 'dev_cases.json', [{'task': c['task'], 'algorithm_seeds': c['algorithm_seeds']} for c in cases['dev']])
        for name in ('preflight.json', 'reference_acceptance.json'):
            if (source / name).exists():
                shutil.copyfile(source / name, dest / name)
        baseline = dest / 'baseline'; baseline.mkdir()
        shutil.copytree(source / 'baseline/dev', baseline / 'dev')
        for split in ('dev', 'selection', 'test'):
            shutil.copyfile(source / 'baseline' / split / 'summary.json', baseline / (split + '_summary.json'))
    costs, trajectories = [], []
    score_map = {keys(r): r for r in scores}
    for record in calls:
        relative = Path(record['initial']) / record['policy'] / ('r' + str(record['repeat']))
        source, dest = run / relative, out / relative
        publish_candidate(source, dest)
        usage = cost(source / 'coding', record, 'formal'); costs.append(usage)
        score = score_map[keys(record)]
        effective = bool(score['selected'] and score['heldout_multiobjective_guard'])
        trajectories.append({**record, 'test': score, 'coding_cost': usage,
                             'raw_test_changes': changes(score['before'], score['raw_candidate']),
                             'selected_test_changes': changes(score['before'], score['selected_after']),
                             'complete_effective_improvement': effective,
                             'compliant_preference_improvement_m': score['selected_preference_improvement_m'] if effective else 0.})
    save(out / 'trajectories.json', trajectories)
    groups = {p: cost_group([r for r in costs if r['policy'] == p]) for p in ('M2', 'M3')}
    save(out / 'costs.json', {'tasks': costs, 'formal_groups': groups,
                            'historical_M3_creation': {'total_tokens': 77723, 'coding_wall_seconds': 131.65,
                                                       'included_in_formal_usage': False},
                            'monetary_cost': None, 'wall_sums_can_overlap': True,
                            'wall_scope': 'Per-trajectory coding and dev/selection/test evaluation wall sums. Excludes shared baselines, M3 creation and preparation; not total elapsed time.'})
    policies = {}
    for policy in ('M2', 'M3'):
        rows = [r for r in trajectories if r['policy'] == policy]
        effective = sum(r['complete_effective_improvement'] for r in rows)
        total_usage = groups[policy]['usage']
        policies[policy] = {'trajectories': len(rows),
            'candidate_completion_rate': sum(r['coding_cost']['backend_status'] == 'completed' for r in rows) / len(rows),
            'candidate_evaluation_rate': sum(r['status'] == 'evaluated' for r in rows) / len(rows),
            'selection_acceptance_rate': sum(r['selected'] for r in rows) / len(rows),
            'raw_test_multiobjective_rate': sum(r['test']['heldout_multiobjective_guard'] for r in rows) / len(rows),
            'selected_complete_effective_improvement_count': effective,
            'selected_complete_effective_improvement_rate': effective / len(rows),
            'MetaImp@1_preference_m': spread([r['test']['selected_preference_improvement_m'] for r in rows]),
            'compliant_preference_improvement_m': spread([r['compliant_preference_improvement_m'] for r in rows]),
            'tokens_per_effective_improvement': total_usage['total_tokens'] / effective if effective and total_usage else None,
            'coding_wall_seconds_per_effective_improvement': groups[policy]['coding_wall_seconds_sum'] / effective if effective else None,
            'coding_and_evaluation_wall_seconds_per_effective_improvement': (
                groups[policy]['coding_wall_seconds_sum'] + groups[policy]['evaluation_wall_seconds_sum']) / effective if effective else None}
    paired = []
    for initial in initials:
        for repeat in (0, 1):
            pair = {r['policy']: r for r in trajectories if r['initial'] == initial and r['repeat'] == repeat}
            paired.append({'initial': initial, 'repeat': repeat,
                'M3_minus_M2_selected_preference_improvement_m': pair['M3']['test']['selected_preference_improvement_m'] - pair['M2']['test']['selected_preference_improvement_m'],
                'M3_minus_M2_compliant_preference_improvement_m': pair['M3']['compliant_preference_improvement_m'] - pair['M2']['compliant_preference_improvement_m'],
                'M2_effective': pair['M2']['complete_effective_improvement'], 'M3_effective': pair['M3']['complete_effective_improvement']})
    save(out / 'summary.json', {'formal_coding_slots': 8, 'K': 1, 'model_samples_per_policy_per_initial': 2,
        'policies': policies, 'paired_trajectories': paired,
        'metric_definitions': {'MetaImp@1_preference_m': 'Original selected-version preference improvement; may include heldout constraint regressions.',
            'compliant_preference_improvement_m': 'Descriptive report metric: selected improvement if the unchanged heldout guard passes, otherwise zero. Not a changed scorer or selection rule.',
            'complete_effective_improvement': 'Selected by validation and independently passes the unchanged heldout multiobjective guard.',
            'cost_per_effective_improvement': 'All formal coding cost of a policy divided by its complete effective improvements; null if zero.',
            'coding_and_evaluation_wall_seconds_per_effective_improvement': 'Sum of per-trajectory coding and dev/selection/test evaluation wall times divided by complete effective improvements; trajectories can overlap. Excludes shared baselines, M3 creation and preparation; not total elapsed time.'},
        'sample_limit': 'Two initial generators and two model samples per policy/initial; no statistical significance claim.',
        'M4_generated': False})
    # Development-only mechanism evidence is kept separate from all formal statistics.
    if (run / 'ablation_dev').exists():
        cases = read(run / 'ablation_dev/private_tasks.json')['dev']
        allowed.extend(c['task'] for c in cases)
        dest = out / 'ablation_dev'; dest.mkdir()
        save(dest / 'dev_cases.json', [{'task': c['task'], 'algorithm_seeds': c['algorithm_seeds']} for c in cases])
        shutil.copytree(run / 'ablation_dev/baseline/dev', dest / 'baseline/dev')
    if (run / 'offline_ablation').exists():
        shutil.copytree(run / 'offline_ablation', out / 'offline_ablation')
    if (run / 'ablation_calls.json').exists():
        ablations = read(run / 'ablation_calls.json')
        assert len(ablations) <= 2
        ablation_costs = []
        for record in ablations:
            relative = Path(record['directory'])
            assert relative in (Path('ablations/full'), Path('ablations/removed'))
            publish_candidate(run / relative, out / relative)
            row = cost(run / relative / 'coding', record, 'development_ablation')
            row.update(mechanism=record['mechanism'], arm=record['arm'])
            ablation_costs.append(row)
        for name in ('ablation_calls.json', 'ablation_results.json'):
            if (run / name).exists():
                shutil.copyfile(run / name, out / name)
        save(out / 'ablation_costs.json', {'tasks': ablation_costs, 'total': cost_group(ablation_costs),
            'included_in_formal_usage': False, 'development_only': True, 'monetary_cost': None})
    files = [p for p in out.rglob('*') if p.is_file()]
    occurrences = 0
    for path in files:
        text = path.read_text()
        assert not any(str(seed) in text for seed in hidden_seeds), str(path)
        assert not re.search(r'github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|sk-(?:proj-)?[A-Za-z0-9_-]{35,}', text), str(path)
        if path.suffix == '.json':
            packets = list(task_packets(json.loads(text))); occurrences += len(packets)
            assert all(packet in allowed for packet in packets), str(path)
            assert not any(packet == private for packet in packets for private in hidden), str(path)
            assert '"reference":' not in text, str(path)
    save(out / 'privacy.json', {'files_checked': len(files), 'private_conditions': len(hidden),
        'public_dev_packet_occurrences': occurrences, 'hidden_packet_matches': 0, 'hidden_seed_text_matches': 0,
        'credential_pattern_matches': 0, 'references_published': False,
        'method': 'Direct structured and seed-text comparisons; no hashes.'})
    print(json.dumps(policies), flush=True)


if __name__ == '__main__':
    main()
