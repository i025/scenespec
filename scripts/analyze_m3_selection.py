"""Post-hoc selection reliability from saved scores only; no model or candidate runs."""
import argparse
from collections import Counter
from datetime import datetime, timezone
from itertools import product
import json
from pathlib import Path
import re
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'scripts'))
from analyze_continuous_benchmark import task_packets
from run_evidence_m3 import CONSTRAINTS, summary4
from scene_rsi.continuous.meta_experiment import eligible
from scene_rsi.world.generator_benchmark import save

METRICS = ('runtime_errors', 'successes', 'visibility_mean', 'diversity_position_rms_m',
           'diversity_rotation_deg', 'valid_pair_count', 'valid_pair_diversity_m', 'preference_error_m')


def read(path):
    return json.loads(path.read_text())


def compare(before, after):
    return {'preference_improvement_m': before['preference_error_m'] - after['preference_error_m'],
            'position_retention': after['diversity_position_rms_m'] / before['diversity_position_rms_m'],
            'valid_position_retention': after['valid_pair_diversity_m'] / before['valid_pair_diversity_m'],
            'rotation_retention': after['diversity_rotation_deg'] / before['diversity_rotation_deg'],
            'visibility_change': after['visibility_mean'] - before['visibility_mean'],
            'hard_success_count_change': after['successes'] - before['successes'],
            'valid_pair_count_change': after['valid_pair_count'] - before['valid_pair_count']}


def checks(before, after):
    # Same inequalities and units as the frozen eligible(); larger margin is better.
    c = CONSTRAINTS
    definitions = (
        ('runtime', c['runtime_errors_max'] - after['runtime_errors'], 'count'),
        ('hard_success', after['successes'] - before['successes'], 'count'),
        ('visibility', after['visibility_mean'] - (before['visibility_mean'] - c['visibility_loss_max']), 'fraction'),
        ('position', after['diversity_position_rms_m'] - c['position_retention_min'] * before['diversity_position_rms_m'], 'm'),
        ('rotation', after['diversity_rotation_deg'] - c['rotation_retention_min'] * before['diversity_rotation_deg'], 'deg'),
        ('valid_pairs', after['valid_pair_count'] - before['valid_pair_count'], 'count'),
        ('valid_position', after['valid_pair_diversity_m'] - c['valid_position_retention_min'] * before['valid_pair_diversity_m'], 'm'),
        ('preference', before['preference_error_m'] - c['preference_improvement_min_m'] - after['preference_error_m'], 'm'))
    result = {name: {'margin': float(margin), 'unit': unit, 'passed': bool(margin >= 0)}
              for name, margin, unit in definitions}
    assert all(r['passed'] for r in result.values()) == eligible(before, after)
    return result


def aggregate(groups, indices):
    """Sufficient statistics for complete four-seed groups, including valid-pair weighting."""
    result = {}
    for key in METRICS:
        if key == 'valid_pair_diversity_m':
            count = np.array([g['valid_pair_count'] for g in groups])[indices]
            numerator = (np.array([g[key] for g in groups])[indices] * count).sum(axis=1)
            result[key] = np.divide(numerator, count.sum(axis=1), out=np.zeros(len(indices)), where=count.sum(axis=1) > 0)
        else:
            values = np.array([g[key] for g in groups])[indices]
            result[key] = values.sum(axis=1) if key in ('runtime_errors', 'successes', 'valid_pair_count') else values.mean(axis=1)
    return result


def scalar(arrays, index):
    return {key: float(value[index]) for key, value in arrays.items()}


