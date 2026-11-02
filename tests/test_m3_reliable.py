"""Fake-backend tests only: cumulative quotas, narrow retries and test isolation."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import run_m3_reliable as exp


class Clock:
    def __init__(self):self.now=0.;self.waits=[]
    def __call__(self):return self.now
    def sleep(self,seconds):self.now+=seconds;self.waits.append(seconds)


class Backend:
    def __init__(self,clock,steps):self.clock=clock;self.steps=iter(steps);self.budgets=[]
    def __call__(self,task,folder,budget):
        self.budgets.append(dict(budget));step=next(self.steps);folder.mkdir()
        self.clock.now+=step.get('duration',10)
        success=step.get('success',False);usage={'input_tokens':20000,'output_tokens':5000,'total_tokens':25000} if success else None
        requests=[{'index':i,'http_status':200 if success else step.get('http',429),'usage':usage,
                   'token_reservation':step.get('reserve',80000)} for i in range(step.get('requests',3 if success else 1))]
        exp.save(folder/'model_calls.json',{'requests':requests,'input_bytes_total':1000})
        exp.save(folder/'response_0.sse',{'error':{'code':'turn_rate_limit_exceeded','message':'Codex is at capacity, please try again later'}})
        (folder/'events.jsonl').write_text('{"type":"turn.failed"}\n' if not step.get('item') else '{"type":"item.completed"}\n')
        (folder/'candidate.diff').write_text('changed' if step.get('edited') else '')
        (folder/'candidate.py').write_text('different source' if step.get('edited') else '')
        return {'status':'completed' if success else 'failed','patch':'patch' if success else '',
                'usage':dict(usage,total_tokens=usage['total_tokens']*len(requests)) if usage else None,'summary':''}


class ReliableExperimentTests(unittest.TestCase):
    def test_balanced_reference_filter_does_not_consult_algorithm_scores(self):
        with TemporaryDirectory() as tmp:
            calls=[]
            class World:
                def __init__(self,task,geometry):calls.append(task['family'])
                def evaluate(self,reference,final):return {'success':len(calls)!=10,'failures':[{'type':'mesh_passage'}] if len(calls)==10 else []}
                def close(self):pass
            with patch.object(exp,'MeshWorld',World),patch.object(exp,'make_task',side_effect=lambda seed,family,name:({'family':family},{})):
                cases,checks,used=exp.make_cases(Path(tmp),Path('/unused'))
            self.assertEqual(len(calls),73);self.assertEqual(len(checks),72)
            for split in cases:
                self.assertEqual({family:sum(c['task']['family']==family for c in cases[split]) for family in exp.FAMILIES},
                                 {family:6 for family in exp.FAMILIES})
            seeds=[s for group in cases.values() for c in group for s in (c['seed'],*c['algorithm_seeds'])]
            self.assertEqual(len(seeds),len(set(seeds)))
            self.assertEqual(exp.read(Path(tmp)/'reference_sampling.json')['rejected'],1)

    def test_capacity_retries_share_all_budgets_and_preserve_unknown_usage(self):
        with TemporaryDirectory() as tmp:
            clock=Clock();backend=Backend(clock,[{}, {}, {'success':True,'duration':40}])
            result=exp.run_logical({'files':{'algorithm.py':'original'}},Path(tmp)/'coding',backend,sleep=clock.sleep,clock=clock)
            ledger=exp.read(Path(tmp)/'coding/logical.json')
            self.assertEqual(clock.waits,[30,60])
            self.assertEqual([b['model_requests'] for b in backend.budgets],[6,5,4])
            self.assertEqual([b['total_tokens'] for b in backend.budgets],[400000,320000,240000])
            self.assertEqual([b['wall_seconds'] for b in backend.budgets],[600,560,490])
            self.assertEqual([b['input_bytes_total'] for b in backend.budgets],[1048576,1047576,1046576])
            self.assertEqual(ledger['service_requests'],5)
            self.assertEqual(ledger['known_tokens'],75000)
            self.assertEqual(ledger['charged_tokens'],235000)
            self.assertEqual(ledger['unknown_usage_requests'],2)
            self.assertIsNone(ledger['actual_total_tokens']);self.assertIsNone(result['usage'])
            self.assertEqual(result['status'],'completed');self.assertEqual(result['wall_seconds'],150)

    def test_three_capacity_failures_stop_without_a_fourth_draw(self):
        with TemporaryDirectory() as tmp:
            clock=Clock();backend=Backend(clock,[{}, {}, {}])
            result=exp.run_logical({'files':{'algorithm.py':'original'}},Path(tmp)/'coding',backend,sleep=clock.sleep,clock=clock)
            self.assertEqual(result['status'],'failed');self.assertEqual(len(backend.budgets),3)
            self.assertEqual(clock.waits,[30,60])

    def test_no_retry_for_other_http_errors_edits_or_model_output(self):
        for step in ({'http':500},{'edited':True},{'item':True},{'success':True}):
            with self.subTest(step=step), TemporaryDirectory() as tmp:
                clock=Clock();backend=Backend(clock,[step])
                exp.run_logical({'files':{'algorithm.py':'original'}},Path(tmp)/'coding',backend,sleep=clock.sleep,clock=clock)
                self.assertEqual(len(backend.budgets),1);self.assertEqual(clock.waits,[])

    def test_quota_exhaustion_prevents_retry_and_over_time_results_are_rejected(self):
        for steps,attempts,waits in (([{'reserve':200000},{'reserve':200000}],2,[30]),
                                    ([{'duration':580}],1,[]),([{'success':True,'duration':601}],1,[])):
            with self.subTest(steps=steps), TemporaryDirectory() as tmp:
                clock=Clock();backend=Backend(clock,steps)
                result=exp.run_logical({'files':{'algorithm.py':'original'}},Path(tmp)/'coding',backend,sleep=clock.sleep,clock=clock)
                self.assertEqual(result['status'],'budget_exhausted')
                self.assertEqual(len(backend.budgets),attempts);self.assertEqual(clock.waits,waits)

    def test_fixed_interleaving_balances_each_initial_pair_order(self):
        slots=exp.slots()
        self.assertEqual([p for _,_,p in slots],['M2','M3']*4)
        self.assertEqual(len(set(slots)),8)
        for initial in exp.INITIALS:
            self.assertEqual({tuple(p for i,r,p in slots if i==initial and r==repeat) for repeat in (0,1)},
                             {('M2','M3'),('M3','M2')})

    def test_selection_is_fixed_before_test_and_is_not_reselected(self):
        base={'runtime_errors':0,'successes':96,'visibility_mean':.98,'diversity_position_rms_m':.5,
              'diversity_rotation_deg':20.,'valid_pair_count':144,'valid_pair_diversity_m':.5,'preference_error_m':.5}
        with TemporaryDirectory() as tmp:
            run=Path(tmp);exp.save(run/'frozen.json',{'git':'frozen'})
            calls=[dict(initial=i,repeat=r,policy=p,K=1,status='reserved',selected=False) for i,r,p in exp.slots()]
            exp.save(run/'calls.json',calls)
            for c in calls:
                folder=run/c['initial']/c['policy']/f'r{c["repeat"]}';folder.mkdir(parents=True)
                exp.save(folder/'task.json',{})
                b=run/c['initial']/'baseline/selection';b.mkdir(parents=True,exist_ok=True);exp.save(b/'summary.json',base)
            def fake_jobs(root,geometry,jobs):
                split=jobs[0][2]
                if split=='test':
                    self.assertTrue(exp.read(run/'proposals_complete.json')['selection_fixed'])
                    self.assertEqual([c['selected'] for c in exp.read(run/'calls.json')],[c['policy']=='M3' for c in calls])
                results=[]
                for label,source,split,out in jobs:
                    is_base=label.startswith('baseline_test')
                    metrics=dict(base)
                    if not is_base:
                        metrics['preference_error_m']=.3 if split=='test' or source.parent.parent.name=='M3' else .5
                    if split=='test' and not is_base and source.parent.parent.name=='M3':metrics['successes']=95
                    out.mkdir(parents=True);exp.save(out/'summary.json',metrics)
                    results.append({'summary':metrics,'wall_seconds':1.})
                return results
            with patch.object(exp,'git',side_effect=lambda root,*a:'frozen' if a==('rev-parse','HEAD') else ''),\
                 patch.object(exp,'candidate',return_value=({'status':'completed'},'def layout(p): return {}')),\
                 patch.object(exp,'run_jobs',side_effect=fake_jobs):
                exp.execute(run,Path('/unused'),None)
            scores=exp.read(run/'scores.json')
            self.assertTrue(all(s['selected_after']==s['before'] for s in scores if s['policy']=='M2'))
            self.assertTrue(all(s['selected'] and not s['heldout_multiobjective_guard'] and
                                s['selected_after']['successes']==95 for s in scores if s['policy']=='M3'))


if __name__=='__main__':unittest.main()
