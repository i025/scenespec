"""Public aggregates and dev-only artifacts for the balanced M2/M3 experiment."""
import argparse
import json
from pathlib import Path
import re
import shutil
import statistics
import sys

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from analyze_m3_selection import aggregate,checks,compare,load_split,scalar,split_comparison
from analyze_continuous_benchmark import task_packets
from run_m3_reliable import INITIALS
from scene_rsi.continuous.meta_experiment import eligible
from scene_rsi.meta.coding import save
from summarize_native_continuous import copy_coding

CREDENTIAL=re.compile(r'(?<![A-Za-z0-9_-])(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|sk-(?:proj-)?[A-Za-z0-9_-]{35,})')


def read(path):return json.loads(path.read_text())


def cluster_statistics(before,after,indices):
    a,b=aggregate(before,indices),aggregate(after,indices)
    values=compare(a,b)
    return {'resamples':len(indices),'conditions_per_draw':len(before),'seeds_kept_together':4,
        'percentile_95':{key:np.quantile(value,[.025,.975]).tolist() for key,value in values.items()},
        'joint_accept_frequency':float(np.mean([eligible(scalar(a,i),scalar(b,i)) for i in range(len(indices))])),
        'analysis_seed_public':20261102,
        'scope':'Condition uncertainty given these fixed model draws; not independent model-sampling uncertainty'}


def coding_cost(folder):
    ledger=read(folder/'logical.json');resources=[];failures=[]
    for record in ledger['attempts']:
        attempt=folder/record['directory']
        result=read(attempt/'backend_result.json') if (attempt/'backend_result.json').exists() else {}
        resources.append(result.get('resources'))
        if result.get('status')!='completed':
            events=[json.loads(line) for line in (attempt/'events.jsonl').read_text().splitlines()] if (attempt/'events.jsonl').exists() else []
            messages=[e['message'] for e in events if e['type']=='error']
            requests=read(attempt/'model_calls.json')['requests'] if (attempt/'model_calls.json').exists() else []
            wire_errors=[]
            for request in requests:
                response=attempt/f'response_{request["index"]}.sse'
                if not response.exists():continue
                text=response.read_text()
                packets=([json.loads(text)] if text.lstrip().startswith('{') else
                         [json.loads(line[6:]) for line in text.splitlines() if line.startswith('data: {')])
                for packet in packets:
                    error=packet.get('error') or packet.get('response',{}).get('error')
                    if error and error not in wire_errors:wire_errors.append(error)
            failures.append({'attempt':record['directory'],'backend_status':record['status'],'cli_errors':messages,
                'server_errors':wire_errors,
                'capacity_error_reported':any('at capacity' in message.lower() for message in messages),
                'requests':[{'index':r['index'],'http_status':r.get('http_status'),'response_status':r.get('response_status'),
                             'usage_known':r.get('usage') is not None} for r in requests],
                'retry_eligible_under_frozen_rule':record['capacity_retry_eligible']})
    return {**ledger,'editor_resources_per_attempt':resources,'failed_attempt_details':failures}


