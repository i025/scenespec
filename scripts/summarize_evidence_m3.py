"""Publish dev/policy evidence and aggregate heldout scores; never hidden packets."""
import argparse
import json
from pathlib import Path
import re
import shutil
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'scripts'))
from summarize_native_continuous import copy_coding
from analyze_continuous_benchmark import interval, task_packets
from run_evidence_m3 import INITIALS, OLD, read
from scene_rsi.meta.coding import save


def load_rows(folder, count):
    return [read(folder / f'{i:02d}_{r}.json') for i in range(count) for r in range(4)]


def improvement(before, after):
    return interval([statistics.mean(r['result']['quality']['preference_error_m'] for r in before if r['task_index'] == i) -
                     statistics.mean(r['result']['quality']['preference_error_m'] for r in after if r['task_index'] == i)
                     for i in sorted({r['task_index'] for r in before})])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True); parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(); run, out = args.run, args.out
    out.mkdir(parents=True, exist_ok=False); costs = []
    def cost(folder, label, policy):
        result = read(folder / 'backend_result.json') if (folder / 'backend_result.json').exists() else {}
        records = read(folder / 'model_calls.json')['requests'] if (folder / 'model_calls.json').exists() else []
        costs.append({'task': label, 'policy': policy, 'status': result.get('status'), 'model': result.get('model'),
                      'service_requests': len(records), 'usage': result.get('usage'),
                      'missing_usage_requests': sum(r.get('usage') is None for r in records),
                      'coding_wall_seconds': result.get('wall_seconds'), 'resources': result.get('resources')})
    for name in ('reservation.json', 'self_task.json', 'freeze.json'):
        if (run / name).exists(): shutil.copyfile(run / name, out / name)
    shutil.copytree(run / 'parent', out / 'parent')
    if (run / 'qualification').exists(): shutil.copytree(run / 'qualification', out / 'qualification')
    creation = out / 'creation'; creation.mkdir(); copy_coding(run / 'm3/coding', creation / 'coding')
    for name in ('gate.json', 'raw_policy.py', 'frozen_policy.py'):
        if (run / 'm3' / name).exists(): shutil.copyfile(run / 'm3' / name, creation / name)
    cost(run / 'm3/coding', 'M3_creation', 'M3_creation')
    gate = read(run / 'm3/gate.json'); hidden = []; hidden_seeds = []
    allowed = [c['task'] for c in read(OLD / 'dev_cases.json')]
    comparisons = []
    folder = run / 'comparison'
    if (folder / 'finished.json').exists():
        for name in ('calls.json', 'freeze.json', 'proposals_complete.json', 'scores.json', 'finished.json'):
            shutil.copyfile(folder / name, out / ('comparison_' + name))
        calls = read(folder / 'calls.json')
        for initial in INITIALS:
            source, dest = folder / initial, out / initial; dest.mkdir()
            cases = read(source / 'private_tasks.json')
            allowed.extend(c['task'] for c in cases['dev'])
            hidden.extend(c['task'] for s in ('selection', 'test') for c in cases[s])
            hidden_seeds.extend(v for s in ('selection', 'test') for c in cases[s] for v in (c['seed'], *c['algorithm_seeds']))
            save(dest / 'dev_cases.json', [{'task': c['task'], 'algorithm_seeds': c['algorithm_seeds']} for c in cases['dev']])
            for name in ('preflight.json', 'reference_acceptance.json'):
                shutil.copyfile(source / name, dest / name)
            for arm in ('baseline', 'M2', 'M3'):
                a, b = source / arm, dest / arm; b.mkdir()
                if (a / 'dev').exists(): shutil.copytree(a / 'dev', b / 'dev')
                for split in ('dev', 'selection', 'test'):
                    if (a / split / 'summary.json').exists(): shutil.copyfile(a / split / 'summary.json', b / (split + '_summary.json'))
                if arm == 'baseline': continue
                for name in ('record.json', 'source.py', 'task.json'):
                    if (a / name).exists(): shutil.copyfile(a / name, b / name)
                if (a / 'policy').exists(): shutil.copytree(a / 'policy', b / 'policy')
                if (a / 'coding').exists(): copy_coding(a / 'coding', b / 'coding')
                cost(a / 'coding', initial + '_' + arm, arm)
            original = load_rows(source / 'baseline/test', 12); selected = []
            for arm in ('M2', 'M3'):
                record = next(c for c in calls if c['initial'] == initial and c['policy'] == arm)
                path = source / arm
                raw = load_rows(path / 'test', 12) if (path / 'test/summary.json').exists() else None
                chosen = raw if record['selected'] else original; selected.append(chosen)
                comparisons.append({'initial': initial, 'policy': arm, 'selected': record['selected'],
                                    'raw_preference_improvement': improvement(original, raw) if raw else None,
                                    'selected_preference_improvement': improvement(original, chosen)})
            comparisons.append({'initial': initial, 'selected_M3_minus_M2_preference_improvement': improvement(selected[0], selected[1]),
                                'model_samples_per_policy': 1, 'task_intervals_are_not_meta_sampling_uncertainty': True})
    groups = {}
    for policy in ('M3_creation', 'M2', 'M3'):
        tasks = [r for r in costs if r['policy'] == policy]
        groups[policy] = {'coding_tasks': len(tasks), 'completed': sum(r['status'] == 'completed' for r in tasks),
                         'service_requests': sum(r['service_requests'] for r in tasks),
                         'usage': None if any(r['usage'] is None for r in tasks) else {
                             k: sum(r['usage'][k] for r in tasks) for k in ('input_tokens', 'output_tokens', 'total_tokens')},
                         'coding_wall_seconds_sum': sum(r['coding_wall_seconds'] or 0 for r in tasks)}
    save(out / 'costs.json', {'tasks': costs, 'groups': groups, 'monetary_cost': None,
                            'coding_wall_sums_can_overlap': True, 'creation_cost_is_separate': True})
    save(out / 'contrasts.json', comparisons)
    save(out / 'summary.json', {'M3_qualified': gate['passed'], 'independent_comparison_completed': (folder / 'finished.json').exists(),
                                'qualification_executed': (run / 'qualification').exists(),
                                'policy_patch_produced': bool((run / 'm3/coding/candidate.diff').read_text()),
                                'downstream_coding_tasks': len(costs)-1,
                                'coding_tasks': len(costs), 'model_samples_per_policy_per_initial': 1 if (folder / 'finished.json').exists() else 0,
                                'M4_generated': False, 'stable_meta_capability_proven': False})
    files = [p for p in out.rglob('*') if p.is_file()]
    for path in files:
        text = path.read_text()
        assert not any(str(seed) in text for seed in hidden_seeds), str(path)
        assert not re.search(r'github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|sk-(?:proj-)?[A-Za-z0-9_-]{35,}', text), str(path)
        if path.suffix == '.json':
            packets = list(task_packets(json.loads(text)))
            assert all(t in allowed for t in packets), str(path)
            assert not any(a == b for a in packets for b in hidden), str(path)
            assert '"reference":' not in text, str(path)
    save(out / 'privacy.json', {'files_checked': len(files), 'private_conditions': len(hidden), 'hidden_packet_matches': 0,
                              'hidden_seed_text_matches': 0, 'credential_pattern_matches': 0, 'references_published': False,
                              'method': 'direct structured and text comparison, no hashes'})
    print(json.dumps(groups), flush=True)


if __name__ == '__main__':
    main()
