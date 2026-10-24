"""Archive completed runs and costs without publishing heldout per-task feedback."""
import argparse
from collections import Counter
import json
from pathlib import Path
import shutil
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scene_rsi.world.generator_benchmark import save


def packets(value):
    if isinstance(value, dict):
        if {'spec', 'domains', 'minimum_steps'} <= value.keys():
            yield value
        for child in value.values():
            yield from packets(child)
    elif isinstance(value, list):
        for child in value:
            yield from packets(child)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    run, out = args.run, args.out
    summary = json.loads((run/'summary.json').read_text())
    calls = json.loads((run/'calls.json').read_text())
    tasks = json.loads((run/'private_tasks.json').read_text())
    out.mkdir(parents=True, exist_ok=False)
    for name in ('summary.json', 'calls.json', 'extension.json', 'proposals_complete.json'):
        shutil.copyfile(run/name, out/name)
    for file in run.glob('selection_K*.json'):
        shutil.copyfile(file, out/file.name)
    save(out/'dev_tasks.json', tasks['dev'])
    costs = []
    for record in calls:
        folder = f"c{record['slot']}"
        source, destination = run/folder, out/folder
        destination.mkdir()
        for name in ('source.py', 'source.diff', 'reservation.json', 'model_call.json', 'events.jsonl', 'cli.json', 'cli.stderr'):
            if (source/name).exists():
                shutil.copyfile(source/name, destination/name)
        for subdir, filenames in [('workspace', ('request.json', 'schema.json')), ('output', ('proposal.json',))]:
            (destination/subdir).mkdir()
            for name in filenames:
                if (source/subdir/name).exists():
                    shutil.copyfile(source/subdir/name, destination/subdir/name)
        model = json.loads((source/'model_call.json').read_text())
        cli = json.loads((source/'cli.json').read_text()) if (source/'cli.json').exists() else {}
        usage = model.get('usage') or {}
        costs.append({'slot':record['slot'], 'trial':record['trial'], 'round':record['round'],
                      'model':model.get('returned_model'), 'http_status':model.get('http_status'),
                      'stream_status':model['status'], 'usage_reported':bool(usage),
                      'upstream_calls':model['upstream_calls'], 'cli_exit':cli.get('exit_code'),
                      **{key:usage.get(key) for key in ('input_tokens', 'output_tokens', 'total_tokens')},
                      'model_seconds':model['wall_seconds'], 'cli_seconds':cli.get('wall_seconds')})
    for source in run.iterdir():
        if not source.is_dir() or not (source.name.startswith('baseline_') or source.name.endswith(('_M0', '_M1'))):
            continue
        for file in source.rglob('*'):
            if not file.is_file():
                continue
            relative = file.relative_to(run)
            private = any(part in ('selection', 'test', 'candidate_selection') or part.startswith('test_c')
                          for part in relative.parts)
            if private and file.name != 'summary.json':
                continue
            destination = out/relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if private:
                report = json.loads(file.read_text())
                resources = report.pop('execution', [])
                report['execution_aggregate'] = {
                    'processes':len(resources), 'nonzero_exits':sum(r['exit_code'] != 0 for r in resources),
                    'runtime_cpu_seconds':sum(r['runtime_cpu_seconds'] for r in resources),
                    'process_lifetime_wall_seconds':sum(r['wall_seconds'] for r in resources)}
                save(destination, report)
            else:
                shutil.copyfile(file, destination)
    totals = {}
    for budget in sorted({c['round'] for c in calls if c['round'] >= 2}):
        totals[str(budget)] = {}
        for arm in ('M0', 'M1'):
            rows = [c for c in costs if c['round'] <= budget and c['trial'].endswith('_'+arm)]
            totals[str(budget)][arm] = {'calls':len(rows), **{
                key:sum(row[key] for row in rows if row[key] is not None)
                for key in ('input_tokens', 'output_tokens', 'total_tokens', 'model_seconds', 'cli_seconds')}}
            totals[str(budget)][arm]['missing_usage_calls'] = sum(not r['usage_reported'] for r in rows)
            totals[str(budget)][arm]['missing_cli_timing_calls'] = sum(r['cli_seconds'] is None for r in rows)
    save(out/'costs.json', {'calls':costs, 'cumulative':totals,
                          'upstream_calls':sum(c['upstream_calls'] for c in costs),
                          'http_200_requests':sum(c['http_status'] == 200 for c in costs),
                          'cli_completed_proposals':sum(c['cli_exit'] == 0 for c in costs),
                          'available_total_tokens':sum(c['total_tokens'] or 0 for c in costs),
                          'missing_usage_calls':sum(not c['usage_reported'] for c in costs),
                          'usd':None, 'note':'No measured billing amount; actual provider token usage.'})
    request_files = list(run.glob('c*/workspace/request.json'))
    request_files += list(run.glob('*_M*/r*/choose/request.json'))+list(run.glob('*_M*/r*/propose/request.json'))
    public = [p for path in request_files for p in packets(json.loads(path.read_text()))]
    private = [c['packet'] for split in ('selection', 'test') for c in tasks[split]]
    if any(a == b for a in public for b in private):
        raise RuntimeError('private task appeared in a policy/model request')
    save(out/'feedback-privacy.json', {'request_files':len(request_files), 'public_packets':len(public),
         'private_packets':len(private), 'private_packets_found':0,
         'method':'direct structured-value comparison; no hashes', 'heldout_rows_published':False})
    per_fixture = {}
    for budget in sorted({r['K'] for r in summary['results']}):
        per_fixture[str(budget)] = {}
        for fixture in ('first_fit', 'wall_priority', 'local_relations'):
            per_fixture[str(budget)][fixture] = {}
            for arm in ('M0', 'M1'):
                values = [r['gain'] for r in summary['results']
                          if r['K'] == budget and r['fixture'] == fixture and r['arm'] == arm]
                per_fixture[str(budget)][fixture][arm] = {'values':values, 'mean':statistics.mean(values),
                                                          'sample_sd':statistics.stdev(values)}
    save(out/'per-fixture.json', per_fixture)
    stages = {}
    for budget in sorted({r['K'] for r in summary['results']}):
        stages[str(budget)] = {}
        for arm in ('M0', 'M1'):
            records = [c for c in calls if c['round'] <= budget and c['trial'].endswith('_'+arm)]
            evaluated = [c for c in records if 'selection' in c]
            scene_tasks = sum(c['selection']['tasks'] for c in evaluated)
            execution_errors = sum(c['selection']['errors'].get('execution_error', 0) for c in evaluated)
            returned = scene_tasks-execution_errors
            stages[str(budget)][arm] = {'outcomes':{
                status:sum(r['status'] == status for r in records) for status in
                ('generation_failed', 'static_rejected', 'evaluated')},
                'dev_failures':dict(sum((Counter(c['dev']['errors'])
                                        for c in records if 'dev' in c), Counter())),
                'selection_failures':dict(sum((Counter(c['selection']['errors'])
                                              for c in records if 'selection' in c), Counter()))}
            stages[str(budget)][arm].update(
                static_pass_rate_given_complete_code=len(evaluated)/(len(records)-sum(c['status']=='generation_failed' for c in records)),
                selection_tasks=scene_tasks, usable_scene_returns=returned,
                task_success_given_usable_scene=(sum(c['selection']['solvable'] for c in evaluated)/returned if returned else None))
        paired = []
        for fixture in ('first_fit', 'wall_priority', 'local_relations'):
            for repeat in range(2):
                by_arm = {arm:[c for c in calls if c['round'] <= budget and
                          c['trial'] == f'{fixture}_{repeat}_{arm}'] for arm in ('M0', 'M1')}
                if all(len([c for c in rows if c['status'] == 'evaluated']) == budget for rows in by_arm.values()):
                    rates = {r['arm']:r['gain'] for r in summary['results'] if r['K'] == budget
                             and r['fixture'] == fixture and r['repeat'] == repeat}
                    paired.append({'fixture':fixture, 'repeat':repeat, 'M1_minus_M0':rates['M1']-rates['M0'],
                                   'selection_quality_mean':{arm:statistics.mean(c['selection']['solvable_rate'] for c in rows)
                                                             for arm, rows in by_arm.items()}})
        stages[str(budget)]['both_arms_all_candidates_audited'] = paired
    save(out/'stages.json', stages)
    failures = []
    for call in calls:
        if call['status'] != 'generation_failed':
            continue
        folder = run/f"c{call['slot']}"
        events = (folder/'events.jsonl').read_text() if (folder/'events.jsonl').exists() else ''
        model = json.loads((folder/'model_call.json').read_text())
        kind = ('stream_interrupted' if model.get('error') == 'IncompleteRead' else
                'output_token_limit' if 'max_output_tokens' in events else
                'wall_limit' if 'TimeoutExpired' in call.get('error', '') else 'other_proposal_error')
        failures.append({'slot':call['slot'], 'trial':call['trial'], 'round':call['round'], 'kind':kind})
    save(out/'proposal-failures.json', failures)
    sensitivity = {}
    for budget in sorted({r['K'] for r in summary['results']}):
        affected = {f['trial'].rsplit('_', 1)[0] for f in failures
                    if f['round'] <= budget and f['kind'] == 'stream_interrupted'}
        pairs = []
        for fixture in ('first_fit', 'wall_priority', 'local_relations'):
            for repeat in range(2):
                if f'{fixture}_{repeat}' in affected:
                    continue
                values = {r['arm']:r['gain'] for r in summary['results']
                          if r['K'] == budget and r['fixture'] == fixture and r['repeat'] == repeat}
                pairs.append(values['M1']-values['M0'])
        sensitivity[str(budget)] = {'excluded_paired_units':sorted(affected), 'paired_differences':pairs,
                                     'mean':statistics.mean(pairs),
                                     'sample_sd':statistics.stdev(pairs) if len(pairs)>1 else None,
                                     'note':'Supplement only; original six-pair primary result remains unchanged.'}
    save(out/'transport-sensitivity.json', sensitivity)
    influence = {}
    for budget, scores in summary['scores'].items():
        values = scores['M1_minus_M0']['values']
        leave_one_out = [(sum(values)-value)/(len(values)-1) for value in values]
        influence[budget] = {'paired_differences':values, 'leave_one_pair_out_means':leave_one_out,
                              'min':min(leave_one_out), 'max':max(leave_one_out),
                              'note':'Robustness diagnostic only; all six pairs retained in primary scores.'}
    save(out/'pair-influence.json', influence)
    execution_costs = {}
    for budget in sorted({r['K'] for r in summary['results']}):
        execution_costs[str(budget)] = {}
        for arm in ('M0', 'M1'):
            resources = []
            for path in run.glob(f'*_{arm}/r*/*/summary.json'):
                round_number = int(path.parents[1].name[1:])
                if round_number <= budget:
                    resources.extend(json.loads(path.read_text())['execution'])
            execution_costs[str(budget)][arm] = {
                'restricted_processes':len(resources),
                'runtime_cpu_seconds':sum(r['runtime_cpu_seconds'] for r in resources),
                'process_lifetime_wall_seconds':sum(r['wall_seconds'] for r in resources),
                'nonzero_exits':sum(r['exit_code'] != 0 for r in resources),
                'scope':'parent dev + candidate dev/selection, including Apptainer; excludes policy/model/test'}
    save(out/'execution-costs.json', execution_costs)
    print(json.dumps({'costs':totals, 'scores':summary['scores']}, indent=2))


if __name__ == '__main__':
    main()
