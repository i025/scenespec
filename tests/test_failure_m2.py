"""No inference: actual decision qualification and common-information boundaries."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import run_failure_m2 as experiment
from scripts.analyze_m2_failures import best_indices


class FailureM2Tests(unittest.TestCase):
    def decisions(self):
        return [{'experiment':'random' if i==0 else 'dense','analysis':'old'} if i%2==0 else
                {'example_indices':[0,1],'analysis':'old','prompt':'old'} for i in range(8)]

    def test_prompt_only_changes_do_not_qualify(self):
        before=self.decisions();after=[dict(r,analysis='preserve diversity',**({'prompt':'new reminder'} if 'prompt' in r else {})) for r in before]
        self.assertFalse(experiment.decisions_changed(before,after))

    def test_nontext_change_must_remain_data_dependent(self):
        before=self.decisions();constant=[dict(r,**({'experiment':'barriers'} if 'experiment' in r else {})) for r in before]
        self.assertFalse(experiment.decisions_changed(before,constant))
        after=self.decisions();after[2]['experiment']='barriers'
        self.assertTrue(experiment.decisions_changed(before,after))
        after=self.decisions();after[3]['example_indices']=[2,3]
        self.assertTrue(experiment.decisions_changed(before,after))

    def test_failed_policy_qualification_blocks_new_world_tasks(self):
        with TemporaryDirectory() as tmp,patch.object(experiment,'read',return_value={'passed':False}),patch.object(experiment,'conditions') as conditions:
            with self.assertRaises(RuntimeError):experiment.plan(Path(tmp),Path(tmp))
            conditions.assert_not_called()

    def test_policy_edit_cannot_implicitly_restart(self):
        with TemporaryDirectory() as tmp:
            backend=unittest.mock.Mock()
            with self.assertRaises(FileExistsError):experiment.create(Path(tmp),backend)
            backend.assert_not_called()

    def test_saved_trace_ties_keep_original_incumbent(self):
        self.assertEqual(best_indices([{'optimizer_loss':x} for x in (.3,.2,.2-1e-9,.1)]),[0,1,1,3])

    def test_all_three_policies_receive_same_history_and_budgets(self):
        cases=[{'task':{'id':i}} for i in range(8)]
        rows=[{'task_index':i,'repeat':r,'state':{'id':i,'repeat':r},'result':{'success':True,'quality':{'preference_error_m':.3}}} for i in range(8) for r in (0,1)]
        examples=[{'packet':cases[r['task_index']]['task'],'generated':r['state'],'valid':True,'solvable':True,
                   'error':None,'quality':{'preference_error_m':.3,'visibility_mean':1.}} for r in rows]
        full={'success_rate':1.,'preference_error_m':.3,'diversity_position_rms_m':.5,'diversity_rotation_deg':70.,
              'valid_pair_count':8,'valid_pair_diversity_m':.5,'visibility_mean':1.}
        history={'summary':{'multiobjective':{'attempts':4,'dev_accepted':0}},'examples':[{'dev_only':True}]}
        tasks=[]
        for probe in ('random','dense','barriers'):
            def decide(source,packet,*args):
                if packet['phase']=='choose':return {'experiment':probe,'analysis':'fixed fixture'}
                return {'example_indices':[0,1],'analysis':'diagnose','prompt':'improve'}
            with patch.object(experiment,'feedback',return_value=[dict(x) for x in examples]),patch.object(experiment,'distance',return_value={'position_rms_m':.5,'rotation_mean_deg':70.}),patch.object(experiment,'summarize',return_value=full),patch.object(experiment,'read',return_value=history),patch.object(experiment,'policy_decision',side_effect=decide):
                task,_=experiment.request_for('def layout(p): return {}',cases,rows,'policy',Path('/unused'),Path('/unused'))
                tasks.append(task)
        self.assertTrue(all(t['budget']==experiment.BUDGET for t in tasks))
        self.assertTrue(all(t['files']==tasks[0]['files'] for t in tasks))
        self.assertTrue(all(t['feedback']['experience']==history for t in tasks))
        self.assertTrue(all(t['feedback']['metrics']==tasks[0]['feedback']['metrics'] for t in tasks))
        self.assertEqual([t['feedback']['examples'][0]['pair_metrics']['position_rms_m'] for t in tasks],[.5]*3)


if __name__=='__main__':unittest.main()