def bootstrap(before, after):
    n = len(before)
    indices = np.array(list(product(range(n), repeat=n)))
    a, b = aggregate(before, indices), aggregate(after, indices)
    # Acceptance is evaluated by the original trusted function, not a replacement rule.
    accepted = np.array([eligible(scalar(a, i), scalar(b, i)) for i in range(len(indices))])
    values = compare(a, b)
    intervals = {key: {'percentile_95': np.quantile(value, [.025, .975]).tolist(),
                       'bootstrap_mean': float(value.mean())} for key, value in values.items()}
    gates = [checks(scalar(a, i), scalar(b, i)) for i in range(len(indices))]
    blocked = {key: np.array([not row[key]['passed'] for row in gates]) for key in gates[0]}
    failed_count = sum(blocked.values())
    return {'method': 'Exhaustive ordinary paired condition-cluster bootstrap; all ordered draws equally weighted',
            'conditions_per_draw': n, 'seeds_per_condition_kept_together': 4,
            'resamples': len(indices), 'monte_carlo_sampling': False,
            'accept_count': int(accepted.sum()), 'accept_frequency': float(accepted.mean()),
            'intervals': intervals,
            'failure_frequency': {k: float(v.mean()) for k, v in blocked.items()},
            'sole_blocker_frequency': {k: float((v & (failed_count == 1)).mean()) for k, v in blocked.items()},
            'diagnostic_acceptance_ignoring_rule': {k: float((failed_count == v.astype(int)).mean()) for k, v in blocked.items()},
            'diagnostic_acceptance_ignoring_position_pair': float((failed_count == blocked['position'].astype(int) + blocked['valid_position'].astype(int)).mean()),
            'counterfactual_rule_omissions_are_not_selection_changes': True}


def load_split(folder, cases):
    rows = [read(folder / f'{i:02d}_{r}.json') for i in range(len(cases)) for r in range(4)]
    # All saved candidates here completed. Missing-state runs need different pair denominators.
    assert all(row['state'] is not None and row['error'] is None for row in rows)
    full = summary4(cases, rows)
    recorded = read(folder / 'summary.json')
    assert full.keys() == recorded.keys()
    for key in full:
        if isinstance(full[key], (int, float)):
            assert abs(full[key] - recorded[key]) < 1e-10, (folder, key)
        else:
            assert full[key] == recorded[key], (folder, key)
    groups = []
    for i, case in enumerate(cases):
        group = [dict(row, task_index=0) for row in rows if row['task_index'] == i]
        assert {r['repeat'] for r in group} == set(range(4))
        groups.append(summary4([case], group))
    rebuilt = scalar(aggregate(groups, np.arange(len(cases))[None, :]), 0)
    assert all(abs(rebuilt[k] - full[k]) < 1e-10 for k in METRICS)
    families = {}
    for family in sorted({c['task']['family'] for c in cases}):
        selected = [g for g, c in zip(groups, cases) if c['task']['family'] == family]
        metrics = scalar(aggregate(selected, np.arange(len(selected))[None, :]), 0)
        families[family] = {'conditions': len(selected), **metrics}
    return full, groups, families


def split_comparison(before, after):
    rules = checks(before, after)
    failed = [key for key, row in rules.items() if not row['passed']]
    return {'baseline': before, 'candidate': after, 'changes': compare(before, after),
            'checks': rules, 'rejected_by': failed, 'accepted': eligible(before, after),
            'sole_blocking_metric': failed[0] if len(failed) == 1 else None,
            'only_position_distribution_blocked': bool(failed) and set(failed) <= {'position', 'valid_position'}}


def distribution(cases):
    result = {'conditions': len(cases), 'runs': len(cases) * 4,
              'family_counts': dict(Counter(c['task']['family'] for c in cases))}
    for label, values in (('room_width_m', [c['task']['spec']['room']['width'] for c in cases]),
                          ('room_depth_m', [c['task']['spec']['room']['depth'] for c in cases])):
        result[label] = {'mean': float(np.mean(values)), 'sample_stdev': float(np.std(values, ddof=1))}
    return result


def family_shift(before, after):
    families = sorted(before)
    ns, nt = sum(x['conditions'] for x in before.values()), sum(x['conditions'] for x in after.values())
    gains = lambda d, f: d[f]['preference_improvement_m']
    composition = sum((after[f]['conditions'] / nt - before[f]['conditions'] / ns) * gains(before, f) for f in families)
    within = sum(after[f]['conditions'] / nt * (gains(after, f) - gains(before, f)) for f in families)
    return {'selection_family_means': before, 'test_family_means': after,
            'selection_gain_under_equal_family_weights_m': float(np.mean([gains(before, f) for f in families])),
            'test_gain_under_equal_family_weights_m': float(np.mean([gains(after, f) for f in families])),
            'test_minus_selection_gain_composition_component_m': composition,
            'test_minus_selection_gain_within_family_component_m': within,
            'decomposition_reference': 'Selection family effects; descriptive identity, not causal attribution'}


