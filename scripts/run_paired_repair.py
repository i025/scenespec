"""Two independent frozen M0/M1 pairs from Repair; exactly four native tasks."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from run_continuous_meta import CONTRACT,POLICIES,prepare,request_for,bootstrap_difference,load_rows
from run_native_continuous import candidate
from scene_rsi.continuous.meta_experiment import initial_source,evaluate,eligible
from scene_rsi.meta.coding import BUDGET,improvement_task,run_codex,git,save

BUDGET=dict(BUDGET,total_tokens=400000)


def read(path):return json.loads(path.read_text())


def plan(run,geometry):
    run.mkdir(parents=True,exist_ok=False);run.chmod(0o700)
    source=initial_source('repair');jobs=[];dev=[]
    for pair in range(2):
        folder=run/f'pair{pair}';prepare(folder,geometry)
        cases=read(folder/'private_tasks.json');baseline={}
        for split in ('dev','selection'):
            baseline[split],rows=evaluate(source,cases[split],folder/'baseline_repair'/split,run,geometry)
            if split=='dev':dev_rows=rows
        dev.append({'pair':pair,'baseline_dev':baseline['dev']})
        for slot,name in enumerate(('M0','M1')):
            request,choice=request_for(source,cases['dev'],dev_rows,(ROOT/POLICIES[name]).read_text(),folder/f'policy_{slot}',run)
            task=improvement_task(request,CONTRACT);task['budget']=dict(BUDGET)
            destination=folder/f'c{slot}';destination.mkdir()
            save(destination/'task.json',task)
            jobs.append({'pair':pair,'slot':slot,'policy':name,'fixture':'repair','K':1,'probe':choice['experiment'],'status':'reserved'})
    save(run/'calls.json',jobs)
    save(run/'preflight.json',{'base':'0e30ebb','independent_pairs':2,'coding_tasks_reserved':4,'K':1,
         'budget':BUDGET,'model':'gpt-6.1-sol','selection_rule':'unchanged continuous.meta_experiment.eligible on private selection only',
         'baseline_dev':dev,'test_feedback_to_proposer':False,'previous_smoke_reused':True})
    print('four K=1 tasks reserved; no real model calls during preparation',flush=True)


def execute(run,geometry,backend):
    if (run/'started.json').exists():raise RuntimeError('no restart or extra proposals')
    calls=read(run/'calls.json')
    if [(r['pair'],r['policy'],r['K'],r['status']) for r in calls]!=[(p,m,1,'reserved') for p in range(2) for m in ('M0','M1')]:
        raise ValueError('exactly two frozen policy pairs required')
    started=time.perf_counter();save(run/'started.json',{'parent_git':git(ROOT,'rev-parse','HEAD'),'coding_tasks':4,'K':1})
    def propose(record):
        folder=run/f'pair{record["pair"]}'/f'c{record["slot"]}'
        try:
            result,source=candidate(read(folder/'task.json'),folder,backend)
            if source is not None and (result.get('usage') is None or result['usage']['total_tokens']>BUDGET['total_tokens']):
                raise ValueError('missing usage or total-token budget exceeded')
            return result,source,None
        except Exception as error:return None,None,type(error).__name__+': '+str(error)[:1000]
    with ThreadPoolExecutor(max_workers=2) as pool:generated=list(pool.map(propose,calls))
    for record,(result,source,error) in zip(calls,generated):
        pair=run/f'pair{record["pair"]}';folder=pair/f'c{record["slot"]}';cases=read(pair/'private_tasks.json')
        if source is None:record.update(status='candidate_failed',error=error or (result or {}).get('status','no patch'),selected=False)
        else:
            (folder/'source.py').write_text(source);record['static_audit']=True
            for split in ('dev','selection'):
                record[split],_=evaluate(source,cases[split],folder/split,run,geometry)
            record.update(status='evaluated',selected=eligible(read(pair/'baseline_repair/selection/summary.json'),record['selection']))
        save(folder/'record.json',record);save(run/'calls.json',calls)
        print('candidate',record['pair'],record['policy'],record['status'],'selected',record['selected'],flush=True)
    save(run/'proposals_complete.json',{'coding_tasks':4,'K':1,'all_proposals_closed':True,'test_feedback_exposed':False})
    for pair in range(2):
        folder=run/f'pair{pair}';cases=read(folder/'private_tasks.json')
        before,old=evaluate(initial_source('repair'),cases['test'],folder/'baseline_repair/test',run,geometry)
        scores=[]
        for record in [r for r in calls if r['pair']==pair]:
            candidate_dir=folder/f'c{record["slot"]}'
            raw,rows=(evaluate((candidate_dir/'source.py').read_text(),cases['test'],candidate_dir/'test',run,geometry)
                      if record['status']=='evaluated' else (None,None))
            selected_rows=rows if record['selected'] else old
            score={'pair':pair,'policy':record['policy'],'slot':record['slot'],'selected':record['selected'],
                   'before':before,'raw_candidate':raw,'selected_after':raw if record['selected'] else before,
                   'selected_preference_improvement':bootstrap_difference(old,selected_rows),
                   'heldout_multiobjective_guard':eligible(before,raw) if raw is not None else False}
            if rows is not None:score['raw_preference_improvement']=bootstrap_difference(old,rows)
            scores.append(score)
        save(folder/'scores.json',scores)
        selected=[load_rows(folder/(f'c{s["slot"]}' if s['selected'] else 'baseline_repair')/'test',cases['test']) for s in scores]
        save(folder/'comparison.json',{'pair':pair,'selected_M0_minus_M1_preference_error':bootstrap_difference(*selected),
                                     'independent_model_samples_per_policy':1})
        save(folder/'finished.json',{'K':1,'coding_tasks':2,'M2_started':False})
        print('pair',pair,'new private test complete',flush=True)
    save(run/'finished.json',{'wall_seconds':time.perf_counter()-started,'independent_pairs':2,'coding_tasks':4,'K':1,'M2_started':False})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True);parser.add_argument('--geometry',type=Path,required=True)
    parser.add_argument('--guard',type=Path,required=True);parser.add_argument('--security',type=Path,required=True)
    parser.add_argument('--editor-security',type=Path,required=True);parser.add_argument('--prepare',action='store_true')
    args=parser.parse_args();security=read(args.security/'acceptance.json');editor=read(args.editor_security/'acceptance.json')
    if len(security)!=8 or not all(r['passed'] for r in security) or not editor['native_patch_applied']:
        raise RuntimeError('both isolation acceptances required; no host fallback')
    if not read(ROOT/'experiments/native_codex/results/smoke/gate.json')['passed']:
        raise RuntimeError('the existing real native capability test must have passed')
    if args.prepare:plan(args.run,args.geometry)
    else:
        backend=partial(run_codex,runtime=args.run/'runtime',guard=args.guard,parent_git=git(ROOT,'rev-parse','HEAD'))
        execute(args.run,args.geometry,backend)


if __name__=='__main__':main()
