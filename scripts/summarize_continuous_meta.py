"""Publish dev evidence and aggregate heldout statistics only, after proposals end."""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from analyze_continuous_benchmark import integers,task_packets
from scene_rsi.world.generator_benchmark import save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args = parser.parse_args()
    run,out = args.run,args.out
    finished = json.loads((run/'finished.json').read_text())
    cases = json.loads((run/'private_tasks.json').read_text())
    calls = json.loads((run/'calls.json').read_text())
    out.mkdir(parents=True,exist_ok=False)
    for filename in ('calls.json','scores.json','raw_K1_scores.json','expansion_gate.json','finished.json','proposals_complete.json'):
        shutil.copyfile(run/filename,out/filename)
    for fixture in ('repair','joint'):
        directory = out/('baseline_'+fixture)
        directory.mkdir()
        for split in ('dev','selection','test'):
            shutil.copyfile(run/('baseline_'+fixture)/split/'summary.json',directory/(split+'_summary.json'))
        shutil.copytree(run/('baseline_'+fixture)/'dev',directory/'dev')
    save(out/'dev_cases.json',cases['dev'])
    costs = []
    for call in calls:
        name = 'c'+str(call['slot'])
        source,destination = run/name,out/name
        destination.mkdir()
        for filename in ('source.py','source.diff','proposal.json','reservation.json','model_call.json','cli.json','events.jsonl','cli.stderr','record.json'):
            if (source/filename).exists():
                shutil.copyfile(source/filename,destination/filename)
        if (source/'workspace/request.json').exists():
            shutil.copyfile(source/'workspace/request.json',destination/'request.json')
        for split in ('dev','selection','test'):
            if (source/split/'summary.json').exists():
                shutil.copyfile(source/split/'summary.json',destination/(split+'_summary.json'))
        if (source/'dev').exists():
            shutil.copytree(source/'dev',destination/'dev')
        policy = run/f'policy_{call["slot"]}'
        for phase in ('choose','propose'):
            for filename in ('request.json','response.json','execution.json'):
                shutil.copyfile(policy/phase/filename,destination/(phase+'_'+filename))
        record = json.loads((source/'model_call.json').read_text()) if (source/'model_call.json').exists() else {}
        cli = json.loads((source/'cli.json').read_text()) if (source/'cli.json').exists() else {}
        usage = record.get('usage') or {}
        costs.append({'slot':call['slot'],'policy':call['policy'],'fixture':call['fixture'],'repeat':call['repeat'],'K':call['K'],
                      'status':call['status'],'model':record.get('returned_model',record.get('model')),
                      'upstream_calls':record.get('upstream_calls',0),'http_status':record.get('http_status'),
                      'input_tokens':usage.get('input_tokens'),'output_tokens':usage.get('output_tokens'),
                      'total_tokens':usage.get('total_tokens'),'upstream_seconds':record.get('wall_seconds'),
                      'cli_wall_seconds':cli.get('wall_seconds'),'request_bytes':record.get('request_bytes')})
    save(out/'model_costs.json',{'calls':costs,'reserved_slots':len(calls),'upstream_calls':sum(c['upstream_calls'] for c in costs),
         'known_total_tokens':sum(c['total_tokens'] or 0 for c in costs),
         'total_tokens':None if any(c['upstream_calls'] and c['total_tokens'] is None for c in costs) else sum(c['total_tokens'] or 0 for c in costs),
         'missing_usage_calls':sum(c['upstream_calls'] and c['total_tokens'] is None for c in costs),
         'model_time_scope':'upstream times may overlap; finished.wall_seconds is experiment wall clock',
         'monetary_cost':None,'M2_started':False})
    save(out/'outcome.json',{'complete_proposals':sum(c['status']!='proposal_failed' for c in calls),
         'static_audit_passes':sum(bool(c.get('static_audit',{}).get('passed')) for c in calls),
         'dynamically_evaluated_candidates':sum(c['status']=='evaluated' for c in calls),
         'interpretation':'proposal feasibility only; no algorithm comparison possible' if all(c['status']=='proposal_failed' for c in calls)
                          else 'exploratory comparison; task clusters are not independent model samples',
         'frozen_controller_git':finished['controller_git']})
    execution = []
    for folder in [*run.glob('baseline_*'),*run.glob('c[0-9]*')]:
        for split in ('dev','selection','test'):
            paths = list((folder/split).glob('[0-9][0-9]_[0-9].json'))
            if not paths: continue
            rows = [json.loads(p.read_text()) for p in paths]
            execution.append({'program':folder.name,'split':split,'scenes':len(rows),
                 'queries':sum(r['queries'] for r in rows),
                 'generation_wall_seconds_sum':sum(r['generation_wall_seconds'] for r in rows),
                 'controller_cpu_seconds_sum':sum(r['controller_cpu_seconds'] for r in rows),
                 'runtime_cpu_seconds_sum':sum((r['execution'] or {}).get('runtime_cpu_seconds',0) for r in rows),
                 'runtime_errors':sum(r['error'] is not None for r in rows)})
    save(out/'evaluation_costs.json',{'groups':execution,
         'scope':'generation only, including container startup; final independent scoring excluded; no GPU',
         'rss_scope':'raw execution RSS is controller-lifetime child high water, not an individual candidate peak'})
    private = [c['task'] for split in ('selection','test') for c in cases[split]]
    private_seeds = [s for split in ('selection','test') for c in cases[split] for s in (c['seed'],*c['algorithm_seeds'])]
    public_files = list(out.rglob('*.json'))
    siblings = out.parent/'renders'
    if siblings.exists(): public_files += list(siblings.rglob('*.json'))
    documents = [json.loads(f.read_text()) for f in public_files]
    packets = [v for d in documents for v in task_packets(d)]
    assert not any(a==b for a in packets for b in private)
    assert not any(s in private_seeds for d in documents for s in integers(d))
    assert all(p in [c['task'] for c in cases['dev']] for p in packets)
    save(out/'privacy.json',{'hidden_tasks':len(private),'public_json_files':len(public_files),
         'public_dev_packet_occurrences':len(packets),'hidden_packet_matches':0,'hidden_seed_matches':0,
         'reference_outputs_published':False,'method':'direct structured comparison; no hashes'})
    print(json.dumps({'calls':len(calls),'privacy':'passed','wall_seconds':finished['wall_seconds']}))


if __name__=='__main__':
    main()
