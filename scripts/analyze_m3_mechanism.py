"""Read saved Elite/r1 inputs, code and traces; no model, candidate or physics runs."""
import argparse
import ast
from itertools import combinations
import json
from pathlib import Path
import statistics
import sys
import time

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from analyze_continuous_benchmark import distance,integers,task_packets
from analyze_m3_selection import load_split,split_comparison
from run_m3_reliable import retry_after_seconds,stream_overload
from scene_rsi.continuous.meta_experiment import values_state
from scene_rsi.meta.coding import save

PUBLIC=ROOT/'experiments/m3_reliable/results/elite_centroid'


def read(path):return json.loads(path.read_text())


def inputs():
    result={}
    for policy in ('M2','M3'):
        folder=PUBLIC/policy/'r1';task=read(folder/'task.json')
        delivered=read(folder/'coding/attempt0/TASK.json')
        assert delivered=={k:v for k,v in task.items() if k not in ('files','contract')}
        events=[json.loads(line) for line in (folder/'coding/attempt0/events.jsonl').read_text().splitlines()]
        reads=[e['item']['command'] for e in events if e['type']=='item.completed'
               and e['item']['type']=='command_execution' and e['item']['exit_code']==0]
        assert any('cat algorithm.py TASK.json CONTRACT.md' in cmd for cmd in reads)
        examples=task['feedback']['examples'];groups=[]
        for index in sorted({e['condition_index'] for e in examples}):
            group=[e for e in examples if e['condition_index']==index]
            assert {e['repeat'] for e in group}==set(range(4))
            groups.append({'dev_condition_index':index,'family':group[0]['packet']['family'],
                           'repeats':4,'pair_metrics':group[0]['pair_metrics']})
        code=folder/'coding/attempt0/candidate.py'
        definitions={node.name:node.lineno for node in ast.parse(code.read_text()).body if isinstance(node,ast.FunctionDef)}
        result[policy]={'choose':read(folder/'policy/choose/response.json'),
            'propose':read(folder/'policy/propose/response.json'),
            'current':task['feedback']['metrics']['current'],'history':task['feedback']['metrics']['history'],
            'constraints':task['feedback']['constraints'],'probe':task['feedback']['selected_probe'],
            'delivered_groups':groups,'actual_task_matches':True,'successful_read_commands':reads,
            'code':str(code.relative_to(ROOT)),'function_lines':definitions,
            'diff':str((folder/'coding/attempt0/candidate.diff').relative_to(ROOT)),
            'budget':task['budget'],'coding_cost':read(folder/'coding/logical.json')}
    assert result['M2']['current']==result['M3']['current']
    assert result['M2']['history']==result['M3']['history']
    assert result['M2']['budget']==result['M3']['budget']
    return result


def trace_statistics(cases,rows):
    """Same-query and best-prefix spreads are diagnostics, not new validity scores."""
    query=[];incumbent=[];indices=[]
    for i,case in enumerate(cases):
        group=[r for r in rows if r['task_index']==i]
        assert len(group)==4 and all(len(r['trace'])==24 and r['error'] is None for r in group)
        states=[[values_state(case['task'],q['values']) for q in row['trace']] for row in group]
        best=[[min(range(q+1),key=lambda j:row['trace'][j]['optimizer_loss']) for q in range(24)] for row in group]
        for r,row in enumerate(group):
            assert states[r][best[r][-1]]==row['state']
            indices.append(best[r][-1])
        for q in range(24):
            for target,selected in ((query,[q]*4),(incumbent,[b[q] for b in best])):
                pairs=[distance(case['task'],states[a][selected[a]],states[b][selected[b]]) for a,b in combinations(range(4),2)]
                target.append({'query':q,'position_rms_m':statistics.mean(p['position_rms_m'] for p in pairs),
                    'rotation_deg':statistics.mean(p['rotation_mean_deg'] for p in pairs),
                    'preference_error_m':statistics.mean(row['trace'][j]['quality']['preference_error_m'] for row,j in zip(group,selected)),
                    'visibility_mean':statistics.mean(row['trace'][j]['quality']['visibility_mean'] for row,j in zip(group,selected))})
    phases={}
    for label,start in (('q0_7',0),('q8_15',8),('q16_23',16)):
        phases[label]={}
        for name,values in (('proposal',query),('best_prefix',incumbent)):
            selected=[v for v in values if start<=v['query']<start+8]
            phases[label][name]={key:statistics.mean(v[key] for v in selected) for key in selected[0] if key!='query'}
    return {'conditions':len(cases),'runs':len(rows),'phases':phases,
            'final_selected_query_histogram':[indices.count(i) for i in range(24)],
            'selected_in_last_eight':sum(i>=16 for i in indices),
            'final_argmin_reconstructed_matches':len(indices)}


