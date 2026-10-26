"""Inspect frozen programs and published real requests; no policy or model execution."""
import argparse
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
M1 = ROOT / 'experiments/generalization_meta/results/meta/frozen_policy.py'
M2 = ROOT / 'experiments/m2_failure/M2.py'
PILOT = ROOT / 'experiments/m2_failure/results/pilot'


def read(path):
    return json.loads(path.read_text())


def functions(path):
    return {n.name: n for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef)}


def evidence():
    before, after = functions(M1), functions(M2)
    changes = {name: {'M1_line': before[name].lineno if name in before else None,
                      'M2_line': after[name].lineno if name in after else None,
                      'changed': name not in before or name not in after or ast.dump(before[name]) != ast.dump(after[name])}
               for name in sorted(before.keys() | after.keys())}
    reports = {}
    for policy, folder in (('M1', 'c1'), ('M2', 'c2')):
        base = PILOT / folder
        choose = read(base / 'policy/choose/request.json')
        choice = read(base / 'policy/choose/response.json')
        request = read(base / 'policy/propose/request.json')
        response = read(base / 'policy/propose/response.json')
        task = read(base / 'coding/TASK.json')
        selected = [request['examples'][i] for i in response['example_indices']]
        if selected != task['feedback']['examples']:
            raise ValueError('saved selection does not match actual backend task')
        new_probe = [r for r in request['examples'] if r['condition_index'] >= 4]
        execution = {phase: read(base / f'policy/{phase}/execution.json') for phase in ('choose', 'propose')}
        events = [json.loads(line) for line in (base / 'coding/events.jsonl').read_text().splitlines()]
        messages, reads = [], []
        for line, event in enumerate(events, 1):
            item = event.get('item', {})
            if event.get('type') != 'item.completed':
                continue
            if item.get('type') == 'agent_message':
                messages.append({'line': line, 'text': item['text']})
            if item.get('type') == 'command_execution':
                reads.append({'line': line, 'command': item.get('command'),
                              'exit_code': item.get('exit_code'),
                              'output_characters': len(item.get('aggregated_output', ''))})
        reports[policy] = {'source': str((M1 if policy == 'M1' else M2).relative_to(ROOT)),
            'choose_summary': choose['summary'], 'choice': choice,
            'propose_summary': request['summary'], 'propose_response': response,
            'probe_pairs_available': [[r['condition_index'], r['repeat']] for r in new_probe],
            'selected_pairs': [[r['condition_index'], r['repeat']] for r in selected],
            'probe_pairs_delivered': [[r['condition_index'], r['repeat']] for r in selected if r['condition_index'] >= 4],
            'actual_task_analysis_matches_propose': task['analysis'] == response['analysis'],
            'full_experience_in_backend': 'examples' in task['feedback']['experience'],
            'full_experience_in_policy_summary': 'examples' in request['summary']['experience'],
            'backend_budget': task['budget'], 'execution': execution,
            'backend_reads': reads, 'backend_messages': messages}
    history = reports['M2']['choose_summary']['experience']
    return {'scope': 'published DEV and existing logs only; no model calls or policy execution',
            'function_changes': changes, 'policies': reports,
            'historical_deficits_not_current_measurements': {
                'position': (.9 - history['position_retention_mean']) / .9,
                'rotation': (.8 - history['rotation_retention_mean']) / .8},
            'same_final_example_membership': sorted(reports['M1']['selected_pairs']) == sorted(reports['M2']['selected_pairs']),
            'same_backend_budget': reports['M1']['backend_budget'] == reports['M2']['backend_budget']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(evidence(), ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
