"""One native capability trial, then frozen M0/M1 x repair/joint at K=1."""
import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'scripts'))
from run_continuous_meta import CONTRACT,POLICIES,FIXTURES,prepare,request_for,bootstrap_difference
from scene_rsi.continuous.meta_experiment import audit,initial_source,evaluate,eligible
from scene_rsi.meta.coding import improvement_task,run_codex,materialize,git,save


def candidate(task,folder,backend):
    """Backend-neutral consumption: the controller uses a patch, never CLI events."""
    result=backend(task,folder/'coding')
    if result['status']!='completed': return result,None
    source=materialize(task['files']['algorithm.py'],result['patch'],folder/'patched')
    audit(source)
    def functions(text):
        return [ast.dump(n,include_attributes=False) for n in ast.parse(text).body if isinstance(n,ast.FunctionDef)]
    if functions(source)==functions(task['files']['algorithm.py']):
        raise ValueError('no algorithm implementation change')
    return result,source


def public_cases():
    return [{'task':json.loads((ROOT/f'experiments/continuous_mesh/results/dev/task_{i:02d}.json').read_text()),
             'algorithm_seeds':[123,456]} for i in range(2)]


def smoke(run,geometry,backend):
    folder=run/'smoke';folder.mkdir(parents=True,exist_ok=False)
    cases=public_cases();source=initial_source('repair')
    before,rows=evaluate(source,cases,folder/'baseline',run,geometry)
    from run_continuous_meta import feedback,meta_summary
    examples=feedback(cases,rows)
    request={'generator_source':source,'meta_analysis':'Unscored native editing capability trial, not a meta-policy comparison.',
             'proposal_instruction':'Implement one small general search/update improvement while preserving the supplied interface and budgets. Do not only switch METHOD or add comments.',
             'dev_metrics':meta_summary(examples),'dev_examples':examples[:2]}
    task=improvement_task(request,CONTRACT)
    try:
        result,code=candidate(task,folder,backend)
        if code is None: raise RuntimeError('native backend did not complete an edit')
        after,_=evaluate(code,cases,folder/'evaluation',run,geometry)
        passed=after['runtime_errors']==0 and after['queries_min']==24 and after['successes']==4
        gate={'passed':passed,'unscored':True,'backend_status':result['status'],'static_audit':True,'before':before,'after':after}
    except Exception as error:
        gate={'passed':False,'unscored':True,'error':type(error).__name__+': '+str(error)[:1000]}
    save(folder/'gate.json',gate)
    print('unscored native capability gate',gate['passed'],flush=True)


def formal(run,geometry,backend):
    if not json.loads((run/'smoke/gate.json').read_text())['passed']:
        raise RuntimeError('a complete valid unscored candidate is required first')
    directory=run/'formal'
    if directory.exists(): raise RuntimeError('no implicit formal restart or extra candidates')
    prepare(directory,geometry)
    cases=json.loads((directory/'private_tasks.json').read_text())
    policies={name:(ROOT/path).read_text() for name,path in POLICIES.items()}
    started=time.perf_counter();baselines={};jobs=[];calls=[]
    for fixture in FIXTURES:
        source=initial_source(fixture);baseline={'source':source}
        for split in ('dev','selection'):
            baseline[split],baseline[split+'_rows']=evaluate(source,cases[split],directory/f'baseline_{fixture}'/split,run,geometry)
        baselines[fixture]=baseline
        for name in ('M0','M1'):
            slot=len(calls);folder=directory/f'c{slot}'
            request,choice=request_for(source,cases['dev'],baseline['dev_rows'],policies[name],directory/f'policy_{slot}',run)
            calls.append({'slot':slot,'fixture':fixture,'policy':name,'K':1,'status':'reserved','probe':choice['experiment']})
            jobs.append((calls[-1],improvement_task(request,CONTRACT),folder))
    save(directory/'calls.json',calls)  # Four candidates reserved before any formal model call.
    def execute(job):
        record,task,folder=job
        try:
            result,code=candidate(task,folder,backend)
            return result,code,None
        except Exception as error:
            return None,None,type(error).__name__+': '+str(error)[:1000]
    with ThreadPoolExecutor(max_workers=2) as pool:
        generated=list(pool.map(execute,jobs))
    for (record,task,folder),(result,code,error) in zip(jobs,generated):
        if code is None:
            record.update(status='candidate_failed',error=error or (result or {}).get('status','no patch'))
        else:
            (folder/'source.py').write_text(code)
            record['static_audit']=True
            record['dev'],_=evaluate(code,cases['dev'],folder/'dev',run,geometry)
            record['selection'],_=evaluate(code,cases['selection'],folder/'selection',run,geometry)
            record.update(status='evaluated',selected=eligible(baselines[record['fixture']]['selection'],record['selection']))
        save(folder/'record.json',record)
        save(directory/'calls.json',calls)
        print('formal',record['slot'],record['policy'],record['fixture'],record['status'],record.get('selected'),flush=True)
    save(directory/'proposals_complete.json',{'formal_candidates':4,'test_feedback_exposed':False})
    test={fixture:evaluate(baselines[fixture]['source'],cases['test'],directory/f'baseline_{fixture}'/'test',run,geometry) for fixture in FIXTURES}
    scores=[]
    for record,task,folder in jobs:
        fixture=record['fixture'];before,old=test[fixture]
        raw,new=(evaluate((folder/'source.py').read_text(),cases['test'],folder/'test',run,geometry)
                 if record['status']=='evaluated' else (None,None))
        after=raw if record.get('selected') else before
        row={'slot':record['slot'],'policy':record['policy'],'fixture':fixture,'selected':bool(record.get('selected')),
             'before':before,'raw_candidate':raw,'selected_after':after,
             'selected_preference_improvement':bootstrap_difference(old,new if record.get('selected') else old)}
        if raw is not None:
            row.update(raw_preference_improvement=bootstrap_difference(old,new),heldout_multiobjective_guard=eligible(before,raw))
        scores.append(row)
    save(directory/'scores.json',scores)
    save(directory/'finished.json',{'wall_seconds':time.perf_counter()-started,'formal_coding_tasks':4,'K':1,'M2_started':False})
    print('formal K=1 finished',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--geometry',type=Path,required=True)
    parser.add_argument('--guard',type=Path,required=True)
    parser.add_argument('--security',type=Path,required=True)
    parser.add_argument('--editor-security',type=Path,required=True)
    parser.add_argument('--smoke',action='store_true')
    args=parser.parse_args()
    security=json.loads((args.security/'acceptance.json').read_text())
    editor=json.loads((args.editor_security/'acceptance.json').read_text())
    if len(security)!=8 or not all(r['passed'] for r in security) or not editor['native_patch_applied']:
        raise RuntimeError('both existing candidate and native editor acceptance required')
    args.run.mkdir(parents=True,exist_ok=True)
    args.run.chmod(0o700)
    backend=partial(run_codex,runtime=args.run/'runtime',guard=args.guard,parent_git=git(ROOT,'rev-parse','HEAD'))
    if args.smoke:smoke(args.run,args.geometry,backend)
    else:formal(args.run,args.geometry,backend)


if __name__=='__main__':main()
