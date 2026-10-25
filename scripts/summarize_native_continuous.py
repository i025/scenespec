"""Publish actual native patches/dev evidence, aggregate private results and costs."""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from analyze_continuous_benchmark import task_packets,integers
from run_continuous_meta import bootstrap_difference,load_rows
from scene_rsi.meta.coding import save


def copy_coding(source,out):
    out.mkdir(parents=True)
    for name in ('candidate.diff','candidate.py','summary.md','events.jsonl','cli.stderr','cli.json','model_calls.json','reservation.json','backend_result.json'):
        if (source/name).exists():shutil.copyfile(source/name,out/name)
    for name in ('TASK.json','CONTRACT.md'):
        if (source/'workspace'/name).exists():shutil.copyfile(source/'workspace'/name,out/name)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();run,out=args.run,args.out
    formal=run/'formal';finished=json.loads((formal/'finished.json').read_text())
    cases=json.loads((formal/'private_tasks.json').read_text());calls=json.loads((formal/'calls.json').read_text())
    out.mkdir(parents=True,exist_ok=False)
    for name in ('calls.json','scores.json','finished.json','proposals_complete.json','reference_acceptance.json'):
        shutil.copyfile(formal/name,out/name)
    save(out/'dev_cases.json',cases['dev'])
    smoke=out/'smoke';smoke.mkdir()
    shutil.copyfile(run/'smoke/gate.json',smoke/'gate.json')
    copy_coding(run/'smoke/coding',smoke/'coding')
    for phase in ('baseline','evaluation'):
        if (run/'smoke'/phase).exists():shutil.copytree(run/'smoke'/phase,smoke/phase)
    costs=[]
    def cost(folder,label,policy=None,fixture=None):
        result=json.loads((folder/'backend_result.json').read_text()) if (folder/'backend_result.json').exists() else {}
        records=json.loads((folder/'model_calls.json').read_text())['requests'] if (folder/'model_calls.json').exists() else []
        costs.append({'task':label,'policy':policy,'fixture':fixture,'status':result.get('status'),
                      'model':result.get('model'),'wall_seconds':result.get('wall_seconds'),
                      'upstream_requests':len(records),'usage':result.get('usage'),
                      'missing_usage_requests':sum(r.get('usage') is None for r in records),'resources':result.get('resources')})
    cost(run/'smoke/coding','unscored')
    for fixture in ('repair','joint'):
        dest=out/('baseline_'+fixture);dest.mkdir()
        shutil.copytree(formal/('baseline_'+fixture)/'dev',dest/'dev')
        for split in ('dev','selection','test'):
            shutil.copyfile(formal/('baseline_'+fixture)/split/'summary.json',dest/(split+'_summary.json'))
    for record in calls:
        name='c'+str(record['slot']);source,dest=formal/name,out/name
        dest.mkdir()
        copy_coding(source/'coding',dest/'coding')
        cost(source/'coding',name,record['policy'],record['fixture'])
        for filename in ('record.json','source.py'):
            if (source/filename).exists():shutil.copyfile(source/filename,dest/filename)
        if (source/'dev').exists():shutil.copytree(source/'dev',dest/'dev')
        for split in ('selection','test'):
            if (source/split/'summary.json').exists():shutil.copyfile(source/split/'summary.json',dest/(split+'_summary.json'))
        for phase in ('choose','propose'):
            for name2 in ('request.json','response.json','execution.json'):
                shutil.copyfile(formal/f'policy_{record["slot"]}'/phase/name2,dest/(phase+'_'+name2))
    groups={}
    for label in ('unscored','M0','M1'):
        rows=[r for r in costs if r['task']=='unscored'] if label=='unscored' else [r for r in costs if r['policy']==label]
        groups[label]={'coding_tasks':len(rows),'completed':sum(r['status']=='completed' for r in rows),
             'upstream_requests':sum(r['upstream_requests'] for r in rows),'missing_usage_requests':sum(r['missing_usage_requests'] for r in rows),
             'usage':None if any(r['usage'] is None for r in rows) else {k:sum(r['usage'][k] for r in rows) for k in ('input_tokens','output_tokens','total_tokens')},
             'coding_wall_seconds_sum':sum(r['wall_seconds'] or 0 for r in rows)}
    save(out/'costs.json',{'tasks':costs,'groups':groups,'monetary_cost':None,
          'wall_scope':'coding task sums may overlap; finished is formal wall clock; smoke is separate'})
    completion={'formal_tasks':4,'cli_and_nonempty_patch':sum(r['status']=='completed' for r in costs if r['policy']),
          'static_passes':sum(bool(r.get('static_audit')) for r in calls),
          'runtime_clean_dev_and_selection':sum(r['status']=='evaluated' and r['dev']['runtime_errors']==0 and r['selection']['runtime_errors']==0 for r in calls),
          'selected_by_unchanged_guard':sum(bool(r.get('selected')) for r in calls),'M2_started':False}
    save(out/'completion.json',completion)
    comparisons=[]
    for fixture in ('repair','joint'):
        selected=[]
        for policy in ('M0','M1'):
            record=next(r for r in calls if r['fixture']==fixture and r['policy']==policy)
            folder=formal/f'c{record["slot"]}' if record.get('selected') else formal/('baseline_'+fixture)
            selected.append(load_rows(folder/'test',cases['test']))
        comparisons.append({'fixture':fixture,'selected_M0_minus_M1_preference_error':bootstrap_difference(*selected),
                            'model_samples_per_policy':1})
    save(out/'policy_comparisons.json',comparisons)
    private=[c['task'] for split in ('selection','test') for c in cases[split]]
    seeds=[s for split in ('selection','test') for c in cases[split] for s in (c['seed'],*c['algorithm_seeds'])]
    files=list(out.rglob('*.json'));documents=[json.loads(p.read_text()) for p in files]
    packets=[p for d in documents for p in task_packets(d)]
    assert not any(a==b for a in packets for b in private)
    assert not any(s in seeds for d in documents for s in integers(d))
    assert all(p in [c['task'] for c in cases['dev']] for p in packets)
    save(out/'privacy.json',{'private_tasks':20,'public_json_files':len(files),'public_dev_packet_occurrences':len(packets),
              'hidden_packet_matches':0,'hidden_seed_matches':0,'reference_outputs_published':False,'method':'direct structured comparison, no hashes'})
    print(json.dumps(completion),flush=True)


if __name__=='__main__':main()
