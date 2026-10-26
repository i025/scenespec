"""Eight preregistered K=1 M2/M3 trajectories; independent dev-only ablations."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import json
from pathlib import Path
import secrets
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from run_evidence_m3 import evaluate4,read,request_for
from run_paired_repair import BUDGET
from run_native_continuous import candidate
from scene_rsi.continuous.tasks import FAMILIES,make_task
from scene_rsi.continuous.geometry import MeshWorld
from scene_rsi.continuous.meta_experiment import eligible
from scene_rsi.meta.coding import git,run_codex,save

EXP=ROOT/'experiments/m3_validation'
INITIALS=('sequential_projection','shared_shift')
POLICIES={'M2':ROOT/'experiments/m2_failure/M2.py','M3':ROOT/'experiments/evidence_m3/M3.py'}


def slots():
    # Reverse model submission order in the second pair, with no outcome-dependent scheduling.
    return [(i,r,m) for i in INITIALS for r in range(2) for m in (('M2','M3') if r==0 else ('M3','M2'))]


def conditions(folder,geometry,counts):
    folder.mkdir();rng=secrets.SystemRandom();cases={};checks=[]
    for split,count in counts.items():
        cases[split]=[]
        for i in range(count):
            seed=rng.getrandbits(64)
            task,reference=make_task(seed,FAMILIES[i%len(FAMILIES)],folder.name+'_'+split+'_'+str(i))
            world=MeshWorld(task,geometry)
            try:result=world.evaluate(reference,final=True)
            finally:world.close()
            checks.append({'split':split,'success':result['success']})
            cases[split].append({'seed':seed,'task':task,'reference':reference,
                                'algorithm_seeds':[rng.getrandbits(32) for _ in range(4)]})
    save(folder/'private_tasks.json',cases);save(folder/'reference_acceptance.json',checks)
    if not all(x['success'] for x in checks):raise RuntimeError('illegal reference; no resampling')
    return cases


def prepare(run,geometry):
    run.mkdir(exist_ok=False);run.chmod(0o700)
    save(run/'freeze.json',{'base':'2bb85f6','project_git':git(ROOT,'rev-parse','HEAD'),
         'policies':{k:str(v.relative_to(ROOT)) for k,v in POLICIES.items()},
         'initials':list(INITIALS),'K':1,'repeats':2,'test_feedback_exposed':False})
    save(run/'reservation.json',{'formal_coding_tasks':8,'ablation_coding_tasks_max':2,
         'budget':BUDGET,'examples_per_task':8,'shared_seeds_per_condition':4,
         'M3_creation_tokens_historical':77723,'no_M4':True})
    for name in INITIALS:
        folder=run/name;cases=conditions(folder,geometry,{'dev':6,'selection':6,'test':12})
        source=(EXP/f'initial_{name}.py').read_text()
        for split in ('dev','selection'):
            summary,rows=evaluate4(source,cases[split],folder/'baseline'/split,run,geometry)
            if summary['runtime_errors']:raise RuntimeError('initial execution failure; no source repair or resampling')
            if split=='dev':dev_rows=rows
        for repeat in range(2):
            for policy in ('M2','M3'):
                dest=folder/policy/f'r{repeat}';dest.mkdir(parents=True)
                task,choice=request_for(source,cases['dev'],dev_rows,POLICIES[policy].read_text(),dest/'policy',run,policy=='M3')
                if len(task['feedback']['examples'])!=8:raise ValueError('exactly eight policy-selected examples; no padding')
                assert task['budget']==BUDGET
                save(dest/'task.json',task)
                if repeat:assert task==read(folder/policy/'r0/task.json'),'repeat task changed'
        left=read(folder/'M2/r0/task.json');right=read(folder/'M3/r0/task.json')
        assert left['feedback']['metrics']==right['feedback']['metrics']
        save(folder/'evidence_budget.json',{'raw_dev_conditions':6,'raw_dev_runs':24,
             'delivered_examples_each':8,'same_metrics_pool':True,'repeat_tasks_identical':True,
             'feedback_bytes':{m:len(json.dumps(read(folder/m/'r0/task.json')['feedback']).encode()) for m in POLICIES},
             'controller_padded_or_truncated':False})
    dev=run/'ablation_dev';cases=conditions(dev,geometry,{'dev':6})
    evaluate4((EXP/'initial_sequential_projection.py').read_text(),cases['dev'],dev/'baseline/dev',run,geometry)
    calls=[{'initial':i,'repeat':r,'policy':m,'K':1,'status':'reserved','selected':False} for i,r,m in slots()]
    save(run/'calls.json',calls)
    print('eight fixed K=1 slots prepared; separate ablation dev ready; no coding calls',flush=True)


def plan_ablation(run):
    if (run/'started.json').exists() or (run/'ablation_plan.json').exists():
        raise RuntimeError('no replanning from formal outcomes')
    result=read(run/'offline_ablation/result.json');chosen=None
    for mechanism in ('history_separation','probe_delivery','constraint_margins'):
        if result['comparisons']['actual_dev'][mechanism]['nontext_behavior_changed']:
            records=result['actual_dev']
            if all(records[k]['example_count']==8 for k in ('full',mechanism)):
                chosen=mechanism;break
    calls=[]
    if chosen:
        for arm,key in (('full','full'),('removed',chosen)):
            folder=run/'ablations'/arm;folder.mkdir(parents=True)
            task=read(Path(result['actual_dev'][key]['coding_task_path']))
            assert task['budget']==BUDGET and len(task['feedback']['examples'])==8
            save(folder/'task.json',task)
            calls.append({'arm':arm,'mechanism':chosen,'directory':'ablations/'+arm,
                          'status':'reserved','selected':False})
    save(run/'ablation_calls.json',calls)
    save(run/'ablation_plan.json',{'selected_mechanism':chosen,'coding_tasks':len(calls),
         'selection_rule':'first actual-dev objective/status/probe/membership change in history,probe,margins order; eight examples',
         'formal_outcomes_used':False,'no_selection_or_test_for_ablation':True})
    print('dev-only ablation slots',len(calls),chosen,flush=True)


def propose(task,folder,backend):
    try:
        result,source=candidate(task,folder,backend)
        if source is None or result.get('usage') is None or result['usage']['total_tokens']>BUDGET['total_tokens']:
            return None,result.get('status','failed')+': no complete budget-compliant patch'
        return source,None
    except Exception as error:return None,type(error).__name__+': '+str(error)[:700]


def execute(run,geometry,backend):
    if (run/'started.json').exists():raise RuntimeError('no implicit restart or extra proposals')
    calls=read(run/'calls.json')
    assert [(c['initial'],c['repeat'],c['policy']) for c in calls]==slots()
    assert all(c['status']=='reserved' for c in calls)
    assert (run/'ablation_plan.json').exists(),'freeze dev-only mechanism plan before formal outcomes'
    save(run/'started.json',{'formal_coding_tasks':8,'K':1});started=time.perf_counter()
    def folder(c):return run/c['initial']/c['policy']/f'r{c["repeat"]}'
    with ThreadPoolExecutor(max_workers=2) as pool:
        proposals=list(pool.map(lambda c:propose(read(folder(c)/'task.json'),folder(c),backend),calls))
    for c,(source,error) in zip(calls,proposals):
        dest=folder(c);c['evaluation_wall_seconds']={}
        if source is None:c.update(status='candidate_failed',error=error)
        else:
            (dest/'source.py').write_text(source);cases=read(dest.parents[1]/'private_tasks.json')
            for split in ('dev','selection'):
                t=time.perf_counter();c[split],_=evaluate4(source,cases[split],dest/split,run,geometry)
                c['evaluation_wall_seconds'][split]=time.perf_counter()-t
            c.update(status='evaluated',static_audit=True,
                     selected=eligible(read(dest.parents[1]/'baseline/selection/summary.json'),c['selection']))
        save(dest/'record.json',c);save(run/'calls.json',calls)
        print(c['initial'],c['repeat'],c['policy'],c['status'],'selected',c['selected'],flush=True)
    save(run/'proposals_complete.json',{'all_eight_closed':True,'selection_fixed':True,'test_feedback_exposed':False})
    scores=[]
    for initial in INITIALS:
        base=run/initial;cases=read(base/'private_tasks.json')
        before,_=evaluate4((EXP/f'initial_{initial}.py').read_text(),cases['test'],base/'baseline/test',run,geometry)
        for c in [x for x in calls if x['initial']==initial]:
            dest=folder(c);raw=None
            if c['status']=='evaluated':
                t=time.perf_counter();raw,_=evaluate4((dest/'source.py').read_text(),cases['test'],dest/'test',run,geometry)
                c['evaluation_wall_seconds']['test']=time.perf_counter()-t
                save(dest/'record.json',c)
            chosen=raw if c['selected'] else before
            scores.append({'initial':initial,'policy':c['policy'],'repeat':c['repeat'],'selected':c['selected'],
                 'before':before,'raw_candidate':raw,'selected_after':chosen,
                 'heldout_multiobjective_guard':eligible(before,raw) if raw else False,
                 'selected_preference_improvement_m':before['preference_error_m']-chosen['preference_error_m']})
    save(run/'calls.json',calls);save(run/'scores.json',scores)
    save(run/'finished.json',{'wall_seconds':time.perf_counter()-started,'formal_coding_tasks':8,'K':1,
         'MetaImp@1_preference_m':{m:statistics.mean(s['selected_preference_improvement_m'] for s in scores if s['policy']==m) for m in POLICIES},
         'extra_formal_trials':0,'M4_generated':False})
    print('all eight formal trajectories complete; selections unchanged after test',flush=True)


def execute_ablation(run,geometry,backend):
    if not (run/'finished.json').exists():raise RuntimeError('finish immutable formal comparison first')
    if (run/'ablation_started.json').exists():raise RuntimeError('no repeated ablation draws')
    calls=read(run/'ablation_calls.json');assert len(calls) in (0,2)
    save(run/'ablation_started.json',{'tasks':len(calls),'formal_results_fixed':True})
    cases=read(run/'ablation_dev/private_tasks.json')['dev'];results=[]
    for c in calls:
        dest=run/c['directory'];source,error=propose(read(dest/'task.json'),dest,backend)
        if source is None:c.update(status='candidate_failed',error=error)
        else:
            (dest/'source.py').write_text(source)
            summary,_=evaluate4(source,cases,dest/'dev',run,geometry)
            c.update(status='evaluated',dev=summary)
        save(dest/'record.json',c);results.append(c)
        save(run/'ablation_calls.json',calls)
    save(run/'ablation_results.json',{'scope':'separate development only; one draw each; not formal capability evidence',
         'baseline':read(run/'ablation_dev/baseline/dev/summary.json'),'arms':results,'formal_result_changes':False})
    print('preplanned dev-only ablation complete; no further draws',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=('prepare','plan-ablation','execute','ablation'),required=True)
    for key in ('run','geometry','guard','security','editor-security'):parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args()
    assert all(x['passed'] for x in read(args.security/'acceptance.json'))
    assert read(args.editor_security/'acceptance.json')['native_patch_applied']
    backend=partial(run_codex,runtime=args.run.parent/'runtime',guard=args.guard,parent_git=git(ROOT,'rev-parse','HEAD'))
    if args.phase=='prepare':prepare(args.run,args.geometry)
    elif args.phase=='plan-ablation':plan_ablation(args.run)
    elif args.phase=='execute':execute(args.run,args.geometry,backend)
    else:execute_ablation(args.run,args.geometry,backend)


if __name__=='__main__':main()
