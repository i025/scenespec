"""Publish actual policy revision and pilot aggregates; never publish private tasks."""
import argparse
import json
from pathlib import Path
import re
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from summarize_native_continuous import copy_coding
from analyze_continuous_benchmark import task_packets
from run_continuous_meta import bootstrap_difference,load_rows
from scene_rsi.meta.coding import save


def read(path):return json.loads(path.read_text())


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();run,out=args.run,args.out;gate=read(run/'m2/gate.json')
    if gate['passed'] and not (run/'pilot/finished.json').exists():raise RuntimeError('pilot not complete')
    out.mkdir(parents=True,exist_ok=False);costs=[]
    def cost(folder,scope,policy):
        b=read(folder/'backend_result.json') if (folder/'backend_result.json').exists() else {}
        q=read(folder/'model_calls.json')['requests'] if (folder/'model_calls.json').exists() else []
        costs.append({'scope':scope,'policy':policy,'backend_status':b.get('status'),'model':b.get('model'),
          'model_requests':len(q),'usage':b.get('usage'),'missing_usage_requests':sum(r.get('usage') is None for r in q),
          'coding_wall_seconds':b.get('wall_seconds'),'resources':b.get('resources')})
    for filename in ('reservation.json','self_task.json'):shutil.copyfile(run/filename,out/filename)
    shutil.copytree(run/'parent',out/'parent')
    if (run/'logic').exists():shutil.copytree(run/'logic',out/'logic')
    creation=out/'creation';creation.mkdir();copy_coding(run/'m2/coding',creation/'coding')
    for filename in ('gate.json','raw_policy.py','frozen_policy.py'):
        if (run/'m2'/filename).exists():shutil.copyfile(run/'m2'/filename,creation/filename)
    cost(run/'m2/coding','policy_creation','M2')
    private=[];seeds=[];allowed=[];summary={'policy_edit_qualification':gate['passed'],'capability_improvement_proven':False,'extra_trials':0}
    if gate['passed']:
        folder=run/'pilot';pilot=out/'pilot';pilot.mkdir();cases=read(folder/'private_tasks.json')
        private=[c['task'] for s in ('selection','test') for c in cases[s]]
        seeds=[v for s in ('selection','test') for c in cases[s] for v in (c['seed'],*c['algorithm_seeds'])]
        allowed=[c['task'] for c in cases['dev']]
        save(pilot/'dev_cases.json',[{'task':c['task'],'algorithm_seeds':c['algorithm_seeds']} for c in cases['dev']])
        for filename in ('calls.json','preflight.json','reference_acceptance.json','prepare_cost.json','started.json','proposals_complete.json','scores.json','finished.json'):
            shutil.copyfile(folder/filename,pilot/filename)
        calls=read(folder/'calls.json');scores=read(folder/'scores.json')
        for name in ('baseline_group','c0','c1','c2'):
            source,dest=folder/name,pilot/name;dest.mkdir()
            if (source/'dev').exists():shutil.copytree(source/'dev',dest/'dev')
            for split in ('dev','selection','test'):
                if (source/split/'summary.json').exists():shutil.copyfile(source/split/'summary.json',dest/(split+'_summary.json'))
            if name=='baseline_group':continue
            slot=int(name[1:]);record=calls[slot]
            if (source/'coding').exists():copy_coding(source/'coding',dest/'coding')
            for filename in ('source.py','record.json'):
                if (source/filename).exists():shutil.copyfile(source/filename,dest/filename)
            if (folder/f'policy_{slot}').exists():shutil.copytree(folder/f'policy_{slot}',dest/'policy')
            cost(source/'coding','generator_optimization',record['policy'])
        selected=[load_rows(folder/(f'c{s["slot"]}' if s['selected'] else 'baseline_group')/'test',cases['test']) for s in scores]
        contrasts={name:bootstrap_difference(selected[i],selected[2]) for i,name in enumerate(('M0_minus_M2','M1_minus_M2'))}
        save(out/'contrasts.json',{'selected_preference_error':contrasts,'model_samples_per_arm':1,
              'initial_generators':1,'task_intervals_are_not_model_sampling_uncertainty':True})
        summary['pilot']={r['policy']:{'policy_failed':bool(r.get('policy_error')),'static_pass':bool(r.get('static_audit')),
            'candidate_evaluated':r['status']=='evaluated','selected':s['selected'],'raw_heldout_guard':s['heldout_multiobjective_guard'],
            'selected_and_heldout_guard':s['selected'] and s['heldout_multiobjective_guard'],
            'raw_test':s['raw_candidate'],'selected_test':s['selected_after']} for r,s in zip(calls,scores)}
    save(out/'summary.json',summary)
    save(out/'costs.json',{'tasks':costs,'monetary_cost':None,'note':'M2 creation separate; coding walls may overlap; process RSS is not aggregate'})
    files=[p for p in out.rglob('*') if p.is_file()]
    for p in files:
        text=p.read_text()
        assert not any(str(s) in text for s in seeds),str(p)
        assert not re.search(r'eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.|sk-(?:proj-)?[A-Za-z0-9_-]{35,}',text),str(p)
        if p.suffix=='.json':
            packets=list(task_packets(json.loads(text)))
            assert not any(a==b for a in packets for b in private),str(p)
            assert all(t in allowed for t in packets),str(p)
    save(out/'privacy.json',{'private_conditions':len(private),'public_files_checked':len(files),'hidden_packet_matches':0,
            'hidden_seed_matches_including_jsonl':0,'credential_pattern_matches':0,'references_published':False,
            'method':'direct structured and text comparison, no hashes'})
    print(json.dumps({'qualified_policy':gate['passed'],'costs':costs}),flush=True)


if __name__=='__main__':main()
