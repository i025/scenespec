"""Offline, isolated M3 mechanism ablations; never calls a coding model."""
import argparse
from copy import deepcopy
import difflib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'scripts'))
from run_continuous_meta import CONTRACT, meta_summary
from run_evidence_m3 import (BUDGET, CONSTRAINTS, MENU, check_delivery, check_diagnosis, examples4,
                             fixture_packets, history, read, summary4)
from scene_rsi.meta.coding import improvement_task, save
from scene_rsi.meta.layout_process import LayoutProcess
from scene_rsi.world.algorithm_experiment import audit

SOURCE = ROOT / 'experiments/evidence_m3/M3.py'
MECHANISMS = ('history_separation', 'probe_delivery', 'constraint_margins')


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError('frozen mechanism anchor changed')
    return source.replace(old, new, 1)


def ablate(source, mechanism):
    """One explicit mechanism removal; no edits to the frozen source file."""
    if mechanism == 'history_separation':
        source = replace_once(source, 'objective, status, evidence = _current_diagnosis(packet)',
                              'objective, status, evidence = _pooled_diagnosis(packet)')
        source += '''

def _pooled_diagnosis(packet):
    # Ablation: the existing legacy diagnosis pools current and historical risks.
    original = _current_diagnosis(packet)
    summary = packet.get('summary', {})
    objective, risks, notes = _diagnose(summary, packet.get('examples', []))
    if objective is None:
        return original
    current_objective, risks, notes = _diagnose(summary.get('current', {}), packet.get('examples', []))
    if objective == current_objective:
        return original if original[0] == objective else (objective, 'observed', ['summary.current'])
    return objective, 'observed', ['summary.history']
'''
    elif mechanism == 'probe_delivery':
        # Same complete groups and eight-example budget, without probe priority.
        source = replace_once(source, 'first = probe_groups[0]', 'first = groups[0]')
    elif mechanism == 'constraint_margins':
        # Remove numerical tolerance guidance; keep directional comparisons.
        old = """('visibility', 'visibility_mean', 0.01),
            ('position', 'diversity_position_rms_m', 0.9),
            ('valid_position', 'valid_pair_diversity_m', 0.9),
            ('rotation', 'diversity_rotation_deg', 0.8),
            ('preference', 'preference_error_m', 0.01),"""
        new = """('visibility', 'visibility_mean', 0.0),
            ('position', 'diversity_position_rms_m', 1.0),
            ('valid_position', 'valid_pair_diversity_m', 1.0),
            ('rotation', 'diversity_rotation_deg', 1.0),
            ('preference', 'preference_error_m', 0.0),"""
        source = replace_once(source, old, new)
        sentence = ('Frozen joint acceptance requires zero runtime errors, no hard-success count loss, '
                    'visibility loss <=0.01, ALL-pair and VALID-pair positional retention >=90%, '
                    'rotation retention >=80%, no valid-pair count loss and preference improvement >=0.01m. ')
        source = replace_once(source, sentence, '')
    else:
        raise ValueError('unknown mechanism')
    audit(source)
    return source


def execute(source, packet, out, run):
    """Retain invalid output as evidence, without relaxing the original validator."""
    audit(source)
    payload = out / 'payload'; payload.mkdir(parents=True, exist_ok=False)
    (payload / 'layout.py').write_text(source)
    save(out / 'request.json', packet)
    process = LayoutProcess(payload, run)
    try:
        result = process.request(packet)
    finally:
        save(out / 'execution.json', process.close())
    save(out / 'response.json', result)
    error = None
    try:
        check_diagnosis(result, packet, True)
    except (ValueError, KeyError, TypeError) as exc:
        error = type(exc).__name__ + ': ' + str(exc)
    save(out / 'validation.json', {'passed': error is None, 'error': error})
    return result, error


def probe_examples(examples, probe):
    # Both policies receive the same available common/probe pool for that probe.
    return [deepcopy(row) for row in examples
            if row['condition_index'] < 2 or row['condition_index'] in probe['condition_indices']]


def exercise(source, summary, examples, generator, out, run):
    choice, choose_error = execute(source, {'phase': 'choose', 'summary': summary,
                                           'probe_menu': MENU}, out / 'choose', run)
    probe = {'name': choice['experiment'], 'condition_indices': MENU[choice['experiment']],
             'repeats': [0, 1, 2, 3]}
    pool = probe_examples(examples, probe)
    for row in pool:
        row['origin'] = 'common' if row['condition_index'] < 2 else 'probe'
    packet = {'phase': 'propose', 'summary': summary, 'source': generator,
              'examples': pool, 'selected_probe': probe}
    decision, propose_error = execute(source, packet, out / 'propose', run)
    selected = [pool[index] for index in decision['example_indices']]
    delivered = sorted([row['condition_index'], row['repeat']] for row in selected)
    task = improvement_task({'generator_source': generator, 'meta_analysis': decision['analysis'],
                             'proposal_instruction': decision['prompt'], 'dev_metrics': summary,
                             'dev_examples': selected}, CONTRACT.replace('assets, two seeds, budgets',
                                                                        'assets, four shared seeds, budgets'))
    task['budget'] = dict(BUDGET)
    delivery = check_delivery(pool, selected, probe, False)
    task['feedback'].update(diagnosis=decision['diagnosis'], constraints=summary.get('constraints', CONSTRAINTS),
                            selected_probe=probe, delivery=delivery)
    save(out / 'coding_task.json', task)
    record = {'choose_diagnosis': choice['diagnosis'], 'probe': choice['experiment'],
              'propose_diagnosis': decision['diagnosis'], 'delivered': delivered,
              'prompt': decision['prompt'], 'analysis': decision['analysis'],
              'choose_validation_error': choose_error, 'propose_validation_error': propose_error,
              'example_count': len(selected), 'coding_model_calls': 0}
    save(out / 'record.json', record)
    return record


