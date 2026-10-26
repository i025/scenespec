"""Replay published DEV traces only: no inference, candidate execution or Mesh queries."""
import argparse
import json
import math
from pathlib import Path
import statistics
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from analyze_continuous_benchmark import distance
from scene_rsi.continuous.baselines import state
from scene_rsi.continuous.meta_experiment import eligible
from scene_rsi.meta.coding import save


def read(path):return json.loads(path.read_text())
def mean(values):return statistics.mean(values) if values else None


def best_indices(trace):
    best=0;indices=[]
    for i,row in enumerate(trace):
        if row['optimizer_loss']<trace[best]['optimizer_loss']-1e-8:best=i
        indices.append(best)
    return indices


def boundary(task,values):
    assets={o['id']:task['spec']['assets'][o['asset']] for o in task['spec']['required_objects']}
    result=[]
    for name,(x0,y0,x1,y1) in task['zones'].items():
        x,y,yaw=values[name];a=assets[name]
        ex=(abs(math.cos(yaw))*a['width']+abs(math.sin(yaw))*a['depth'])/2+.01
        ey=(abs(math.sin(yaw))*a['width']+abs(math.cos(yaw))*a['depth'])/2+.01
        result.append(min(abs(x-x0-ex),abs(x-x1+ex),abs(y-y0-ey),abs(y-y1+ey))<1e-5)
    return mean(result)


def projected(pair,slot,trace,q,task):
    if q==0:return False
    if (pair,slot)!=(0,1):return q%3==1
    prior=trace[best_indices(trace)[q-1]]
    failed=[name for f in prior['failures'] for name in f['objects'] if name in task['supports']]
    return q%3!=0 and not failed


def analyze(folder,cases,label,pair,slot=None):
    curves=[[] for _ in range(24)];alternatives={'min_optimizer':[],'first_query_valid':[],'last_query_valid':[],'min_preference_query_valid':[]}
    groups={'projection_both':[],'projection_neither':[],'mixed':[]};contacts=[]
    for i,case in enumerate(cases):
        task=case['task'];rows=[read(folder/f'{i:02d}_{repeat}.json') for repeat in (0,1)]
        traces=[r['trace'] for r in rows];indices=[best_indices(t) for t in traces]
        assert all(len(t)==24 for t in traces)
        states=[[state(task,q['values']) for q in t] for t in traces]
        assert all(states[k][indices[k][-1]]==rows[k]['state'] for k in (0,1))
        contacts.extend(boundary(task,traces[k][indices[k][-1]]['values']) for k in (0,1))
        for q in range(24):
            proposal=distance(task,states[0][q],states[1][q]);winner=distance(task,states[0][indices[0][q]],states[1][indices[1][q]])
            record={'proposal_position_m':proposal['position_rms_m'],'proposal_rotation_deg':proposal['rotation_mean_deg'],
                    'incumbent_position_m':winner['position_rms_m'],'incumbent_rotation_deg':winner['rotation_mean_deg'],
                    'proposal_preference_m':mean([t[q]['quality']['preference_error_m'] for t in traces]),
                    'incumbent_preference_m':mean([traces[k][indices[k][q]]['quality']['preference_error_m'] for k in (0,1)]),
                    'proposal_zone_contact_fraction':mean([boundary(task,t[q]['values']) for t in traces])}
            curves[q].append(record)
            if slot is not None:
                flags=[projected(pair,slot,t,q,task) for t in traces]
                groups['projection_both' if all(flags) else 'projection_neither' if not any(flags) else 'mixed'].append(record)
        for method in alternatives:
            chosen=[]
            for k,t in enumerate(traces):
                valid=[j for j,r in enumerate(t) if not r['failures']]
                j=(indices[k][-1] if method=='min_optimizer' or not valid else valid[0] if method=='first_query_valid'
                   else valid[-1] if method=='last_query_valid' else min(valid,key=lambda j:t[j]['quality']['preference_error_m']))
                chosen.append(j)
            delta=distance(task,*[states[k][chosen[k]] for k in (0,1)])
            alternatives[method].append({'position_m':delta['position_rms_m'],'rotation_deg':delta['rotation_mean_deg'],
                   'preference_m':mean([traces[k][chosen[k]]['quality']['preference_error_m'] for k in (0,1)]),
                   'query_valid_outputs':sum(not traces[k][chosen[k]]['failures'] for k in (0,1))})
    averaged=[{'query':q,**{key:mean([r[key] for r in records]) for key in records[0]}} for q,records in enumerate(curves)]
    return {'pair':pair,'algorithm':label,'dev_summary':read(folder/'summary.json'),
          'proposal_mean_position_m':mean([r['proposal_position_m'] for r in averaged]),
          'incumbent_mean_position_m':mean([r['incumbent_position_m'] for r in averaged]),
          'selected_zone_contact_fraction':mean(contacts),'curves':averaged,
          'projection_slot_groups':{key:{'paired_queries':len(values),**({k:mean([r[k] for r in values]) for k in values[0]} if values else {})} for key,values in groups.items()},
          'fixed_trace_selector_replays':{key:{k:mean([r[k] for r in values]) for k in values[0]} for key,values in alternatives.items()}}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=False);old=ROOT/'experiments/paired_repair/results';reports=[];examples=[]
    for pair in range(2):
        folder=old/f'pair{pair}';cases=read(folder/'dev_cases.json')
        baseline=analyze(folder/'baseline_repair/dev',cases,'Repair',pair);reports.append(baseline)
        for slot,policy in enumerate(('M0','M1')):
            result=analyze(folder/f'c{slot}/dev',cases,policy,pair,slot);reports.append(result)
            before,after=baseline['dev_summary'],result['dev_summary']
            examples.append({'packet':{'historical_pair':pair,'policy':policy},'valid':after['runtime_errors']==0,
               'solvable':eligible(before,after),'error':'dev_multiobjective_rejected','quality':{'preference_error_m':after['preference_error_m']},
               'multiobjective':{'baseline':before,'candidate':after,'position_retention':after['diversity_position_rms_m']/before['diversity_position_rms_m'],
                  'rotation_retention':after['diversity_rotation_deg']/before['diversity_rotation_deg'],
                  'preference_improvement_m':before['preference_error_m']-after['preference_error_m'],
                  'proposal_mean_position_m':result['proposal_mean_position_m'],'selected_zone_contact_fraction':result['selected_zone_contact_fraction'],
                  'fixed_trace_selector_replays':result['fixed_trace_selector_replays']}})
    save(args.out/'trace_analysis.json',{'scope':'prior published dev only; reconstructed saved values, no new geometry queries',
         'model_calls':0,'candidate_executions':0,'mesh_queries':0,'reports':reports,
         'limitation':'alternative selectors replay the same adaptive proposal stream; they do not identify a causal search-policy ablation or final physics success'})
    save(args.out/'training_feedback.json',{'summary':{'tasks':4,'valid':4,'valid_rate':1.,'solvable':0,'solvable_rate':0.,
          'multiobjective':{'attempts':4,'dev_accepted':0,'position_retention_mean':mean([x['multiobjective']['position_retention'] for x in examples]),
                            'rotation_retention_mean':mean([x['multiobjective']['rotation_retention'] for x in examples])}},
          'examples':examples,'definitions':{'valid':'candidate ran without errors','solvable':'candidate met unchanged DEV multiobjective eligibility',
             'selector_replays':'query-only counterfactuals, no final physics or extra generation','feedback_scope':'DEV ONLY; no validation/test or manual repair recipe'}})
    print('analyzed four candidates and two baselines using public dev traces; model calls 0',flush=True)


if __name__=='__main__':main()