def utc(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec='milliseconds')


def service_analysis(run, calls):
    tasks, requests = [], []
    for slot, call in enumerate(calls):
        path = Path(call['initial']) / call['policy'] / f'r{call["repeat"]}' / 'coding'
        folder = run / path
        result, model = read(folder / 'backend_result.json'), read(folder / 'model_calls.json')
        end = (folder / 'backend_result.json').stat().st_mtime
        start = end - result['wall_seconds']
        task = {k: call[k] for k in ('initial', 'policy', 'repeat')}
        task.update(submission_slot=slot, status=result['status'], usage=result['usage'],
                    coding_wall_seconds=result['wall_seconds'],
                    estimated_backend_start_utc=utc(start), recorded_result_file_mtime_utc=utc(end),
                    backend_interval_estimate_seconds=[start, end],
                    model_requests=len(model['requests']), source=str(path))
        for row in model['requests']:
            response = folder / f'response_{row["index"]}.sse'
            text = response.read_text(); error = None; created_at = None
            if row.get('http_status') == 429:
                error = json.loads(text)['error']
            else:
                for line in text.splitlines():
                    if line.startswith('data: ') and line[6:] != '[DONE]':
                        event = json.loads(line[6:])
                        if event.get('type') == 'response.created':
                            created_at = event['response'].get('created_at')
                            break
            finished = response.stat().st_mtime
            requests.append({**{k: task[k] for k in ('submission_slot', 'initial', 'policy', 'repeat')},
                'request_index': row['index'], 'http_status': row.get('http_status'), 'status': row['status'],
                'wall_seconds': row['wall_seconds'],
                'usage': ({k: row['usage'][k] for k in ('input_tokens', 'output_tokens', 'total_tokens')
                           if k in row['usage']} if row['usage'] is not None else None), 'error': error,
                'response_created_at_utc_if_reported': utc(created_at) if created_at is not None else None,
                'estimated_request_start_utc': utc(finished - row['wall_seconds']),
                'response_file_mtime_utc': utc(finished), 'response_mtime_seconds': finished,
                'source': str(path / response.name)})
        cli_record = read(folder / 'cli.json'); cli = cli_record['argv']
        events = [json.loads(line) for line in (folder / 'events.jsonl').read_text().splitlines()]
        task['cli_exit_code'] = cli_record.get('exit_code')
        task['cli_error_messages'] = [event['message'] for event in events if event['type'] == 'error']
        task['cli_event_types'] = [event['type'] for event in events]
        task['cli_request_and_stream_retries_disabled'] = all(any(setting in arg for arg in cli) for setting in ('request_max_retries=0', 'stream_max_retries=0'))
        tasks.append(task)
    failed = [r for r in requests if r['http_status'] == 429]
    failed.sort(key=lambda r: r['response_mtime_seconds'])
    for failure in failed:
        start, end = tasks[failure['submission_slot']]['backend_interval_estimate_seconds']
        failure['estimated_overlapping_coding_slots'] = [task['submission_slot'] for task in tasks
            if task['submission_slot'] != failure['submission_slot'] and
            max(start, task['backend_interval_estimate_seconds'][0]) < min(end, task['backend_interval_estimate_seconds'][1])]
    groups = {}
    for policy in ('M2', 'M3'):
        rows = [r for r in requests if r['policy'] == policy]
        groups[policy] = {'service_requests': len(rows), 'unknown_usage_requests': sum(r['usage'] is None for r in rows),
                         'known_token_lower_bound': sum((r['usage'] or {}).get('total_tokens', 0) for r in rows),
                         'exact_total_tokens': None if any(r['usage'] is None for r in rows) else sum(r['usage']['total_tokens'] for r in rows)}
    return {'tasks': tasks, 'requests': requests, 'capacity_failures': failed, 'costs': groups,
            'capacity_response_span_seconds_from_file_mtimes': failed[-1]['response_mtime_seconds'] - failed[0]['response_mtime_seconds'],
            'configured_max_workers': 2,
            'timing_caveat': 'No request UTC timestamps in model_calls or error events. Times use original response/result file mtimes and elapsed durations; inferred intervals are approximate, not server arrival times. Successful response.created times are separately reported.',
            'cause_caveat': 'Temporal clustering and client concurrency do not establish that local concurrency caused upstream capacity errors.',
            'new_model_calls': 0, 'failed_slots_retried': False}


