"""Publish dev-only coding evidence, paired aggregate metrics and actual costs."""
import argparse
import json
from pathlib import Path
import re
import shutil
import statistics
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from summarize_native_continuous import copy_coding
from analyze_continuous_benchmark import task_packets
from scene_rsi.meta.coding import save


def read(path):return json.loads(path.read_text())


def distribution(values):
    return {'n':len(values),'mean':statistics.mean(values),'sample_stdev':statistics.stdev(values) if len(values)>1 else None,
            'min':min(values),'max':max(values)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();run,out=args.run,args.out
    assert (run/'finished.json').exists()
    out.mkdir(parents=True,exist_ok=False)
    for filename in ('calls.json','preflight.json','started.json','proposals_complete.json','finished.json'):
        shutil.copyfile(run/filename,out/filename)
    calls=read(run/'calls.json');costs=[];scores=[];private=[];seeds=[];dev=[];comparisons=[]
    for pair in range(2):
        src=run/f'pair{pair}';dst=out/f'pair{pair}';dst.mkdir();cases=read(src/'private_tasks.json')
        private += [c['task'] for k in ('selection','test') for c in cases[k]]
        seeds += [s for k in ('selection','test') for c in cases[k] for s in (c['seed'],*c['algorithm_seeds'])]
        dev += [c['task'] for c in cases['dev']]
        save(dst/'dev_cases.json',cases['dev'])
        for filename in ('scores.json','comparison.json','reference_acceptance.json','finished.json'):
            shutil.copyfile(src/filename,dst/filename)
        for name in ('baseline_repair','c0','c1'):
            source,destination=src/name,dst/name;destination.mkdir()
            if (source/'dev').exists():shutil.copytree(source/'dev',destination/'dev')
            for split in ('dev','selection','test'):
                if (source/split/'summary.json').exists():shutil.copyfile(source/split/'summary.json',destination/(split+'_summary.json'))
            if name=='baseline_repair':continue
            slot=int(name[1:]);record=next(r for r in calls if r['pair']==pair and r['slot']==slot)
            for filename in ('record.json','source.py'):
                if (source/filename).exists():shutil.copyfile(source/filename,destination/filename)
            copy_coding(source/'coding',destination/'coding')
            for phase in ('choose','propose'):
                for filename in ('request.json','response.json','execution.json'):
                    shutil.copyfile(src/f'policy_{slot}'/phase/filename,destination/(phase+'_'+filename))
            backend=read(source/'coding/backend_result.json') if (source/'coding/backend_result.json').exists() else {}
            model=read(source/'coding/model_calls.json') if (source/'coding/model_calls.json').exists() else {'requests':[]}
            costs.append({'pair':pair,'policy':record['policy'],'status':backend.get('status'),'model':backend.get('model'),
                          'coding_wall_seconds':backend.get('wall_seconds'),'upstream_requests':len(model['requests']),
                          'usage':backend.get('usage'),'missing_usage_requests':sum(r.get('usage') is None for r in model['requests']),
                          'token_quota_rejected':model.get('token_quota_rejected',False),'resources':backend.get('resources')})
        pair_scores=read(src/'scores.json');scores+=pair_scores
        a,b=[s['selected_after'] for s in pair_scores]
        x,y=[s['raw_candidate'] for s in pair_scores]
        comparisons.append({'pair':pair,'selected_M0_minus_M1':{k:a[k]-b[k] for k in
            ('success_rate','preference_error_m','diversity_position_rms_m','valid_pair_diversity_m','generation_wall_seconds_mean')},
            'raw_M0_minus_M1_preference_error':x['preference_error_m']-y['preference_error_m'] if x is not None and y is not None else None,
            'task_interval':read(src/'comparison.json')['selected_M0_minus_M1_preference_error']})
    groups={}
    for policy in ('M0','M1'):
        c=[r for r in costs if r['policy']==policy];r=[r for r in calls if r['policy']==policy];s=[r for r in scores if r['policy']==policy]
        groups[policy]={'coding_tasks':2,'completed':sum(x['status']=='completed' for x in c),
             'static_pass':sum(bool(x.get('static_audit')) for x in r),'selected':sum(x['selected'] for x in s),
             'runtime_clean_all_splits':sum(x['status']=='evaluated' and x['dev']['runtime_errors']==0 and x['selection']['runtime_errors']==0 and y['raw_candidate']['runtime_errors']==0 for x,y in zip(r,s)),
             'heldout_guard_passes_raw':sum(x['heldout_multiobjective_guard'] for x in s),
             'selected_and_heldout_guard':sum(x['selected'] and x['heldout_multiobjective_guard'] for x in s),
             'upstream_requests':sum(x['upstream_requests'] for x in c),
             'usage':None if any(x['usage'] is None for x in c) else {k:sum(x['usage'][k] for x in c) for k in ('input_tokens','output_tokens','total_tokens')},
             'selected_test':{k:distribution([x['selected_after'][k] for x in s]) for k in
                 ('success_rate','preference_error_m','diversity_position_rms_m','generation_wall_seconds_mean')}}
    save(out/'costs.json',{'coding_tasks':costs,'monetary_cost':None,'note':'coding tasks overlap; RSS peaks are per process, not aggregate memory'})
    save(out/'paired_comparisons.json',{'pairs':comparisons,'independent_model_pairs':2,
           'selected_M0_minus_M1_preference_error':distribution([r['selected_M0_minus_M1']['preference_error_m'] for r in comparisons]),
           'note':'task-cluster intervals do not quantify model-sampling uncertainty'})
    save(out/'summary.json',{'policies':groups,'K':1,'independent_model_pairs':2,'M2_started':False})
    documents=[read(p) for p in out.rglob('*.json')]
    packets=[t for d in documents for t in task_packets(d)]
    assert not any(a==b for a in packets for b in private)
    assert all(t in dev for t in packets)
    files=[p for p in out.rglob('*') if p.is_file()]
    for path in files:
        text=path.read_text()
        assert not any(str(s) in text for s in seeds),str(path)
        assert not re.search(r'eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.|sk-(?:proj-)?[A-Za-z0-9_-]{35,}',text),str(path)
    save(out/'privacy.json',{'private_tasks':len(private),'public_files_checked':len(files),'hidden_packet_matches':0,
                           'hidden_seed_matches_including_jsonl':0,'credential_pattern_matches':0,'references_published':False,
                           'method':'direct structured and text comparison, no hashes'})
    print(json.dumps({'policies':groups,'pairs':comparisons}),flush=True)


if __name__=='__main__':main()