def compare(before, after):
    # Wording and order alone are explicitly excluded from behavioral evidence.
    fields = ('choose_diagnosis', 'probe', 'propose_diagnosis', 'delivered')
    changes = {key: {'full': before[key], 'ablated': after[key]}
               for key in fields if before[key] != after[key]}
    factual = ('objective', 'status')
    nontext = (before['probe'] != after['probe'] or before['delivered'] != after['delivered'] or
               any(before[key].get(part) != after[key].get(part)
                   for key in ('choose_diagnosis', 'propose_diagnosis') for part in factual))
    return {'nontext_behavior_changed': nontext, 'changes': changes,
            'prompt_changed': before['prompt'] != after['prompt'],
            'analysis_changed': before['analysis'] != after['analysis']}


def synthetic_examples():
    """Labelled counterfactuals, not measured scenes or formal experiment data."""
    return [{'condition_index': i, 'repeat': repeat, 'valid': True, 'solvable': True,
             'error': None, 'packet': {'spec': {'room': {'width': 6., 'depth': 6.}}},
             'pair_metrics': {'position_rms_m': .6, 'rotation_mean_deg': 80.,
                              'preference_error_m_mean': .8 if i < 2 else .2,
                              'all_hard_success': True}}
            for i in range(6) for repeat in range(4)]


def load_development(folder):
    # No glob over other splits and no reading of their records or reference states.
    cases = read(folder / 'private_tasks.json')['dev']
    rows = [read(folder / 'baseline/dev' / f'{index:02d}_{repeat}.json')
            for index in range(len(cases)) for repeat in range(4)]
    public = examples4(cases, rows)
    common = [row for row in public if row['condition_index'] < 2]
    summary = {'current': {'baseline': summary4(cases, rows), 'common': meta_summary(common),
                           'seeds_per_condition': 4}, 'history': history(),
               'constraints': dict(CONSTRAINTS)}
    return summary, public


def check(dev, generator, out, run):
    out.mkdir(parents=True, exist_ok=False)
    source = SOURCE.read_text(); audit(source)
    policies = {'full': source, **{name: ablate(source, name) for name in MECHANISMS}}
    for name, policy in policies.items():
        (out / f'{name}.py').write_text(policy)
        (out / f'{name}.diff').write_text(''.join(difflib.unified_diff(
            source.splitlines(True), policy.splitlines(True), fromfile='M3.py', tofile=name + '.py')))
    summary, examples = load_development(dev)
    synthetic = fixture_packets('unknown', 'position')['summary']
    synthetic['current']['baseline']['preference_error_m'] = .4
    margin = fixture_packets('unknown', 'none')['summary']
    margin['current']['baseline']['preference_error_m'] = .4
    margin['current']['candidate'] = dict(margin['current']['baseline'],
                                         preference_error_m=.2, diversity_rotation_deg=72.)
    datasets = [('actual_dev', summary, examples),
                ('counterfactual_history_and_delivery', synthetic, synthetic_examples()),
                ('counterfactual_margin_boundary', margin, synthetic_examples())]
    records = {}
    for label, packet_summary, pool in datasets:
        records[label] = {name: exercise(policy, packet_summary, pool, generator,
                                        out / label / name, run)
                          for name, policy in policies.items()}
    # The frozen implementation hardcodes its thresholds. Changing only the supplied
    # constraint packet must be tested, rather than assuming those values are used.
    changed = deepcopy(margin)
    changed['constraints'].update(rotation_retention_min=.95, preference_improvement_min_m=.3)
    altered = exercise(source, changed, synthetic_examples(), generator,
                       out / 'counterfactual_external_constraints', run)
    comparison = {label: {name: compare(row['full'], row[name]) for name in MECHANISMS}
                  for label, row in records.items()}
    result = {'comparisons': comparison, 'external_constraint_packet_change': compare(
        records['counterfactual_margin_boundary']['full'], altered),
        'actual_dev': {name: dict(record, coding_task_path=str((out / 'actual_dev' / name / 'coding_task.json').resolve()),
                                 choose_response_path=str((out / 'actual_dev' / name / 'choose/response.json').resolve()),
                                 propose_response_path=str((out / 'actual_dev' / name / 'propose/response.json').resolve()))
                       for name, record in records['actual_dev'].items()},
        'scope': 'development and labelled synthetic counterfactuals only',
        'coding_model_calls': 0, 'candidate_algorithm_executions': 0,
        'frozen_policies_modified': False, 'formal_results_modified': False,
        'margin_ablation': 'direction-only comparisons; outer fixed acceptance constraints preserved',
        'history_ablation': 'existing legacy diagnosis pools current/history; original validation failures retained',
        'probe_ablation': 'remove probe priority; retain complete four-seed groups and eight-example cap'}
    save(out / 'result.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('dev', 'generator', 'out', 'run'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    result = check(args.dev, args.generator.read_text(), args.out, args.run)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