def publish_candidate(source,dest):
    dest.mkdir(parents=True)
    for name in ('task.json','record.json','source.py'):
        if (source/name).exists():shutil.copyfile(source/name,dest/name)
    shutil.copytree(source/'policy',dest/'policy')
    (dest/'coding').mkdir()
    for name in ('logical.json','backend_result.json'):
        shutil.copyfile(source/'coding'/name,dest/'coding'/name)
    for attempt in read(source/'coding/logical.json')['attempts']:
        copy_coding(source/'coding'/attempt['directory'],dest/'coding'/attempt['directory'])
    for split in ('selection','test'):
        if (source/split/'summary.json').exists():shutil.copyfile(source/split/'summary.json',dest/(split+'_summary.json'))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();run,out=args.run,args.out
    assert (run/'finished.json').exists();out.mkdir(parents=True,exist_ok=False)
    cases=read(run/'private_tasks.json');calls=read(run/'calls.json');scores=read(run/'scores.json')
    save(out/'dev_cases.json',[{'task':c['task'],'algorithm_seeds':c['algorithm_seeds']} for c in cases['dev']])
    for name in ('prepared.json','frozen.json','reference_acceptance.json','reference_sampling.json','proposals_complete.json','finished.json','calls.json','scores.json'):
        shutil.copyfile(run/name,out/name)
    baselines={};before_groups={}
    for initial in INITIALS:
        folder=out/initial/'baseline';folder.mkdir(parents=True)
        shutil.copytree(run/initial/'baseline/dev',folder/'dev')
        baselines[initial]={}
        for split in ('dev','selection','test'):
            summary=read(run/initial/'baseline'/split/'summary.json');baselines[initial][split]=summary
            save(folder/(split+'_summary.json'),summary)
            if split!='dev':
                if summary['runtime_errors']==0:
                    _,before_groups[initial,split],_=load_split(run/initial/'baseline'/split,cases[split])
    indices={split:np.random.default_rng(20261102).integers(len(cases[split]),size=(20000,len(cases[split]))) for split in ('selection','test')}
    trajectories=[];selected_vectors={};compliant_vectors={}
    for call,score in zip(calls,scores):
        assert all(call[k]==score[k] for k in ('initial','policy','repeat'))
        initial,policy,repeat=(call[k] for k in ('initial','policy','repeat'))
        relative=Path(initial)/policy/f'r{repeat}';source=run/relative;dest=out/relative
        publish_candidate(source,dest);cost=coding_cost(source/'coding')
        row={**call,'cost':cost,'test':score,'complete_effective_improvement':bool(call['selected'] and score['heldout_multiobjective_guard'])}
        key=(initial,policy,repeat);selected_vectors[key]=np.zeros(len(cases['test']));compliant_vectors[key]=np.zeros(len(cases['test']))
        for split in ('selection','test'):
            if call['status']!='evaluated':continue
            before=baselines[initial][split];after=read(source/split/'summary.json')
            row[split+'_comparison']=split_comparison(before,after)
            if after['runtime_errors']==0 and (initial,split) in before_groups:
                _,groups,_=load_split(source/split,cases[split]);bg=before_groups[initial,split]
                row[split+'_cluster_statistics']=cluster_statistics(bg,groups,indices[split])
            else:row[split+'_cluster_statistics']={'unavailable':'Incomplete runtime outputs; original score and rejection retained'}
            if split=='test' and call['selected']:
                vector=np.array([np.mean([read(run/initial/'baseline/test'/f'{i:02d}_{r}.json')['result']['quality']['preference_error_m']-
                    read(source/'test'/f'{i:02d}_{r}.json')['result']['quality']['preference_error_m'] for r in range(4)]) for i in range(len(cases['test']))])
                assert abs(vector.mean()-score['selected_preference_improvement_m'])<1e-10
                selected_vectors[key]=vector
                if row['complete_effective_improvement']:compliant_vectors[key]=vector
        trajectories.append(row)
    save(out/'trajectories.json',trajectories)
    groups={}
    for policy in ('M2','M3'):
        rows=[r for r in trajectories if r['policy']==policy];effective=sum(r['complete_effective_improvement'] for r in rows)
        selected=np.mean([selected_vectors[r['initial'],policy,r['repeat']] for r in rows],axis=0)
        compliant=np.mean([compliant_vectors[r['initial'],policy,r['repeat']] for r in rows],axis=0)
        actual=None if any(r['cost']['actual_total_tokens'] is None for r in rows) else sum(r['cost']['actual_total_tokens'] for r in rows)
        known=sum(r['cost']['known_tokens'] for r in rows);wall=sum(r['cost']['wall_seconds'] for r in rows)
        groups[policy]={'logical_tasks':len(rows),'completed_coding':sum(r['cost']['status']=='completed' for r in rows),
            'evaluated':sum(r['status']=='evaluated' for r in rows),'selected':sum(r['selected'] for r in rows),
            'complete_effective_improvements':effective,'complete_effective_rate':effective/len(rows),
            'MetaImp@1_preference_m':statistics.mean(r['test']['selected_preference_improvement_m'] for r in rows),
            'trajectory_sample_stdev_m':statistics.stdev(r['test']['selected_preference_improvement_m'] for r in rows),
            'MetaImp_condition_bootstrap_95_m':np.quantile(selected[indices['test']].mean(axis=1),[.025,.975]).tolist(),
            'compliant_gain_mean_m':float(compliant.mean()),
            'compliant_condition_bootstrap_95_m':np.quantile(compliant[indices['test']].mean(axis=1),[.025,.975]).tolist(),
            'service_requests':sum(r['cost']['service_requests'] for r in rows),
            'cli_attempts':sum(len(r['cost']['attempts']) for r in rows),
            'retry_wait_seconds':sum(r['cost']['retry_wait_seconds'] for r in rows),
            'unknown_usage_requests':sum(r['cost']['unknown_usage_requests'] for r in rows),
            'known_token_lower_bound':known,'actual_total_tokens':actual,
            'charged_tokens_including_unknown_reservations':sum(r['cost']['charged_tokens'] for r in rows),
            'coding_wall_seconds':wall,'evaluation_wall_seconds_sum':sum(sum(r.get('evaluation_wall_seconds',{}).values()) for r in rows),
            'tokens_per_effective_improvement':actual/effective if effective and actual is not None else None,
            'tokens_per_effective_improvement_known_lower_bound':known/effective if effective else None,
            'coding_seconds_per_effective_improvement':wall/effective if effective else None}
    paired=[]
    for initial in INITIALS:
        for repeat in (0,1):
            pair={r['policy']:r for r in trajectories if r['initial']==initial and r['repeat']==repeat}
            paired.append({'initial':initial,'repeat':repeat,'both_evaluated':all(r['status']=='evaluated' for r in pair.values()),
                'M2_effective':pair['M2']['complete_effective_improvement'],'M3_effective':pair['M3']['complete_effective_improvement'],
                'M3_minus_M2_selected_gain_m':pair['M3']['test']['selected_preference_improvement_m']-pair['M2']['test']['selected_preference_improvement_m']})
    save(out/'summary.json',{'groups':groups,'paired':paired,'baselines':baselines,'historical_M3_creation_tokens':77723,
        'historical_creation_included_in_downstream_cost':False,'monetary_cost':None,'K':1,'M4_generated':False,
        'statistics_scope':'20000 paired condition-cluster draws with fixed model samples; no statistical significance claim across meta-agent samples',
        'complete_effective_definition':'Validation selected AND unchanged private-test multiobjective guard passes',
        'runtime_cost_scope':'Coding includes retries and waits; evaluation wall sums can overlap, excludes shared baseline preparation and historical creation'})
    hidden=[c['task'] for split in ('selection','test') for c in cases[split]]
    seeds=[s for split in ('selection','test') for c in cases[split] for s in (c['seed'],*c['algorithm_seeds'])]
    dev=[c['task'] for c in cases['dev']];files=[p for p in out.rglob('*') if p.is_file()]
    for path in files:
        text=path.read_text()
        assert not any(str(seed) in text for seed in seeds),path
        assert not CREDENTIAL.search(text),path
        if path.suffix=='.json':
            assert all(packet in dev for packet in task_packets(json.loads(text))),path
            assert '"reference":' not in text,path
    save(out/'privacy.json',{'files_checked':len(files),'private_conditions':len(hidden),'hidden_task_matches':0,
        'hidden_seed_text_matches':0,'credential_pattern_matches':0,'references_published':False,'method':'Direct structured/text comparisons; no hashes'})
    print(json.dumps(groups),flush=True)


if __name__=='__main__':main()
