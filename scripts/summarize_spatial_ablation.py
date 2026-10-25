"""Publish pilot evidence; heldout output states and per-case feedback stay private."""
import argparse
from collections import Counter
import json
from pathlib import Path
import shutil
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scene_rsi.world.generator_benchmark import save


def packets(value):
    if isinstance(value, dict):
        if {'spec', 'domains', 'elevations'} <= value.keys():
            raise ValueError('unexpected reference-like packet')
        if {'spec', 'domains', 'requirements'} <= value.keys():
            yield value
        for child in value.values():
            yield from packets(child)
    elif isinstance(value, list):
        for child in value:
            yield from packets(child)


def aggregate(report):
    report = dict(report)
    resources = report.pop('execution', [])
    report['execution_aggregate'] = {'processes':len(resources),
        'nonzero_exits':sum(r['exit_code'] != 0 for r in resources),
        'runtime_cpu_seconds':sum(r['runtime_cpu_seconds'] for r in resources),
        'process_lifetime_wall_seconds':sum(r['wall_seconds'] for r in resources)}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    run, out = args.run, args.out
    out.mkdir(parents=True, exist_ok=False)
    calls = json.loads((run/'calls.json').read_text())
    scores = json.loads((run/'scores.json').read_text())
    tasks = json.loads((run/'private_tasks.json').read_text())
    for name in ('calls.json', 'scores.json', 'finished.json', 'ceiling_stop.json', 'proposals_complete.json'):
        shutil.copyfile(run/name, out/name)
    if (run/'early_ceiling_stop.json').exists():
        shutil.copyfile(run/'early_ceiling_stop.json', out/'early_ceiling_stop.json')
    for file in run.glob('selection_K*.json'):
        shutil.copyfile(file, out/file.name)
    save(out/'dev_tasks.json', {'common':tasks['common'], 'probes':tasks['probes']})
    costs = []
    for record in calls:
        source, destination = run/f"c{record['slot']}", out/f"c{record['slot']}"
        destination.mkdir()
        for name in ('source.py','source.diff','reservation.json','model_call.json','events.jsonl','cli.json','cli.stderr',
                     'workspace/request.json','workspace/schema.json','output/proposal.json'):
            if (source/name).exists():
                (destination/name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source/name, destination/name)
        model = json.loads((source/'model_call.json').read_text())
        cli = json.loads((source/'cli.json').read_text()) if (source/'cli.json').exists() else {}
        usage = model.get('usage') or {}
        costs.append({'slot':record['slot'], 'arm':record['arm'], 'round':record['round'],
                      'upstream_calls':model['upstream_calls'], 'http_status':model.get('http_status'),
                      'returned_model':model.get('returned_model'), 'stream_status':model['status'],
                      'response_status':model.get('response_status'), 'usage_reported':bool(usage),
                      **{key:usage.get(key) for key in ('input_tokens','output_tokens','total_tokens')},
                      'model_seconds':model.get('wall_seconds'), 'cli_seconds':cli.get('wall_seconds'),
                      'cli_exit':cli.get('exit_code')})
    labels = {c['trial'] for c in calls}
    for folder in run.iterdir():
        if not folder.is_dir() or not (folder.name in labels or folder.name.startswith('baseline_')):
            continue
        for file in folder.rglob('*'):
            if not file.is_file():
                continue
            relative = file.relative_to(run)
            private = any(part in ('selection','test','candidate_selection') or part.startswith('test_')
                          for part in relative.parts)
            if private and file.name != 'summary.json':
                continue
            destination = out/relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if private:
                save(destination, aggregate(json.loads(file.read_text())))
            else:
                shutil.copyfile(file, destination)
    cumulative, stages = {}, {}
    for budget in sorted({r['K'] for r in scores}):
        cumulative[str(budget)], stages[str(budget)] = {}, {}
        for arm in ('M0','M0_M1probe','M1_random','M1'):
            records = [c for c in calls if c['arm'] == arm and c['round'] <= budget]
            spent = [c for c in costs if c['arm'] == arm and c['round'] <= budget]
            evaluated = [c for c in records if c['status'] == 'evaluated']
            complete = sum(c['status'] != 'generation_failed' for c in records)
            values = [s['MetaImp'] for s in scores if s['arm'] == arm and s['K'] == budget]
            final = [s['after']['solvable_rate'] for s in scores if s['arm'] == arm and s['K'] == budget]
            cumulative[str(budget)][arm] = {'calls':len(spent), 'missing_usage_calls':sum(not c['usage_reported'] for c in spent),
                **{key:sum(c[key] for c in spent if c[key] is not None)
                   for key in ('input_tokens','output_tokens','total_tokens','model_seconds','cli_seconds')}}
            stages[str(budget)][arm] = {'attempts':len(records), 'complete_proposals':complete,
                'static_passes':len(evaluated), 'static_pass_given_complete':len(evaluated)/complete if complete else None,
                'quality_given_static_pass_selection':statistics.mean(c['selection']['solvable_rate'] for c in evaluated) if evaluated else None,
                'selected_final_rates':final, 'MetaImp_values':values, 'MetaImp_mean':statistics.mean(values),
                'MetaImp_sample_sd_across_initial_generators':statistics.stdev(values),
                'dev_failure_counts':dict(sum((Counter(c['dev']['errors']) for c in evaluated), Counter())),
                'selection_failure_counts':dict(sum((Counter(c['selection']['errors']) for c in evaluated), Counter())),
                'probe_counts':dict(Counter(c['probe'] for c in records))}
    request_files = list(run.glob('c*/workspace/request.json'))
    request_files += list(run.glob('*/r*/choose/request.json'))+list(run.glob('*/r*/propose/request.json'))
    public = [p for path in request_files for p in packets(json.loads(path.read_text()))]
    private = [c['packet'] for split in ('selection','test') for c in tasks[split]]
    if any(a == b for a in public for b in private):
        raise RuntimeError('private packet reached policy or model')
    save(out/'feedback_privacy.json', {'requests':len(request_files), 'public_packets':len(public),
         'private_packets':len(private), 'private_packets_found':0, 'heldout_rows_published':False,
         'method':'direct structured value comparison, no hashes'})
    save(out/'costs.json', {'calls':costs, 'cumulative':cumulative,
        'upstream_calls':sum(c['upstream_calls'] for c in costs),
        'known_total_tokens':sum(c['total_tokens'] for c in costs if c['total_tokens'] is not None),
        'missing_usage_calls':sum(not c['usage_reported'] for c in costs), 'usd':None})
    save(out/'stages.json', stages)
    probe_pairs = []
    for fixture in sorted({c['fixture'] for c in calls}):
        for round_number in sorted({c['round'] for c in calls}):
            for left, right in (('M0','M0_M1probe'), ('M1_random','M1')):
                pair = [next(c for c in calls if c['fixture'] == fixture and c['round'] == round_number and c['arm'] == arm)
                        for arm in (left, right)]
                inputs = [json.loads((run/c['trial']/f'r{round_number}'/'choose/request.json').read_text()) for c in pair]
                probe_pairs.append({'fixture':fixture, 'round':round_number, 'arms':[left,right],
                                    'same_common_diagnostics':inputs[0] == inputs[1],
                                    'choices':[c['probe'] for c in pair], 'diverged':pair[0]['probe'] != pair[1]['probe']})
    save(out/'probe_pairs.json', probe_pairs)
    execution = {}
    for arm in ('M0','M0_M1probe','M1_random','M1'):
        # Exact labels, not suffix matches: M0_M1probe and M1_random are distinct arms.
        arm_labels = {c['trial'] for c in calls if c['arm'] == arm}
        files = [file for label in arm_labels for file in (run/label).rglob('summary.json')]
        aborted = [file for file in files if 'r2' in file.relative_to(run).parts and not any(c['round'] == 2 for c in calls)]
        resources = [r for file in files if file not in aborted for r in json.loads(file.read_text()).get('execution', [])]
        preparation = [r for file in aborted for r in json.loads(file.read_text()).get('execution', [])]
        execution[arm] = {'processes':len(resources), 'nonzero_exits':sum(r['exit_code'] != 0 for r in resources),
                          'runtime_cpu_seconds':sum(r['runtime_cpu_seconds'] for r in resources),
                          'process_lifetime_wall_seconds':sum(r['wall_seconds'] for r in resources),
                          'abandoned_K2_preparation':{'processes':len(preparation),
                              'runtime_cpu_seconds':sum(r['runtime_cpu_seconds'] for r in preparation),
                              'model_calls':0},
                          'scope':'Apptainer/candidate child CPU, all parent/candidate dev/selection and selected test; excludes model/policy/checker CPU'}
    save(out/'execution_costs.json', execution)
    preparation = []
    for path in sorted(run.glob('*/r2/choose/response.json')):
        folder = path.parent.parent
        row = {'trial':folder.parent.name, 'probe':json.loads(path.read_text())['experiment'], 'model_calls':0}
        for phase in ('parent_common','parent_probe'):
            if (folder/phase/'summary.json').exists():
                row[phase] = json.loads((folder/phase/'summary.json').read_text())['metrics']
        preparation.append(row)
    save(out/'abandoned_K2_preparation.json', preparation)
    print(json.dumps(stages, indent=2))


if __name__ == '__main__':
    main()