def analyze(run, out):
    out.mkdir(parents=True, exist_ok=False)
    calls, scores = read(run / 'calls.json'), read(run / 'scores.json')
    baselines, cases_by_initial, before_groups = {}, {}, {}
    hidden, hidden_seeds = [], []
    for initial in sorted({c['initial'] for c in calls}):
        cases = read(run / initial / 'private_tasks.json')
        cases_by_initial[initial] = cases
        baselines[initial] = {}
        for split in ('selection', 'test'):
            full, groups, families = load_split(run / initial / 'baseline' / split, cases[split])
            before_groups[initial, split] = groups
            baselines[initial][split] = {'distribution': distribution(cases[split]), 'metrics': full, 'by_family': families}
            hidden.extend(c['task'] for c in cases[split])
            hidden_seeds.extend(s for c in cases[split] for s in (c['seed'], *c['algorithm_seeds']))
    results = []
    for call in calls:
        if call['status'] != 'evaluated':
            continue
        initial, policy, repeat = (call[k] for k in ('initial', 'policy', 'repeat'))
        item = {'initial': initial, 'policy': policy, 'repeat': repeat}; per_family = {}
        for split in ('selection', 'test'):
            cases = cases_by_initial[initial][split]
            after, groups, families = load_split(run / initial / policy / f'r{repeat}' / split, cases)
            before = baselines[initial][split]['metrics']
            item[split] = split_comparison(before, after)
            per_family[split] = {f: {'conditions': d['conditions'], **compare(baselines[initial][split]['by_family'][f], d)} for f, d in families.items()}
            if split == 'selection':
                item['selection_bootstrap'] = bootstrap(before_groups[initial, split], groups)
        assert item['selection']['accepted'] == call['selected']
        old = next(s for s in scores if all(s[k] == item[k] for k in ('initial', 'policy', 'repeat')))
        assert item['test']['accepted'] == old['heldout_multiobjective_guard']
        item['selection_test_disagreement'] = item['selection']['accepted'] != item['test']['accepted']
        item['family_shift'] = family_shift(per_family['selection'], per_family['test'])
        results.append(item)
    save(out / 'selection_analysis.json', {'scope': 'Post-hoc only; original selection and private-test usage unchanged',
        'new_model_calls': 0, 'candidate_executions': 0, 'thresholds': CONSTRAINTS,
        'score_audit': {'saved_summary_recomputations': 14, 'all_summary_fields_match': True,
                        'four_seed_condition_aggregation_matches': True, 'original_decisions_match': True,
                        'geometry_or_physics_rescoring_performed': False},
        'baselines': baselines, 'candidates': results,
        'limitations': 'Six observed selection conditions; bootstrap frequencies are sensitivity under their empirical distribution, not probabilities of true validity. Singleton families cannot estimate within-family uncertainty. Test diagnoses cannot be reused for selection or policy training.'})
    save(out / 'service_analysis.json', service_analysis(run, calls))
    # Publish aggregate statistics only. No conditions, seed indices, poses, witnesses or raw rows.
    files = list(out.glob('*.json'))
    for path in files:
        text = path.read_text(); value = json.loads(text)
        assert not list(task_packets(value)), path
        assert not any(str(seed) in text for seed in hidden_seeds), path
        assert not re.search(r'github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|sk-(?:proj-)?[A-Za-z0-9_-]{35,}', text), path
    save(out / 'privacy.json', {'files_checked': len(files), 'private_conditions_not_published': len(hidden),
         'hidden_task_packets': 0, 'hidden_seed_text_matches': 0, 'credential_pattern_matches': 0,
         'raw_rows_or_reference_states_published': False, 'method': 'Direct structured/text comparisons; no hashes'})
    print(json.dumps([{'initial': r['initial'], 'policy': r['policy'], 'repeat': r['repeat'],
                      'bootstrap_accept_frequency': r['selection_bootstrap']['accept_frequency']} for r in results]), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    analyze(args.run, args.out)