def condition_association(before,after):
    gain=np.array([a['preference_error_m']-b['preference_error_m'] for a,b in zip(before,after)])
    spread=np.array([b['diversity_position_rms_m']-a['diversity_position_rms_m'] for a,b in zip(before,after)])
    return {'conditions':len(before),'preference_improved_conditions':int((gain>0).sum()),
            'position_decreased_conditions':int((spread<0).sum()),
            'both_conditions':int(((gain>0)&(spread<0)).sum()),
            'preference_gain_vs_position_change_pearson':float(np.corrcoef(gain,spread)[0,1])
                if np.std(gain)>0 and np.std(spread)>0 else None,
            'scope':'Descriptive association within saved conditions, not causal or independent model replications'}


def service_evidence():
    from datetime import datetime,timezone
    rows=[]
    for initial,policy,repeat in (('elite_centroid','M3',0),('axis_target','M2',1)):
        root=PUBLIC.parent/initial/policy/f'r{repeat}';folder=root/'coding/attempt0'
        task=read(root/'task.json');result=read(folder/'backend_result.json')
        calls=read(folder/'model_calls.json');logical=read(root/'coding/logical.json')
        error=stream_overload(result,folder,task['files']['algorithm.py'],calls['requests'])
        assert error is not None
        delay=retry_after_seconds(error['headers'],datetime.now(timezone.utc))
        rows.append({'initial':initial,'policy':policy,'repeat':repeat,'http_status':200,**error,
            'retry_after_seconds':delay,'historical_retries':len(logical['attempts'])-1,
            'historical_retry_eligible':logical['attempts'][0]['capacity_retry_eligible'],
            'future_opt_in_error_class_matches':True,
            'remaining_wall_after_first_wait_seconds':task['budget']['wall_seconds']-logical['wall_seconds']-delay,
            'unknown_usage_requests':logical['unknown_usage_requests'],
            'charged_tokens':logical['charged_tokens'],
            'retry_success_unknown':True,'new_requests_sent':0})
    return rows


def analyze(run,out):
    started=time.perf_counter();out.mkdir(parents=True,exist_ok=False)
    cases=read(run/'private_tasks.json');save(out/'inputs.json',inputs())
    save(out/'service_replay.json',service_evidence());metrics={};traces={}
    for split in ('selection','test'):
        paths={'baseline':run/'elite_centroid/baseline'/split,
               **{p:run/'elite_centroid'/p/'r1'/split for p in ('M2','M3')}}
        loaded={name:load_split(folder,cases[split]) for name,folder in paths.items()}
        baseline,bg,bf=loaded['baseline'];metrics[split]={'baseline':baseline};traces[split]={}
        rows={name:[read(folder/f'{i:02d}_{r}.json') for i in range(24) for r in range(4)] for name,folder in paths.items()}
        for name,group in rows.items():
            traces[split][name]=trace_statistics(cases[split],group)
            if name!='baseline':
                equal=[]
                for start in (0,8,16):
                    equal.append(sum(row['trace'][q]==base['trace'][q] for row,base in zip(group,rows['baseline']) for q in range(start,start+8)))
                traces[split][name]['trace_entries_equal_to_baseline_by_phase']=equal
                after,ag,af=loaded[name]
                metrics[split][name]={**split_comparison(baseline,after),
                    'condition_association':condition_association(bg,ag),
                    'families':{f:split_comparison(bf[f],af[f]) for f in bf}}
        metrics[split]['M3_minus_M2']={key:loaded['M3'][0][key]-loaded['M2'][0][key] for key in (
            'preference_error_m','diversity_position_rms_m','valid_pair_diversity_m',
            'diversity_rotation_deg','visibility_mean','successes')}
    save(out/'metrics.json',metrics);save(out/'traces.json',traces)
    # New output contains public dev evidence and split/family aggregates only.
    hidden=[c for split in ('selection','test') for c in cases[split]]
    seeds={s for c in hidden for s in (c['seed'],*c['algorithm_seeds'])}
    dev=[c['task'] for c in cases['dev']]
    for path in out.glob('*.json'):
        document=read(path)
        assert all(packet in dev for packet in task_packets(document)),path
        assert not seeds.intersection(integers(document)),path
        assert '"reference":' not in path.read_text(),path
    save(out/'execution.json',{'source_git':'f36b91e','model_calls':0,'candidate_executions':0,'physics_evaluations':0,
        'seconds':time.perf_counter()-started,'private_conditions_checked':len(hidden),
        'hidden_packets_or_seeds_published':False,'final_test_used_only_post_hoc':True,
        'method':'Saved trace coordinate conversion and original statistics, no candidate imports or execution'})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();analyze(args.run,args.out)
