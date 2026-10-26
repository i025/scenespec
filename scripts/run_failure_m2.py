"""One M1-directed policy edit, then one preregistered M0/M1/M2 K=1 pilot."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import json
from pathlib import Path
import secrets
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from run_continuous_meta import CONTRACT,POLICIES,feedback,meta_summary,bootstrap_difference
from run_metaimp import policy_decision
from run_native_continuous import candidate
from run_paired_repair import BUDGET
from scene_rsi.continuous.meta_experiment import evaluate,eligible,summarize
from scene_rsi.continuous.tasks import make_task,FAMILIES
from scene_rsi.continuous.geometry import MeshWorld
from analyze_continuous_benchmark import distance
from scene_rsi.world.algorithm_experiment import audit as policy_audit
from scene_rsi.meta.coding import improvement_task,run_codex,git,save

EXPERIMENT=ROOT/'experiments/m2_failure'
META_CONTRACT='''layout(packet) is a pure META-POLICY, not a scene generator.
Edit algorithm.py, which initially contains the parent M1 policy. The user authorizes
one next-generation policy M2. Your research objective is to learn from the supplied
DEV-only failed improvement attempts. Change actual data-dependent diagnosis,
objective prioritization, probe selection and/or example selection logic. Merely
appending a diversity reminder or changing long prompt text is insufficient.
Keep the existing schema: phase choose returns exactly experiment (random/barriers/dense)
and analysis <=3000 chars. Phase propose receives source, summary, examples and returns
exactly analysis <=3000, <=8 unique in-range example_indices, prompt <=8000 chars.
The fixed coding backend later edits a GENERATOR according to the policy output.
The old parent prompt's statements about modifying only a generator are historical
instructions for that later phase; THIS authorized task modifies only the meta-policy.
Do not embed a generator implementation, observed scene coordinates or seed lists.

summary retains tasks, valid/solvable counts/rates, mean preference error and visibility.
summary.multiobjective adds success_rate, preference_error_m, diversity_position_rms_m,
diversity_rotation_deg, valid_pair_count, valid_pair_diversity_m and visibility_mean.
summary.experience contains aggregate old dev attempt measurements. Historical training
valid means a runnable candidate, solvable means passed unchanged DEV multiobjective
acceptance; these are meta-level outcomes, not hidden world/test feedback.
New generator examples retain packet, generated, valid, solvable, error, quality,
shortest_actions, plus condition_index, repeat and pair_metrics with position_rms_m,
rotation_mean_deg, preference_error_m_mean and both_hard_success. Task room dimensions
are inside packet.spec.room. No missing field should cause an exception.

The fixed probe menu uses 8 dev conditions with two seeds: common conditions 0..3,
random adds 4..7 seed 0; barriers adds both seeds of conditions 5 and 7; dense adds
both seeds of conditions 5 and 6. All receive the same measured baseline pool and
fixed cost. You may select only this menu, not create evaluations or budgets.
Frozen acceptance: no hard-success loss, no runtime errors, visibility loss <=.01,
position diversity >=90% for ALL pairs and VALID pairs, rotation >=80%, no lower
valid-pair count, preference improvement >=.01m. No changing these thresholds.
Future M0/M1/M2 share the same dev metadata and historical failure statistics.

Your new logic must cause a nontext decision change (experiment or selected example
membership) on at least one public-schema diagnostic fixture, and remain data dependent
across reliability/quality/diversity/runtime scenarios. Actual capability is assessed
later on new tasks and a new initial generator, not these interface fixtures.
Allowed imports math,collections,heapq,itertools only. Top-level functions, imports,
literal constants, docstrings. No file/network/process access, globals/nonlocal,
private attributes, eval/exec/compile/getattr or extra tools inside policy execution.
The unchanged single-process Apptainer runner limits 256MiB, 5CPU s and bounded JSON.
You cannot modify evaluator, task distribution, model, query/token budgets or outer
controller. No references, private validation/test, future initial generator or
manual generator repair recipe are provided. Edit in place and summarize changes.
'''


def read(path):return json.loads(path.read_text())


def signature(row):
    return ('choose',row['experiment']) if 'experiment' in row else ('propose',tuple(sorted(row['example_indices'])))


def decisions_changed(before,after):
    changed=any(signature(a)!=signature(b) for a,b in zip(before,after))
    adaptive=any(len({signature(r) for r in after if ('experiment' in r)==choice})>1 for choice in (True,False))
    return changed and adaptive


def fixtures():
    result=[]
    for scenario,rate,retention in (('reliability',.4,.95),('diversity',1.,.45),('quality',1.,.98),('runtime',.85,.9)):
        summary={'tasks':12,'valid':12,'valid_rate':1.,'solvable':round(rate*12),'solvable_rate':rate,
          'preference_error_m_mean':.3,'visibility_mean':.99,
          'multiobjective':{'success_rate':rate,'preference_error_m':.3,'diversity_position_rms_m':.4,
           'diversity_rotation_deg':60.,'valid_pair_count':4,'valid_pair_diversity_m':.4,'visibility_mean':.99},
          'experience':{'attempts':4,'dev_accepted':0,'position_retention_mean':retention,'rotation_retention_mean':retention,
                        'runtime_errors':4 if scenario=='runtime' else 0}}
        examples=[]
        for i in range(12):
            examples.append({'condition_index':i//2,'repeat':i%2,'packet':{'family':FAMILIES[(i//2)%4],
                  'spec':{'scene_id':'diagnostic_'+str(i//2),'room':{'width':7.,'depth':6.}}},
                 'valid':True,'solvable':not (scenario=='reliability' and i<6),'error':None,
                 'quality':{'preference_error_m':(.05+.11*(i//2)) if scenario!='diversity' else (.7-.11*(i//2))},
                 'pair_metrics':{'position_rms_m':.1+.13*(i//2),'rotation_mean_deg':10+12*(i//2),
                                  'preference_error_m_mean':.05+.11*(i//2),'both_hard_success':not (scenario=='reliability' and i<6)}})
        result += [{'phase':'choose','summary':summary},{'phase':'propose','source':'def layout(p): return {}',
                                                       'summary':summary,'examples':examples}]
    return result


def create(run,backend):
    run.mkdir(parents=True,exist_ok=False);run.chmod(0o700)
    save(run/'reservation.json',{'policy_edits':1,'pilot_coding_tasks_max':3,'total_coding_tasks_max':4,'K':1,'budget':BUDGET})
    evidence=read(EXPERIMENT/'analysis/training_feedback.json');parent=(ROOT/POLICIES['M1']).read_text()
    policy_decision(parent,{'phase':'choose','summary':evidence['summary']},run/'parent/choose',run)
    decision=policy_decision(parent,{'phase':'propose','source':parent,'summary':evidence['summary'],'examples':evidence['examples']},run/'parent/propose',run)
    task={'objective':'Produce M2 by modifying parent M1 actual diagnostic and research-decision program logic using dev failures.',
          'analysis':decision['analysis'],'strategy':decision['prompt'],
          'feedback':{'metrics':evidence['summary'],'examples':[evidence['examples'][i] for i in decision['example_indices']],
                      'definitions':evidence['definitions']},'allowed_files':['algorithm.py'],'files':{'algorithm.py':parent},
          'contract':META_CONTRACT,'budget':dict(BUDGET)}
    save(run/'self_task.json',task);gate={'passed':False,'policy_edits_reserved':1}
    try:
        result,source=candidate(task,run/'m2',backend)
        if source is None or result.get('usage') is None or result['usage']['total_tokens']>BUDGET['total_tokens']:
            raise ValueError('no complete budget-compliant policy patch')
        policy_audit(source);(run/'m2/raw_policy.py').write_text(source)
        before=[];after=[]
        for i,packet in enumerate(fixtures()):
            before.append(policy_decision(parent,packet,run/f'logic/parent_{i}',run))
            after.append(policy_decision(source,packet,run/f'logic/m2_{i}',run))
        changed=decisions_changed(before,after)
        if not changed:raise ValueError('no adaptive nontext policy-decision change; prompt-only edit is insufficient')
        (run/'m2/frozen_policy.py').write_text(source)
        gate={'passed':True,'policy_edits_reserved':1,'static_audit':True,'public_schema_fixtures':8,
              'nontext_decision_change':True,'adaptive_decisions':True,'parent_decisions':before,'m2_decisions':after,
              'independent_capability_proven':False}
    except Exception as error:gate['error']=type(error).__name__+': '+str(error)[:1000]
    save(run/'m2/gate.json',gate)
    print('M2 code/decision qualification',gate['passed'],flush=True)


def conditions(folder,geometry):
    rng=secrets.SystemRandom();cases={};checks=[]
    for split,count in (('dev',8),('selection',8),('test',12)):
        cases[split]=[]
        for i in range(count):
            seed=rng.getrandbits(64);task,reference=make_task(seed,FAMILIES[i%4],f'm2_{split}_{i:02d}')
            world=MeshWorld(task,geometry)
            try:report=world.evaluate(reference,final=True)
            finally:world.close()
            checks.append({'split':split,'success':report['success']})
            cases[split].append({'seed':seed,'task':task,'reference':reference,'algorithm_seeds':[rng.getrandbits(32) for _ in range(2)]})
    save(folder/'private_tasks.json',cases);save(folder/'private_reference_checks.json',checks)
    if not all(r['success'] for r in checks):raise RuntimeError('new reference validation failed; no resampling')
    save(folder/'reference_acceptance.json',{'conditions':28,'legal_references':28,'new_dev':8,'new_selection':8,'new_test':12})
    return cases


def request_for(source,cases,rows,policy,folder,run):
    public=feedback(cases,rows);history=read(EXPERIMENT/'analysis/training_feedback.json')
    for i,case in enumerate(cases):
        pair=[r for r in rows if r['task_index']==i]
        delta=distance(case['task'],*[r['state'] for r in pair])
        for r in pair:
            j=rows.index(r);public[j].update(condition_index=i,repeat=r['repeat'],pair_metrics={
                'position_rms_m':delta['position_rms_m'],'rotation_mean_deg':delta['rotation_mean_deg'],
                'preference_error_m_mean':sum(x['result']['quality']['preference_error_m'] for x in pair)/2,
                'both_hard_success':all(x['result']['success'] for x in pair)})
    full=summarize(cases,rows)
    def summary(examples):
        return dict(meta_summary(examples),multiobjective={k:full[k] for k in ('success_rate','preference_error_m',
              'diversity_position_rms_m','diversity_rotation_deg','valid_pair_count','valid_pair_diversity_m','visibility_mean')},
              experience=history['summary']['multiobjective'])
    common=[r for r in public if r['condition_index']<4]
    choice=policy_decision(policy,{'phase':'choose','summary':summary(common)},folder/'choose',run)
    pool=([r for r in public if r['condition_index']>=4 and r['repeat']==0] if choice['experiment']=='random' else
          [r for r in public if r['condition_index'] in ((5,6) if choice['experiment']=='dense' else (5,7))])
    examples=common+pool;assert len(examples)==12
    decision=policy_decision(policy,{'phase':'propose','source':source,'summary':summary(examples),'examples':examples},folder/'propose',run)
    task=improvement_task({'generator_source':source,'dev_metrics':summary(examples),'dev_examples':[examples[i] for i in decision['example_indices']],
                           'meta_analysis':decision['analysis'],'proposal_instruction':decision['prompt']},CONTRACT)
    task['budget']=dict(BUDGET);task['feedback']['experience']=history
    return task,choice


def plan(run,geometry):
    if not read(run/'m2/gate.json')['passed']:raise RuntimeError('no qualified M2; stop without pilot calls')
    folder=run/'pilot';folder.mkdir(exist_ok=False)
    cases=conditions(folder,geometry);source=(EXPERIMENT/'initial.py').read_text();baseline={};started=time.perf_counter()
    for split in ('dev','selection'):
        baseline[split],rows=evaluate(source,cases[split],folder/'baseline_group'/split,run,geometry)
        if split=='dev':dev_rows=rows
    dev=baseline['dev']
    feasible=dev['runtime_errors']==0 and dev['success_rate']>=.5 and dev['preference_error_m']>=.02 and dev['diversity_position_rms_m']>.05
    save(folder/'preflight.json',{'passed':feasible,'initial_generator':'group_restart','baseline_dev':dev,'budget':BUDGET,
              'new_policy_frozen_before_conditions':True,'arms':['M0','M1','M2'],'K':1})
    if not feasible:raise RuntimeError('initial dev feasibility gate failed; no tuning or live pilot')
    calls=[]
    for slot,name in enumerate(('M0','M1','M2')):
        policy=(run/'m2/frozen_policy.py').read_text() if name=='M2' else (ROOT/POLICIES[name]).read_text()
        dest=folder/f'c{slot}';dest.mkdir()
        record={'slot':slot,'policy':name,'fixture':'group_restart','K':1,'status':'reserved'}
        try:
            task,choice=request_for(source,cases['dev'],dev_rows,policy,folder/f'policy_{slot}',run)
            save(dest/'task.json',task);record['probe']=choice['experiment']
        except Exception as error:record['policy_error']=type(error).__name__+': '+str(error)[:1000]
        calls.append(record)
    save(folder/'calls.json',calls);save(folder/'prepare_cost.json',{'wall_seconds':time.perf_counter()-started})
    print('three K=1 pilot tasks reserved on a new generator and 28 new conditions',flush=True)


def pilot(run,geometry,backend):
    folder=run/'pilot'
    if (folder/'started.json').exists():raise RuntimeError('no pilot restart or extra proposals')
    calls=read(folder/'calls.json');assert [r['policy'] for r in calls]==['M0','M1','M2'] and all(r['status']=='reserved' for r in calls)
    save(folder/'started.json',{'parent_git':git(ROOT,'rev-parse','HEAD'),'coding_tasks':3,'K':1});started=time.perf_counter()
    cases=read(folder/'private_tasks.json')
    def propose(record):
        dest=folder/f'c{record["slot"]}'
        if record.get('policy_error'):return None,record['policy_error']
        try:
            result,source=candidate(read(dest/'task.json'),dest,backend)
            if source is not None and (result.get('usage') is None or result['usage']['total_tokens']>BUDGET['total_tokens']):raise ValueError('missing/exceeded token usage')
            return source,None if source is not None else result['status']
        except Exception as error:return None,type(error).__name__+': '+str(error)[:1000]
    with ThreadPoolExecutor(max_workers=2) as pool:generated=list(pool.map(propose,calls))
    for record,(source,error) in zip(calls,generated):
        dest=folder/f'c{record["slot"]}'
        if source is None:record.update(status='candidate_failed',error=error,selected=False)
        else:
            (dest/'source.py').write_text(source);record['static_audit']=True
            for split in ('dev','selection'):record[split],_=evaluate(source,cases[split],dest/split,run,geometry)
            record.update(status='evaluated',selected=eligible(read(folder/'baseline_group/selection/summary.json'),record['selection']))
        save(dest/'record.json',record);save(folder/'calls.json',calls)
        print('pilot',record['policy'],record['status'],'selected',record['selected'],flush=True)
    save(folder/'proposals_complete.json',{'coding_tasks':3,'all_proposals_closed':True,'test_feedback_exposed':False})
    before,old=evaluate((EXPERIMENT/'initial.py').read_text(),cases['test'],folder/'baseline_group/test',run,geometry);scores=[]
    for record in calls:
        dest=folder/f'c{record["slot"]}'
        raw,rows=(evaluate((dest/'source.py').read_text(),cases['test'],dest/'test',run,geometry) if record['status']=='evaluated' else (None,None))
        score={'policy':record['policy'],'slot':record['slot'],'selected':record['selected'],'before':before,'raw_candidate':raw,
               'selected_after':raw if record['selected'] else before,'heldout_multiobjective_guard':eligible(before,raw) if raw else False,
               'selected_preference_improvement':bootstrap_difference(old,rows if record['selected'] else old)}
        if rows:score['raw_preference_improvement']=bootstrap_difference(old,rows)
        scores.append(score)
    save(folder/'scores.json',scores);save(folder/'finished.json',{'coding_tasks':3,'K':1,'wall_seconds':time.perf_counter()-started,'extra_trials':0})
    print('one pilot completed; no continuation',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('run','geometry','guard','security','editor-security'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--phase',choices=('create','prepare','pilot'),required=True);args=parser.parse_args()
    safety=read(args.security/'acceptance.json');editor=read(args.editor_security/'acceptance.json')
    if len(safety)!=8 or not all(r['passed'] for r in safety) or not editor['native_patch_applied']:raise RuntimeError('isolation acceptance required')
    backend=partial(run_codex,runtime=args.run.parent/'runtime',guard=args.guard,parent_git=git(ROOT,'rev-parse','HEAD'))
    if args.phase=='create':create(args.run,backend)
    elif args.phase=='prepare':plan(args.run,args.geometry)
    else:pilot(args.run,args.geometry,backend)


if __name__=='__main__':main()
