"""Balanced frozen M2/M3 experiment; bounded capacity retries, no scoring changes."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from run_evidence_m3 import evaluate4,read,request_for
from run_m3_validation import POLICIES
from run_native_continuous import candidate
from run_paired_repair import BUDGET
from scene_rsi.continuous.tasks import FAMILIES,make_task
from scene_rsi.continuous.geometry import MeshWorld
from scene_rsi.continuous.meta_experiment import eligible
from scene_rsi.meta.coding import git,run_codex,save

EXP=ROOT/'experiments/m3_reliable'
INITIALS=('axis_target','elite_centroid')
BACKOFF=(30,60)


def slots():
    a,b=INITIALS
    return [(a,0,'M2'),(b,0,'M3'),(b,0,'M2'),(a,0,'M3'),
            (b,1,'M2'),(a,1,'M3'),(a,1,'M2'),(b,1,'M3')]


def capacity_only(result,folder,source,requests):
    if result['status']!='failed' or result.get('patch') or len(requests)!=1:return False
    row=requests[0]
    if row.get('index')!=0 or row.get('http_status')!=429 or row.get('usage') is not None:return False
    error=read(folder/'response_0.sse').get('error',{})
    if error.get('code')!='turn_rate_limit_exceeded' or 'at capacity' not in error.get('message',''):return False
    if (folder/'candidate.diff').exists() and (folder/'candidate.diff').read_text().strip():return False
    if (folder/'candidate.py').exists() and (folder/'candidate.py').read_text() not in ('',source):return False
    events=[json.loads(line) for line in (folder/'events.jsonl').read_text().splitlines()]
    return all(e['type'] in ('thread.started','turn.started','error','turn.failed') for e in events)


def run_logical(task,directory,backend,*,sleep=time.sleep,clock=time.monotonic):
    directory.mkdir(parents=True,exist_ok=False);started=clock()
    record={'attempts':[],'service_requests':0,'input_bytes_total':0,'known_tokens':0,
            'unknown_usage_requests':0,'charged_tokens':0,'retry_wait_seconds':0,'status':'failed'}
    result={'status':'budget_exhausted','patch':'','summary':'','usage':None}
    for attempt in range(3):
        remaining=dict(BUDGET,wall_seconds=int(BUDGET['wall_seconds']-(clock()-started)),
            model_requests=BUDGET['model_requests']-record['service_requests'],
            input_bytes_total=BUDGET['input_bytes_total']-record['input_bytes_total'],
            total_tokens=BUDGET['total_tokens']-record['charged_tokens'])
        if min(remaining[k] for k in ('wall_seconds','model_requests','input_bytes_total','total_tokens'))<=0:
            result=dict(result,status='budget_exhausted',patch='');break
        folder=directory/f'attempt{attempt}'
        save(directory/'logical.json',{**record,'next_attempt':attempt,'remaining_budget':remaining})
        try:result=backend(task,folder,budget=remaining)
        except Exception as error:
            result={'status':'backend_exception','patch':'','summary':type(error).__name__,'usage':None}
        calls=read(folder/'model_calls.json') if (folder/'model_calls.json').exists() else {'requests':[],'input_bytes_total':0}
        requests=calls['requests'];charge=0
        for row in requests:
            if row.get('usage') is not None:
                charge+=row['usage']['total_tokens'];record['known_tokens']+=row['usage']['total_tokens']
            else:
                charge+=row['token_reservation'];record['unknown_usage_requests']+=1
        record['charged_tokens']+=charge;record['service_requests']+=len(requests)
        record['input_bytes_total']+=calls['input_bytes_total']
        within=(record['service_requests']<=BUDGET['model_requests'] and record['charged_tokens']<=BUDGET['total_tokens']
                and record['input_bytes_total']<=BUDGET['input_bytes_total'] and clock()-started<=BUDGET['wall_seconds'])
        retryable=capacity_only(result,folder,task['files']['algorithm.py'],requests) if requests else False
        record['attempts'].append({'directory':folder.name,'status':result['status'],'service_requests':len(requests),
            'charged_tokens':charge,'usage':result.get('usage'),'capacity_retry_eligible':retryable,'within_budget':within})
        record.update(wall_seconds=clock()-started,budget_compliant=within)
        save(directory/'logical.json',record)
        if not within:
            result=dict(result,status='budget_exhausted',patch='');break
        if result['status']=='completed':break
        if not retryable or attempt==2:break
        delay=BACKOFF[attempt]
        if clock()-started+delay>=BUDGET['wall_seconds']:
            result=dict(result,status='budget_exhausted');break
        sleep(delay);record['retry_wait_seconds']+=delay
    record.update(status=result['status'],wall_seconds=clock()-started,
                  actual_total_tokens=None if record['unknown_usage_requests'] else record['known_tokens'])
    save(directory/'logical.json',record)
    save(directory/'backend_result.json',result)
    return result


def evaluate_job(path):
    job=read(path);started=time.perf_counter()
    summary,_=evaluate4(Path(job['source']).read_text(),read(Path(job['cases']))[job['split']],
                        Path(job['out']),Path(job['run']),Path(job['geometry']))
    save(path.with_suffix('.result.json'),{'summary':summary,'wall_seconds':time.perf_counter()-started})


def run_jobs(run,geometry,jobs):
    folder=run/'jobs';folder.mkdir(exist_ok=True)
    paths=[]
    for label,source,split,out in jobs:
        path=folder/(label+'.json');assert not path.exists()
        save(path,{'source':str(source),'cases':str(run/'private_tasks.json'),'split':split,
                   'out':str(out),'run':str(run),'geometry':str(geometry)});paths.append(path)
    def launch(path):
        with path.with_suffix('.log').open('w') as log:
            subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'--phase','worker','--job',str(path)],
                           stdout=log,stderr=subprocess.STDOUT,check=True)
        return read(path.with_suffix('.result.json'))
    # Separate trusted controller processes keep CPU accounting independent. Candidates still use LayoutProcess only.
    with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(launch,paths))


def prepare(run,geometry):
    run.mkdir(exist_ok=False);run.chmod(0o700)
    rng=secrets.SystemRandom();used=set();cases={};references=[]
    def fresh(bits):
        while True:
            value=rng.getrandbits(bits)
            if value not in used:used.add(value);return value
    for split in ('dev','selection','test'):
        cases[split]=[]
        for i in range(24):
            seed=fresh(64);task,reference=make_task(seed,FAMILIES[i%4],f'reliable_{split}_{i}')
            world=MeshWorld(task,geometry)
            try:report=world.evaluate(reference,final=True)
            finally:world.close()
            references.append({'split':split,'success':report['success']})
            cases[split].append({'seed':seed,'task':task,'reference':reference,'algorithm_seeds':[fresh(32) for _ in range(4)]})
    save(run/'private_tasks.json',cases);save(run/'reference_acceptance.json',references)
    assert all(r['success'] for r in references),'invalid reference; no score-dependent resampling'
    jobs=[(f'baseline_{name}_{split}',EXP/f'initial_{name}.py',split,run/name/'baseline'/split)
          for name in INITIALS for split in ('dev','selection')]
    results=run_jobs(run,geometry,jobs)
    assert all(r['summary']['runtime_errors']==0 and r['summary']['queries_min']==r['summary']['queries_max']==24 for r in results)
    for name in INITIALS:
        source=(EXP/f'initial_{name}.py').read_text();folder=run/name
        rows=[read(folder/'baseline/dev'/f'{i:02d}_{r}.json') for i in range(24) for r in range(4)]
        for policy in POLICIES:
            for repeat in range(2):
                dest=folder/policy/f'r{repeat}';dest.mkdir(parents=True)
                task,_=request_for(source,cases['dev'],rows,POLICIES[policy].read_text(),dest/'policy',run,policy=='M3')
                save(dest/'task.json',task)
                assert len(task['feedback']['examples'])==8 and task['budget']==BUDGET
                if repeat:assert read(dest/'task.json')==read(folder/policy/'r0/task.json')
        assert read(folder/'M2/r0/task.json')['feedback']['metrics']==read(folder/'M3/r0/task.json')['feedback']['metrics']
    save(run/'calls.json',[{'initial':i,'repeat':r,'policy':p,'K':1,'status':'reserved','selected':False} for i,r,p in slots()])
    save(run/'prepared.json',{'project_git':git(ROOT,'rev-parse','HEAD'),'references_passed':len(references),
        'conditions_each_split':24,'families_each_split':{f:6 for f in FAMILIES},'seeds_each_condition':4,
        'distinct_task_and_algorithm_seeds':len(used),'baseline_dev_selection':results,
        'research_model_calls':0,'test_algorithm_scores_computed':False,'coding_concurrency':1,'evaluation_workers':2})
    print('stage 1 complete: 72 legal conditions, disjoint seeds, four baseline evaluations, zero proposals',flush=True)


def execute(run,geometry,backend):
    frozen=read(run/'frozen.json');assert frozen['git']==git(ROOT,'rev-parse','HEAD')
    assert not git(ROOT,'status','--porcelain')
    assert not (run/'started.json').exists()
    save(run/'started.json',{'logical_tasks':8,'K':1,'coding_concurrency':1});started=time.perf_counter()
    calls=read(run/'calls.json');assert [(c['initial'],c['repeat'],c['policy']) for c in calls]==slots()
    for c in calls:
        folder=run/c['initial']/c['policy']/f'r{c["repeat"]}'
        try:
            result,source=candidate(read(folder/'task.json'),folder,partial(run_logical,backend=backend))
            if source is None:c.update(status='candidate_failed',error=result['status'])
            else:(folder/'source.py').write_text(source);c.update(status='coded',static_audit=True)
        except Exception as error:c.update(status='candidate_failed',error=type(error).__name__+': '+str(error)[:300])
        save(folder/'record.json',c);save(run/'calls.json',calls)
        print(c['initial'],c['policy'],c['repeat'],c['status'],flush=True)
    coded=[c for c in calls if c['status']=='coded']
    def folder(c):return run/c['initial']/c['policy']/f'r{c["repeat"]}'
    jobs=[(f'selection_{i}',folder(c)/'source.py','selection',folder(c)/'selection') for i,c in enumerate(coded)]
    for c,result in zip(coded,run_jobs(run,geometry,jobs)):
        c.update(status='evaluated',selection=result['summary'],evaluation_wall_seconds={'selection':result['wall_seconds']},
                 selected=eligible(read(run/c['initial']/'baseline/selection/summary.json'),result['summary']))
        save(folder(c)/'record.json',c)
    save(run/'calls.json',calls)
    save(run/'proposals_complete.json',{'all_eight_closed':True,'selection_fixed':True,'test_feedback_exposed':False})
    print('all selection decisions fixed; beginning private test',flush=True)
    jobs=[(f'baseline_test_{name}',EXP/f'initial_{name}.py','test',run/name/'baseline/test') for name in INITIALS]
    jobs += [(f'test_{i}',folder(c)/'source.py','test',folder(c)/'test') for i,c in enumerate(coded)]
    evaluated=run_jobs(run,geometry,jobs);scores=[]
    for c,result in zip(coded,evaluated[len(INITIALS):]):
        c['evaluation_wall_seconds']['test']=result['wall_seconds'];save(folder(c)/'record.json',c)
    for c in calls:
        before=read(run/c['initial']/'baseline/test/summary.json')
        raw=read(folder(c)/'test/summary.json') if c['status']=='evaluated' else None
        chosen=raw if c['selected'] else before
        scores.append({**{k:c[k] for k in ('initial','repeat','policy','selected')},'before':before,'raw_candidate':raw,
            'selected_after':chosen,'heldout_multiobjective_guard':eligible(before,raw) if raw else False,
            'selected_preference_improvement_m':before['preference_error_m']-chosen['preference_error_m']})
    save(run/'calls.json',calls);save(run/'scores.json',scores)
    save(run/'finished.json',{'wall_seconds':time.perf_counter()-started,'logical_tasks':8,'K':1,'M4_generated':False})
    print('eight logical tasks closed; private tests complete; no reselection',flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--phase',choices=('prepare','freeze','execute','worker'),required=True)
    for key in ('run','geometry','guard','security','editor-security','job'):p.add_argument('--'+key,type=Path)
    args=p.parse_args()
    if args.phase=='worker':evaluate_job(args.job);return
    assert all(x['passed'] for x in read(args.security/'acceptance.json'))
    assert read(args.editor_security/'acceptance.json')['native_patch_applied']
    if args.phase=='prepare':prepare(args.run,args.geometry)
    elif args.phase=='freeze':
        assert (args.run/'prepared.json').exists() and not git(ROOT,'status','--porcelain')
        assert read(args.run.parent/'checks/scheduler_acceptance.json')['passed']
        save(args.run/'frozen.json',{'git':git(ROOT,'rev-parse','HEAD'),'stage1_passed':True,'stage2_offline_tests_passed':True})
    else:
        backend=partial(run_codex,runtime=args.run.parent/'runtime',guard=args.guard,parent_git=git(ROOT,'rev-parse','HEAD'))
        execute(args.run,args.geometry,backend)


if __name__=='__main__':main()
